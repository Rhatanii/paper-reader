"""claude CLI(헤드리스 -p 모드) 래퍼.

이 서버에 로그인된 Claude 구독 계정을 그대로 사용한다 (별도 API 과금 없음).
- run(): 단발 호출, 최종 텍스트 반환
- stream(): 텍스트 델타를 async generator로 스트리밍
"""
import asyncio
import json
import os
import shutil
from pathlib import Path

HOME = Path.home()


class ClaudeError(RuntimeError):
    pass


NO_CLAUDE_MSG = (
    "claude CLI를 찾을 수 없습니다. Claude Code를 설치하고 로그인하세요:\n"
    "  1) 설치: https://claude.com/claude-code  (또는 npm install -g @anthropic-ai/claude-code)\n"
    "  2) 터미널에서 claude 실행 → /login 으로 구독 계정 로그인"
)


def find_claude() -> str | None:
    candidates = [
        os.environ.get("PATH", ""),
        str(HOME / "bin"),
        str(HOME / ".local" / "bin"),
        str(HOME / ".npm-global" / "bin"),
    ]
    if os.name == "nt":  # Windows: npm 전역 설치 경로
        appdata = os.environ.get("APPDATA")
        if appdata:
            candidates.append(str(Path(appdata) / "npm"))
    return shutil.which("claude", path=os.pathsep.join(candidates))


_claude_bin: str | None = None


def claude_bin() -> str:
    """claude 실행 파일 경로 (지연 해석 — 서버 기동 후 설치해도 인식)."""
    global _claude_bin
    if not _claude_bin:
        _claude_bin = find_claude()
    if not _claude_bin:
        raise ClaudeError(NO_CLAUDE_MSG)
    return _claude_bin


def _build_cmd(model, output_format, system=None, resume=None, allowed_tools=("Read",),
               max_turns=8):
    bin_ = claude_bin()
    # Windows npm 설치는 claude.cmd 라서 cmd.exe 경유로 실행해야 한다
    cmd = ["cmd", "/c", bin_] if (
        os.name == "nt" and bin_.lower().endswith((".cmd", ".bat"))
    ) else [bin_]
    cmd += [
        "-p",
        "--model", model,
        "--output-format", output_format,
        # 사용자 전역 hook/plugin이 매 호출마다 따라붙지 않도록 격리
        "--setting-sources", "project",
        "--max-turns", str(max_turns),
    ]
    if allowed_tools:
        cmd += ["--allowedTools", ",".join(allowed_tools)]
    else:
        cmd += ["--allowedTools", "", "--disallowedTools",
                "Bash,Write,Edit,WebSearch,WebFetch,NotebookEdit"]
    if system:
        cmd += ["--append-system-prompt", system]
    if resume:
        cmd += ["--resume", resume]
    if output_format == "stream-json":
        cmd += ["--verbose", "--include-partial-messages"]
    return cmd


async def _spawn(cmd, prompt, cwd, env_extra=None):
    env = os.environ.copy()
    if env_extra:
        env.update(env_extra)
    return await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(cwd) if cwd else None,
        env=env,
        # stream-json 이벤트에 base64 이미지(Read 결과)가 통째로 실려 오므로
        # 기본 64KB readline 한도를 크게 늘린다
        limit=64 * 1024 * 1024,
    )


async def run(prompt: str, model: str, cwd=None, system=None, resume=None,
              allowed_tools=("Read",), max_turns=8, timeout=600, env=None) -> dict:
    """단발 호출. {'text', 'session_id'} 반환. 실패 시 ClaudeError."""
    cmd = _build_cmd(model, "json", system, resume, allowed_tools, max_turns)
    proc = await _spawn(cmd, prompt, cwd, env)
    try:
        out, err = await asyncio.wait_for(
            proc.communicate(prompt.encode()), timeout=timeout
        )
    except asyncio.TimeoutError:
        proc.kill()
        raise ClaudeError(f"claude 호출이 {timeout}초를 초과했습니다.")
    if proc.returncode != 0:
        raise ClaudeError(
            f"claude 종료 코드 {proc.returncode}: {err.decode(errors='replace')[:800]}"
        )
    try:
        data = json.loads(out.decode())
    except json.JSONDecodeError:
        raise ClaudeError(f"claude 출력 파싱 실패: {out.decode(errors='replace')[:500]}")
    if data.get("is_error"):
        raise ClaudeError(str(data.get("result", data))[:800])
    return {"text": data.get("result", "") or "", "session_id": data.get("session_id")}


async def stream(prompt: str, model: str, cwd=None, system=None, resume=None,
                 allowed_tools=("Read",), max_turns=8, timeout=900):
    """스트리밍 호출. dict 이벤트를 yield:
    {'type':'delta','text':...} / {'type':'done','text':전체,'session_id':...} /
    {'type':'error','message':...}
    """
    cmd = _build_cmd(model, "stream-json", system, resume, allowed_tools, max_turns)
    proc = await _spawn(cmd, prompt, cwd)
    proc.stdin.write(prompt.encode())
    await proc.stdin.drain()
    proc.stdin.close()

    deltas = []          # 스트리밍으로 받은 text_delta 누적
    turn_texts = []      # fallback: assistant 메시지 단위 텍스트
    session_id = None
    result_text = None
    is_error = False
    err_msg = None

    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout

    try:
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                proc.kill()
                yield {"type": "error", "message": f"시간 초과({timeout}초)"}
                return
            try:
                line = await asyncio.wait_for(proc.stdout.readline(), timeout=remaining)
            except asyncio.TimeoutError:
                proc.kill()
                yield {"type": "error", "message": f"시간 초과({timeout}초)"}
                return
            if not line:
                break
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue

            et = ev.get("type")
            if et == "stream_event":
                inner = ev.get("event", {})
                if inner.get("type") == "content_block_delta":
                    delta = inner.get("delta", {})
                    if delta.get("type") == "text_delta" and delta.get("text"):
                        deltas.append(delta["text"])
                        yield {"type": "delta", "text": delta["text"]}
            elif et == "assistant":
                msg = ev.get("message", {})
                text = "".join(
                    b.get("text", "")
                    for b in msg.get("content", [])
                    if isinstance(b, dict) and b.get("type") == "text"
                )
                if text:
                    turn_texts.append(text)
                    if not deltas:
                        # partial 미지원 환경 fallback: 턴 단위로 내보냄
                        yield {"type": "delta", "text": text}
            elif et == "result":
                session_id = ev.get("session_id")
                result_text = ev.get("result")
                is_error = bool(ev.get("is_error"))
                if is_error:
                    err_msg = str(result_text or ev.get("subtype", "unknown error"))

        await proc.wait()
        if proc.returncode != 0 and not is_error:
            err = (await proc.stderr.read()).decode(errors="replace")[:800]
            yield {"type": "error", "message": f"claude 종료 코드 {proc.returncode}: {err}"}
            return
        if is_error:
            yield {"type": "error", "message": err_msg or "unknown error"}
            return

        full = "".join(deltas) or "\n\n".join(turn_texts) or (result_text or "")
        yield {"type": "done", "text": full, "session_id": session_id}
    finally:
        if proc.returncode is None:
            proc.kill()


def extract_json(text: str):
    """모델 응답에서 JSON 오브젝트 추출 (코드펜스/잡담 허용)."""
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("응답에서 JSON을 찾지 못했습니다")
    return json.loads(text[start : end + 1])
