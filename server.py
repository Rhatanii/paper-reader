"""Paper Reader — 논문 읽기 로컬 웹앱 백엔드.

로그인된 Claude 구독 계정(claude CLI)을 사용하므로 별도 API 비용이 들지 않는다.
실행:  ./run.sh   (기본 http://127.0.0.1:8123)
"""
import asyncio
import json
import re
import shutil
import time
import urllib.request
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

import claude_client
import notion_sync
import paths
import prompts
from pdf_utils import (
    ensure_citations,
    ensure_lines,
    ensure_page_image,
    ensure_ref_crop,
    ensure_sentences,
    find_citing_sentences,
    find_mentions,
    parse_pdf,
)

# 번역/키 추출용 동시 claude 프로세스 상한 (메모리 보호)
_claude_sem = asyncio.Semaphore(4)
# 번역·핵심문장 추출은 사고(thinking) 불필요 — 지연시간 단축
_FAST_ENV = {"MAX_THINKING_TOKENS": "0"}

DATA = paths.DATA
STATIC = paths.resource_path("static")

app = FastAPI(title="Paper Reader")

MODELS = [
    {"id": "sonnet", "name": "Sonnet (기본 · 빠름)"},
    {"id": "opus", "name": "Opus"},
    {"id": "claude-fable-5", "name": "Fable 5 (최고 성능)"},
    {"id": "haiku", "name": "Haiku (최속)"},
]
MODEL_IDS = {m["id"] for m in MODELS}
DEFAULT_MODEL = "sonnet"

FULLTEXT_LIMIT = 400_000  # 요약 프롬프트에 넣는 본문 최대 길이(문자)

# 논문별 채팅 직렬화 락
_chat_locks: dict[str, asyncio.Lock] = {}


def _paper_dir(pid: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{8}", pid):
        raise HTTPException(400, "잘못된 paper id")
    d = DATA / pid
    if not d.exists():
        raise HTTPException(404, "논문을 찾을 수 없습니다")
    return d


def _meta(pid: str) -> dict:
    return json.loads((_paper_dir(pid) / "meta.json").read_text())


def _model_or_default(model: str | None) -> str:
    if not model:
        return DEFAULT_MODEL
    if model not in MODEL_IDS:
        raise HTTPException(400, f"지원하지 않는 모델: {model}")
    return model


def _ndjson(gen):
    async def wrap():
        async for ev in gen:
            yield json.dumps(ev, ensure_ascii=False) + "\n"

    return StreamingResponse(
        wrap(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------------------------------------------------------------- 논문 관리

@app.get("/api/models")
def list_models():
    return {"models": MODELS, "default": DEFAULT_MODEL}


@app.get("/api/papers")
def list_papers():
    papers = []
    for d in sorted(DATA.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        mf = d / "meta.json"
        if mf.exists():
            m = json.loads(mf.read_text())
            papers.append(
                {
                    "id": d.name,
                    "title": m["title"],
                    "npages": m["npages"],
                    "created": m.get("created"),
                }
            )
    return {"papers": papers}


def _register_pdf(tmp_pdf: Path, filename: str) -> dict:
    pid = uuid.uuid4().hex[:8]
    pdir = DATA / pid
    pdir.mkdir(parents=True)
    dest = pdir / "paper.pdf"
    shutil.move(str(tmp_pdf), dest)
    try:
        meta = parse_pdf(dest, pdir)
    except Exception as e:
        shutil.rmtree(pdir, ignore_errors=True)
        raise HTTPException(400, f"PDF 파싱 실패: {e}")
    meta.update({"id": pid, "filename": filename, "created": int(time.time())})
    (pdir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False))
    (pdir / "translations").mkdir(exist_ok=True)
    (pdir / "figures").mkdir(exist_ok=True)
    try:
        ensure_citations(pdir)
    except Exception:
        pass  # 인용 파싱 실패해도 논문 등록은 유지
    return meta


@app.post("/api/papers")
async def upload_paper(file: UploadFile):
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "PDF 파일만 업로드할 수 있습니다")
    tmp = DATA / f".upload-{uuid.uuid4().hex}.pdf"
    with tmp.open("wb") as f:
        while chunk := await file.read(1 << 20):
            f.write(chunk)
    return _register_pdf(tmp, file.filename)


@app.post("/api/papers/from-url")
async def upload_from_url(body: dict):
    url = (body.get("url") or "").strip()
    if not url.startswith(("http://", "https://")):
        raise HTTPException(400, "올바른 URL이 아닙니다")
    # arXiv abs 링크는 pdf 링크로 변환
    m = re.match(r"https?://arxiv\.org/abs/([\w.\-/]+)", url)
    if m:
        url = f"https://arxiv.org/pdf/{m.group(1)}"

    def fetch():
        req = urllib.request.Request(url, headers={"User-Agent": "paper-reader/1.0"})
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.read()

    try:
        blob = await asyncio.to_thread(fetch)
    except Exception as e:
        raise HTTPException(400, f"다운로드 실패: {e}")
    if not blob.startswith(b"%PDF"):
        raise HTTPException(400, "PDF가 아닌 응답입니다 (URL을 확인하세요)")
    tmp = DATA / f".upload-{uuid.uuid4().hex}.pdf"
    tmp.write_bytes(blob)
    name = url.rsplit("/", 1)[-1] or "paper.pdf"
    return _register_pdf(tmp, name)


@app.get("/api/papers/{pid}")
def paper_detail(pid: str):
    pdir = _paper_dir(pid)
    meta = _meta(pid)
    figures = json.loads((pdir / "figures.json").read_text())
    translated = sorted(
        int(p.stem) for p in (pdir / "translations").glob("*.json")
    )
    explained = [p.stem for p in (pdir / "figures").glob("*.md")]
    return {
        **meta,
        "figures": figures,
        "translated_pages": translated,
        "explained_figures": explained,
        "has_summary": (pdir / "summary.md").exists(),
    }


@app.delete("/api/papers/{pid}")
def delete_paper(pid: str):
    shutil.rmtree(_paper_dir(pid))
    return {"ok": True}


@app.get("/api/papers/{pid}/page/{n}.png")
def page_image(pid: str, n: int):
    pdir = _paper_dir(pid)
    try:
        png = ensure_page_image(pdir, n)
    except ValueError as e:
        raise HTTPException(404, str(e))
    return FileResponse(png, media_type="image/png")


@app.get("/api/papers/{pid}/page/{n}/sentences")
def page_sentences(pid: str, n: int):
    try:
        return JSONResponse(ensure_sentences(_paper_dir(pid), n))
    except ValueError as e:
        raise HTTPException(404, str(e))


@app.get("/api/papers/{pid}/page/{n}/lines")
def page_lines(pid: str, n: int):
    """텍스트 선택 레이어용 라인 데이터."""
    try:
        return JSONResponse(ensure_lines(_paper_dir(pid), n))
    except ValueError as e:
        raise HTTPException(404, str(e))


# ---------------------------------------------------------------- 인용/참고문헌

@app.get("/api/papers/{pid}/citations")
def get_citations(pid: str):
    """본문 인용 좌표 + 참고문헌 항목. 기존 논문도 첫 조회 때 자동 생성."""
    return ensure_citations(_paper_dir(pid))


@app.get("/api/papers/{pid}/refs/{n}.png")
def ref_crop(pid: str, n: int):
    pdir = _paper_dir(pid)
    refs = ensure_citations(pdir)["references"]
    entry = next((e for e in refs["entries"] if e["n"] == n), None)
    if not entry:
        raise HTTPException(404, "참고문헌 항목이 없습니다")
    return FileResponse(ensure_ref_crop(pdir, entry), media_type="image/png")


@app.get("/api/papers/{pid}/refs/{n}/summary")
def get_ref_summary(pid: str, n: int):
    f = _paper_dir(pid) / "refs" / f"summary-{n}.md"
    return {"summary": f.read_text() if f.exists() else None}


@app.post("/api/papers/{pid}/refs/{n}/summary")
async def make_ref_summary(pid: str, n: int, body: dict | None = None):
    pdir = _paper_dir(pid)
    model = _model_or_default((body or {}).get("model"))
    refs = ensure_citations(pdir)["references"]
    entry = next((e for e in refs["entries"] if e["n"] == n), None)
    if not entry:
        raise HTTPException(404, "참고문헌 항목이 없습니다")

    cache = pdir / "refs" / f"summary-{n}.md"
    if cache.exists() and not (body or {}).get("force"):
        async def cached():
            yield {"type": "delta", "text": cache.read_text()}
            yield {"type": "done"}
        return _ndjson(cached())

    fulltext = (pdir / "fulltext.txt").read_text()
    citing = find_citing_sentences(fulltext, entry)
    prompt = prompts.REF_SUMMARY_PROMPT.format(
        n=n,
        entry=entry["text"][:700],
        citing="\n".join(f"- {c}" for c in citing) or "- (본문 인용 문장을 찾지 못함)",
    )

    async def gen():
        cache.parent.mkdir(exist_ok=True)
        async for ev in claude_client.stream(
            prompt, model, cwd=pdir,
            allowed_tools=("WebSearch", "WebFetch"), max_turns=8, timeout=420,
        ):
            if ev["type"] == "done":
                cache.write_text(ev["text"])
                yield {"type": "done"}
            else:
                yield ev

    return _ndjson(gen())


# ---------------------------------------------------------------- 밑줄(하이라이트)

def _highlights_file(pdir: Path) -> Path:
    return pdir / "highlights.json"


def _load_highlights(pdir: Path) -> list:
    f = _highlights_file(pdir)
    return json.loads(f.read_text()) if f.exists() else []


def _save_highlights(pdir: Path, hs: list):
    _highlights_file(pdir).write_text(json.dumps(hs, ensure_ascii=False))


@app.get("/api/papers/{pid}/highlights")
def list_highlights(pid: str):
    return {"highlights": _load_highlights(_paper_dir(pid))}


@app.post("/api/papers/{pid}/highlights")
async def upsert_highlight(pid: str, body: dict):
    """밑줄 추가 또는 메모 수정 (upsert). Notion 로그 이벤트 발행."""
    pdir = _paper_dir(pid)
    try:
        page, sent_i = int(body["page"]), int(body["sent_i"])
    except (KeyError, TypeError, ValueError):
        raise HTTPException(400, "page/sent_i가 필요합니다")
    note = (body.get("note") or "").strip()

    try:
        sent = next(
            (s for s in ensure_sentences(pdir, page) if s["i"] == sent_i), None
        )
    except ValueError:
        raise HTTPException(404, "페이지가 없습니다")
    if not sent:
        raise HTTPException(404, "문장을 찾을 수 없습니다")

    hid = f"{page}-{sent_i}"
    hs = _load_highlights(pdir)
    existing = next((h for h in hs if h["id"] == hid), None)
    if existing:
        changed = existing.get("note", "") != note
        existing["note"] = note
        if changed and note:
            notion_sync.log_event(
                pid, {"type": "highlight_note", "page": page, "note": note}
            )
    else:
        h = {
            "id": hid,
            "page": page,
            "sent_i": sent_i,
            "text": sent["text"],
            "note": note,
            "ts": int(time.time()),
        }
        hs.append(h)
        notion_sync.log_event(
            pid,
            {"type": "highlight", "page": page, "text": sent["text"], "note": note},
        )
    _save_highlights(pdir, hs)
    return {"highlights": hs}


@app.delete("/api/papers/{pid}/highlights/{hid}")
def delete_highlight(pid: str, hid: str):
    pdir = _paper_dir(pid)
    hs = [h for h in _load_highlights(pdir) if h["id"] != hid]
    _save_highlights(pdir, hs)
    return {"highlights": hs}


# ---------------------------------------------------------------- Notion 로그

@app.get("/api/notion")
def notion_status(pid: str | None = None):
    s = notion_sync.status()
    if pid:
        s["paper_page_url"] = notion_sync.paper_page_url(pid)
    return s


@app.post("/api/notion")
async def notion_settings(body: dict):
    cfg = notion_sync.load_config()
    if "enabled" in body:
        cfg["enabled"] = bool(body["enabled"])
    if body.get("model") in MODEL_IDS:
        cfg["model"] = body["model"]
    notion_sync.save_config(cfg)
    return notion_sync.status()


@app.post("/api/notion/flush")
async def notion_flush():
    results = await notion_sync.flush_all()
    return {"results": results, **notion_sync.status()}


# ---------------------------------------------------------------- 요약

@app.get("/api/papers/{pid}/summary")
def get_summary(pid: str):
    f = _paper_dir(pid) / "summary.md"
    return {"summary": f.read_text() if f.exists() else None}


@app.post("/api/papers/{pid}/summary")
async def make_summary(pid: str, body: dict | None = None):
    pdir = _paper_dir(pid)
    model = _model_or_default((body or {}).get("model"))
    fulltext = (pdir / "fulltext.txt").read_text()[:FULLTEXT_LIMIT]
    prompt = prompts.SUMMARY_PROMPT.format(fulltext=fulltext)

    async def gen():
        full = None
        async for ev in claude_client.stream(
            prompt, model, cwd=pdir, system=prompts.SUMMARY_SYSTEM,
            allowed_tools=None, max_turns=4, timeout=900,
        ):
            if ev["type"] == "done":
                full = ev["text"]
                (pdir / "summary.md").write_text(full)
                notion_sync.log_event(pid, {"type": "summary", "text": full, "model": model})
                yield {"type": "done"}
            else:
                yield ev

    return _ndjson(gen())


# ---------------------------------------------------------------- 번역

@app.get("/api/papers/{pid}/pages/{n}/translation")
def get_translation(pid: str, n: int):
    f = _paper_dir(pid) / "translations" / f"{n}.json"
    if not f.exists():
        return {"translation": None}
    return {"translation": json.loads(f.read_text())}


@app.post("/api/papers/{pid}/pages/{n}/translate")
async def translate_page(pid: str, n: int, body: dict | None = None):
    pdir = _paper_dir(pid)
    body = body or {}
    model = _model_or_default(body.get("model"))
    cache = pdir / "translations" / f"{n}.json"
    if cache.exists() and not body.get("force"):
        return {"translation": json.loads(cache.read_text())}

    try:
        sentences = ensure_sentences(pdir, n)
    except ValueError as e:
        raise HTTPException(404, str(e))
    if not sentences:
        cache.write_text("[]")
        return {"translation": []}

    src = [
        {"i": s["i"], "b": s.get("b"), "text": s["text"]} for s in sentences
    ]

    # 문장을 청크로 나눠 병렬 번역 + 핵심 문장 추출을 동시에 (벽시계 시간 단축)
    CHUNK = 10
    chunks = [src[k:k + CHUNK] for k in range(0, len(src), CHUNK)]

    async def tr_chunk(chunk):
        prompt = prompts.TRANSLATE_PROMPT.format(
            n=len(chunk), sentences_json=json.dumps(chunk, ensure_ascii=False)
        )

        async def call(extra: str = ""):
            async with _claude_sem:
                r = await claude_client.run(
                    prompt + extra, model, cwd=pdir, system=prompts.TRANSLATE_SYSTEM,
                    allowed_tools=None, max_turns=2, timeout=600, env=_FAST_ENV,
                )
            data = claude_client.extract_json(r["text"])
            ts = data.get("translations")
            if not isinstance(ts, list) or len(ts) != len(chunk):
                raise ValueError(
                    f"번역 개수 불일치: 입력 {len(chunk)} vs 출력 {len(ts) if isinstance(ts, list) else '?'}"
                )
            return [str(t) for t in ts]

        try:
            return await call()
        except (ValueError, json.JSONDecodeError):
            return await call(
                f"\n\n(주의: 이전 시도에서 형식 오류가 있었다. 반드시 입력과 같은 "
                f"{len(chunk)}개의 translations 배열을 가진 JSON만 출력하라.)"
            )

    async def get_keys():
        prompt = prompts.KEY_PROMPT.format(
            sentences_json=json.dumps(src, ensure_ascii=False)
        )
        try:
            async with _claude_sem:
                r = await claude_client.run(
                    prompt, model, cwd=pdir, allowed_tools=None,
                    max_turns=2, timeout=420, env=_FAST_ENV,
                )
            data = claude_client.extract_json(r["text"])
            return {int(k) for k in data.get("key", []) if isinstance(k, (int, float))}
        except Exception:
            return set()  # 핵심 문장 실패는 번역을 막지 않는다

    try:
        results = await asyncio.gather(*(tr_chunk(c) for c in chunks), get_keys())
    except claude_client.ClaudeError as e:
        raise HTTPException(502, f"Claude 호출 실패: {e}")
    except (ValueError, json.JSONDecodeError) as e:
        raise HTTPException(502, f"번역 결과 파싱 실패: {e}")

    keys = results[-1]
    ko = [t for chunk_result in results[:-1] for t in chunk_result]
    result = [
        {"i": s["i"], "src": s["text"], "ko": ko[idx], "key": s["i"] in keys}
        for idx, s in enumerate(sentences)
    ]
    cache.write_text(json.dumps(result, ensure_ascii=False))
    return {"translation": result}


@app.post("/api/papers/{pid}/pages/{n}/keys")
async def extract_keys(pid: str, n: int, body: dict | None = None):
    """기존 번역 캐시에 문단별 핵심 문장 표시만 추가 (재번역 없이)."""
    pdir = _paper_dir(pid)
    model = _model_or_default((body or {}).get("model"))
    cache = pdir / "translations" / f"{n}.json"
    if not cache.exists():
        raise HTTPException(404, "번역이 아직 없습니다")
    items = json.loads(cache.read_text())
    if not items:
        return {"translation": items}

    try:
        b_map = {s["i"]: s.get("b") for s in ensure_sentences(pdir, n)}
    except Exception:
        b_map = {}
    src = [{"i": it["i"], "b": b_map.get(it["i"]), "text": it["src"]} for it in items]
    prompt = prompts.KEY_PROMPT.format(
        sentences_json=json.dumps(src, ensure_ascii=False, indent=0)
    )
    try:
        async with _claude_sem:
            r = await claude_client.run(
                prompt, model, cwd=pdir, allowed_tools=None,
                max_turns=2, timeout=300, env=_FAST_ENV,
            )
        data = claude_client.extract_json(r["text"])
        keys = {int(k) for k in data.get("key", []) if isinstance(k, (int, float))}
    except claude_client.ClaudeError as e:
        raise HTTPException(502, f"Claude 호출 실패: {e}")
    except (ValueError, json.JSONDecodeError) as e:
        raise HTTPException(502, f"핵심 문장 추출 실패: {e}")

    for it in items:
        it["key"] = it["i"] in keys
    cache.write_text(json.dumps(items, ensure_ascii=False))
    return {"translation": items}


# ---------------------------------------------------------------- Figure 설명

@app.get("/api/papers/{pid}/figures/{fid}/explanation")
def get_figure_explanation(pid: str, fid: str):
    f = _paper_dir(pid) / "figures" / f"{fid}.md"
    return {"explanation": f.read_text() if f.exists() else None}


@app.post("/api/papers/{pid}/figures/{fid}/explain")
async def explain_figure(pid: str, fid: str, body: dict | None = None):
    pdir = _paper_dir(pid)
    body = body or {}
    model = _model_or_default(body.get("model"))
    figures = json.loads((pdir / "figures.json").read_text())
    fig = next((f for f in figures if f["id"] == fid), None)
    if not fig:
        raise HTTPException(404, "figure를 찾을 수 없습니다")

    cache = pdir / "figures" / f"{fid}.md"
    if cache.exists() and not body.get("force"):
        async def cached():
            yield {"type": "delta", "text": cache.read_text()}
            yield {"type": "done"}
        return _ndjson(cached())

    ensure_page_image(pdir, fig["page"])
    fulltext = (pdir / "fulltext.txt").read_text()
    mentions = find_mentions(fulltext, fig["label"], fig["caption"])
    prompt = prompts.FIGURE_PROMPT.format(
        img_path=f"pages/page-{fig['page']}.png",
        page=fig["page"],
        label=fig["label"],
        caption=fig["caption"],
        kind_ko="테이블" if fig["kind"] == "table" else "그림",
        mentions="\n".join(f"- {m}" for m in mentions) or "- (본문 언급을 찾지 못함)",
    )

    async def gen():
        async for ev in claude_client.stream(
            prompt, model, cwd=pdir, allowed_tools=("Read",), max_turns=6, timeout=600,
        ):
            if ev["type"] == "done":
                cache.write_text(ev["text"])
                yield {"type": "done"}
            else:
                yield ev

    return _ndjson(gen())


# ---------------------------------------------------------------- 채팅

def _chat_file(pdir: Path) -> Path:
    return pdir / "chat.json"


def _load_chat(pdir: Path) -> dict:
    f = _chat_file(pdir)
    if f.exists():
        return json.loads(f.read_text())
    return {"session_id": None, "messages": []}


@app.get("/api/papers/{pid}/chat")
def get_chat(pid: str):
    return _load_chat(_paper_dir(pid))


@app.post("/api/papers/{pid}/chat/reset")
def reset_chat(pid: str):
    f = _chat_file(_paper_dir(pid))
    if f.exists():
        f.unlink()
    return {"ok": True}


@app.post("/api/papers/{pid}/chat")
async def chat(pid: str, body: dict):
    pdir = _paper_dir(pid)
    message = (body.get("message") or "").strip()
    if not message:
        raise HTTPException(400, "메시지가 비어 있습니다")
    model = _model_or_default(body.get("model"))
    lock = _chat_locks.setdefault(pid, asyncio.Lock())

    async def gen():
        async with lock:
            state = _load_chat(pdir)
            state["messages"].append(
                {"role": "user", "content": message, "ts": int(time.time())}
            )
            _chat_file(pdir).write_text(json.dumps(state, ensure_ascii=False))

            full = None
            async for ev in claude_client.stream(
                message, model, cwd=pdir,
                system=prompts.CHAT_SYSTEM,
                resume=state.get("session_id"),
                allowed_tools=("Read",), max_turns=25, timeout=900,
            ):
                if ev["type"] == "done":
                    full = ev["text"]
                    state["session_id"] = ev.get("session_id") or state.get("session_id")
                    state["messages"].append(
                        {"role": "assistant", "content": full, "ts": int(time.time())}
                    )
                    _chat_file(pdir).write_text(
                        json.dumps(state, ensure_ascii=False)
                    )
                    notion_sync.log_event(
                        pid, {"type": "chat", "q": message, "a": full, "model": model}
                    )
                    yield {"type": "done"}
                else:
                    yield ev

    return _ndjson(gen())


# ---------------------------------------------------------------- 정적 파일

app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


def main():
    import os
    import sys
    import threading
    import webbrowser

    import uvicorn

    # Windows: asyncio 서브프로세스(claude 호출)에 Proactor 루프 필요
    uv_kwargs = {}
    if sys.platform == "win32":
        import asyncio

        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
        uv_kwargs["loop"] = "asyncio"   # uvloop 미탐색 + 위 정책 유지

    host = os.environ.get("PAPER_READER_HOST", "127.0.0.1")
    port = int(os.environ.get("PAPER_READER_PORT", "8123"))
    url = f"http://{host}:{port}"

    def p(*a):
        print(*a, flush=True)

    p()
    p("  📄 Paper Reader")
    p(f"     주소     : {url}")
    p(f"     데이터   : {DATA}")
    cb = claude_client.find_claude()
    if cb:
        p(f"     claude   : {cb}")
    else:
        p("  ⚠ claude CLI를 찾지 못했습니다 — AI 기능(요약/번역/채팅)이 동작하지 않습니다.")
        p("     " + claude_client.NO_CLAUDE_MSG.replace("\n", "\n     "))
    p()

    if not os.environ.get("PAPER_READER_NO_BROWSER"):
        def open_browser():
            try:
                webbrowser.open(url)
            except Exception:
                pass
        threading.Timer(1.2, open_browser).start()

    uvicorn.run(app, host=host, port=port, log_level="warning", **uv_kwargs)


if __name__ == "__main__":
    main()
