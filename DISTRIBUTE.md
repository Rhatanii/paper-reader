# 📦 Paper Reader 배포 가이드

로컬 PC에서 **claude CLI 설치 + 로그인만 하면** 동작하는 단일 실행 파일로 배포할 수 있다.
실행 파일 안에 Python·모든 라이브러리·프론트엔드가 전부 들어 있다.

## 대상 PC에서 할 일 (딱 2가지)

1. **Claude Code 설치** — https://claude.com/claude-code
   (또는 `npm install -g @anthropic-ai/claude-code`)
2. **로그인** — 터미널에서 `claude` 실행 → `/login` 으로 구독 계정 로그인
   (비대화형 환경이면 `claude setup-token` 사용)

그다음 실행 파일을 더블클릭/실행하면 브라우저가 자동으로 열린다 (http://127.0.0.1:8123).

- 논문·번역·밑줄 데이터는 `~/.paper-reader/` 에 저장된다.
- **Notion 로그**도 별도 설정 없이 동작한다 — 단, claude.ai 계정에 Notion 커넥터가
  연결되어 있어야 한다 (claude.ai → Settings → Connectors → Notion 연결. 이 계정은 이미 연결됨).

## 실행 파일 얻기

### Linux (x86_64) — 빌드 완료

`dist/paper-reader` (64MB) 를 그대로 복사하면 된다:

```bash
scp <이서버>:/mnt/ssd3/hs/Dataset/paper-reader/dist/paper-reader .
chmod +x paper-reader && ./paper-reader
```

### Windows — `paper-reader-win.zip` 하나로 해결

서버의 `paper-reader-win.zip`(약 100KB)을 PC로 내려받아 압축 해제하면
더블클릭용 배치 파일이 들어 있다:

| 파일 | 용도 |
|---|---|
| `run-windows.bat` | **더블클릭 실행** — 최초 1회 자동으로 라이브러리 설치 후 서버 시작 + 브라우저 오픈. 이후에도 이 파일로 실행 |
| `build-windows.bat` | (선택) 단일 `dist\paper-reader.exe` 빌드 — 이후 exe 하나만 있으면 됨 |

사전 준비: ① Python 3.10+ (python.org, 설치 시 **"Add python.exe to PATH" 체크**),
② Claude Code 설치(PowerShell에서 `irm https://claude.ai/install.ps1 | iex`) 후
`claude` 실행 → `/login`.

주의: 빌드한 exe 첫 실행 시 SmartScreen 경고가 뜨면 「추가 정보 → 실행」.
콘솔 창을 닫으면 앱이 종료된다.

### macOS — 해당 OS에서 1회 빌드

PyInstaller는 크로스 빌드가 안 되므로 맥에서 한 번 빌드한다 (Python 3.10+, 5분):

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt pyinstaller
python build.py     # → dist/paper-reader
```

> 빌드는 깨끗한 venv에서 할 것 — anaconda base 등에서 빌드하면 무관한 패키지가
> 딸려 들어가 파일이 수백 MB로 커진다.

### 실행 파일 없이 소스로 실행 (Python이 이미 있는 PC)

```bash
pip install -r requirements.txt
python server.py
```

## 환경변수

| 변수 | 기본값 | 설명 |
|---|---|---|
| `PAPER_READER_PORT` | `8123` | 포트 |
| `PAPER_READER_HOST` | `127.0.0.1` | 바인드 주소 (본인 PC 전용이면 기본값 유지) |
| `PAPER_READER_DATA` | `~/.paper-reader` | 데이터 저장 위치 |
| `PAPER_READER_NO_BROWSER` | (없음) | 설정 시 브라우저 자동 열기 끔 |

## 배포 시 복사할 파일

실행 파일 배포: `dist/paper-reader` 하나면 끝.
소스/빌드 배포: `*.py`, `static/`, `requirements.txt`, `build.py` (data/·dist/ 제외).
