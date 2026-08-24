"""Notion 논문 읽기 로그 동기화.

밑줄/채팅/개요 이벤트를 논문별 큐(notion-queue.jsonl)에 쌓고, 백그라운드에서
claude CLI + claude.ai Notion 커넥터(MCP)로 "📚 Paper Reading Log" DB에 기록한다.
별도 Notion 토큰이 필요 없다 (구독 계정의 Notion 연결을 그대로 사용).
"""
import asyncio
import json
import time
from datetime import datetime
from pathlib import Path

import claude_client
import paths
import prompts

DATA = paths.DATA
CONFIG_FILE = DATA / "notion-config.json"

DEFAULT_CONFIG = {
    "enabled": True,
    "model": "sonnet",          # flush 에이전트용 모델
    "database_id": None,
    "database_url": None,
    "last_sync": None,
    "last_error": None,
}

_flush_lock = asyncio.Lock()    # Notion 쓰기 직렬화 (중복 DB 생성 방지)
_scheduled: set[str] = set()    # 디바운스 대기 중인 paper id


def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    if CONFIG_FILE.exists():
        try:
            cfg.update(json.loads(CONFIG_FILE.read_text()))
        except json.JSONDecodeError:
            pass
    return cfg


def save_config(cfg: dict):
    CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=1))


def _queue_file(pid: str) -> Path:
    return DATA / pid / "notion-queue.jsonl"


def _paper_notion_file(pid: str) -> Path:
    return DATA / pid / "notion.json"


def _load_paper_notion(pid: str) -> dict:
    f = _paper_notion_file(pid)
    if f.exists():
        try:
            return json.loads(f.read_text())
        except json.JSONDecodeError:
            pass
    return {}


def queued_count(pid: str) -> int:
    f = _queue_file(pid)
    if not f.exists():
        return 0
    return sum(1 for line in f.read_text().splitlines() if line.strip())


def status() -> dict:
    cfg = load_config()
    queued = {}
    if DATA.exists():
        for d in DATA.iterdir():
            if d.is_dir():
                n = queued_count(d.name)
                if n:
                    queued[d.name] = n
    return {**cfg, "queued": queued, "queued_total": sum(queued.values())}


def paper_page_url(pid: str) -> str | None:
    return _load_paper_notion(pid).get("page_url")


def log_event(pid: str, event: dict):
    """이벤트를 큐에 적재하고 디바운스 flush 예약. enabled=False면 무시."""
    cfg = load_config()
    if not cfg.get("enabled"):
        return
    event = {**event, "ts": event.get("ts") or int(time.time())}
    qf = _queue_file(pid)
    with qf.open("a") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")
    _schedule_flush(pid)


def _schedule_flush(pid: str, delay: float = 4.0):
    if pid in _scheduled:
        return
    _scheduled.add(pid)

    async def later():
        try:
            await asyncio.sleep(delay)
            _scheduled.discard(pid)
            await flush_paper(pid)
        except Exception:
            _scheduled.discard(pid)

    try:
        asyncio.get_running_loop().create_task(later())
    except RuntimeError:
        _scheduled.discard(pid)


def _fmt_events(events: list[dict]) -> str:
    """이벤트에 사람이 읽을 시각을 붙여 프롬프트용 JSON으로 직렬화."""
    out = []
    for e in events:
        e = dict(e)
        e["time"] = datetime.fromtimestamp(e.pop("ts", time.time())).strftime("%H:%M")
        out.append(e)
    return json.dumps(out, ensure_ascii=False, indent=1)


async def flush_paper(pid: str) -> dict:
    """해당 논문의 큐를 Notion에 기록. 성공 시 기록한 만큼 큐에서 제거."""
    async with _flush_lock:
        cfg = load_config()
        qf = _queue_file(pid)
        if not qf.exists():
            return {"pid": pid, "flushed": 0}
        lines = [l for l in qf.read_text().splitlines() if l.strip()]
        if not lines:
            return {"pid": pid, "flushed": 0}

        meta_file = DATA / pid / "meta.json"
        if not meta_file.exists():           # 삭제된 논문
            qf.unlink(missing_ok=True)
            return {"pid": pid, "flushed": 0}
        title = json.loads(meta_file.read_text()).get("title", "(제목 없음)")

        events = [json.loads(l) for l in lines]
        pn = _load_paper_notion(pid)

        if cfg.get("database_id"):
            db_section = (
                f"로그 데이터베이스 id는 이미 확보되어 있다: {cfg['database_id']} — "
                "검색하지 말고 이 id를 그대로 사용하라."
            )
        else:
            db_section = (
                "notion-search로 '📚 Paper Reading Log' 데이터베이스를 찾아라. 없으면 "
                "notion-create-database로 생성하라 — 제목 '📚 Paper Reading Log', 위치는 "
                "개인(private) 영역, 속성: '논문'(title), '상태'(select: 읽는 중/완료), "
                "'마지막 기록'(date)."
            )

        if pn.get("page_id"):
            page_section = (
                f"이 논문의 로그 페이지 id는 {pn['page_id']} — 그대로 사용하라."
            )
        else:
            page_section = (
                f"위 데이터베이스 안에 제목이 '{title}' 인 페이지가 있으면 그것을 쓰고, "
                "없으면 새 페이지를 생성하라 (제목 속성 '논문' = 그 제목, '상태' = '읽는 중')."
            )

        prompt = prompts.NOTION_FLUSH_PROMPT.format(
            db_section=db_section,
            page_section=page_section,
            title=title,
            today=datetime.now().strftime("%Y-%m-%d"),
            events_json=_fmt_events(events),
        )

        try:
            r = await claude_client.run(
                prompt,
                cfg.get("model", "sonnet"),
                cwd=DATA / pid,
                allowed_tools=("ToolSearch", "mcp__claude_ai_Notion"),
                max_turns=30,
                timeout=420,
            )
            data = claude_client.extract_json(r["text"])
            if not data.get("page_id"):
                raise ValueError(f"page_id 없음: {str(data)[:200]}")
        except Exception as e:
            cfg["last_error"] = f"{type(e).__name__}: {e}"[:400]
            save_config(cfg)
            return {"pid": pid, "flushed": 0, "error": cfg["last_error"]}

        # 성공: 설정/페이지 정보 저장
        if data.get("database_id"):
            cfg["database_id"] = data["database_id"]
        if data.get("database_url"):
            cfg["database_url"] = data["database_url"]
        cfg["last_sync"] = int(time.time())
        cfg["last_error"] = None
        save_config(cfg)
        _paper_notion_file(pid).write_text(
            json.dumps(
                {"page_id": data["page_id"], "page_url": data.get("page_url")},
                ensure_ascii=False,
            )
        )

        # flush 도중 새로 쌓인 이벤트는 남긴다
        remain = [l for l in qf.read_text().splitlines() if l.strip()][len(lines):]
        if remain:
            qf.write_text("\n".join(remain) + "\n")
        else:
            qf.unlink(missing_ok=True)
        return {"pid": pid, "flushed": len(lines), "page_url": data.get("page_url")}


async def flush_all() -> list[dict]:
    results = []
    if DATA.exists():
        for d in sorted(DATA.iterdir()):
            if d.is_dir() and queued_count(d.name):
                results.append(await flush_paper(d.name))
    return results
