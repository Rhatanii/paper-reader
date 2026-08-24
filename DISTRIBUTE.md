# 📦 빌드 & 배포 가이드

Paper Reader는 두 가지 방식으로 쓸 수 있다:

1. **소스 실행** — Python 3.10+ 만 있으면 됨 (README의 시작하기 참고)
2. **단일 실행 파일** — PyInstaller로 빌드. 대상 PC에 Python이 없어도 됨

어느 쪽이든 대상 PC에서 필요한 것은 **Claude Code CLI 설치 + 구독 계정 로그인**뿐이다.

## 대상 PC 준비 (공통, 2가지)

1. **Claude Code 설치** — https://claude.com/claude-code
   (Windows PowerShell: `irm https://claude.ai/install.ps1 | iex` / macOS·Linux: `curl -fsSL https://claude.ai/install.sh | bash` 또는 `npm install -g @anthropic-ai/claude-code`)
2. **로그인** — 터미널에서 `claude` 실행 → `/login` (비대화형 환경은 `claude setup-token`)

Notion 읽기 로그를 쓰려면 claude.ai 계정에 Notion 커넥터가 연결되어 있어야 한다
(claude.ai → Settings → Connectors → Notion).

## Windows

| 파일 | 용도 |
|---|---|
| `run-windows.bat` | **더블클릭 실행** — 최초 1회 자동으로 라이브러리 설치 후 서버 시작 + 브라우저 오픈. `python` 명령이 구버전이어도 py 런처로 3.10+를 자동 탐색 |
| `build-windows.bat` | 단일 `dist\paper-reader.exe` 빌드 — 이후 exe 하나만 복사해서 쓰면 됨 |

주의:
- 빌드한 exe 첫 실행 시 SmartScreen 경고가 뜨면 「추가 정보 → 실행」 (서명 없는 개인 빌드라 뜨는 정상 경고)
- 콘솔 창을 닫으면 앱이 종료된다
- 데이터는 `%USERPROFILE%\.paper-reader` 에 저장된다

## macOS / Linux 빌드

PyInstaller는 크로스 빌드가 안 되므로 배포하려는 OS에서 빌드한다 (5분):

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt pyinstaller
python build.py     # → dist/paper-reader
```

> **깨끗한 venv에서 빌드할 것** — anaconda base 등에서 빌드하면 무관한 패키지가
> 딸려 들어가 파일이 수백 MB로 커지고 Qt 충돌이 날 수 있다.

빌드된 실행 파일은 GitHub Release로 배포하면 된다:
`gh release create v1.0 dist/paper-reader --title "v1.0"`

## 환경변수

| 변수 | 기본값 | 설명 |
|---|---|---|
| `PAPER_READER_PORT` | `8123` | 포트 |
| `PAPER_READER_HOST` | `127.0.0.1` | 바인드 주소 |
| `PAPER_READER_DATA` | `~/.paper-reader` | 데이터 저장 위치 |
| `PAPER_READER_NO_BROWSER` | (없음) | 설정 시 브라우저 자동 열기 끔 |
