# 📄 Paper Reader

> **Claude 구독 계정으로 동작하는 로컬 논문 리더.** API 키 없이 — 논문 개요 요약, 문장 단위 한글 번역, Figure/Table 설명, Q&A 채팅, 밑줄·메모, Notion 읽기 로그까지.

별도 API 과금 없이 **이미 쓰고 있는 Claude Pro/Max 구독**으로 대체. 앱은 로컬에 로그인된 [Claude Code CLI](https://claude.com/claude-code)를 헤드리스로 호출하므로, 사용자마다 각자의 Claude 계정으로 동작하고 사용량도 각자의 구독에서 차감됩니다.

<!-- TODO: 스크린샷 추가 (리더 화면 / 번역 hover / reference 팝업) -->

## 기능

| 기능 | 설명 |
|---|---|
| **논문 개요** | 논문을 열고 「개요 생성」 → ①연구 트렌드 관점에서 무엇이 새로운가 ②Problem (Task와 구분) ③Solution 직관 요약 ④Main 비교 실험 — 4섹션 구조로 novelty를 바로 파악 |
| **페이지 번역** | 페이지별 「번역」 버튼 → 우측 패널에 한글 번역. **원문 문장에 마우스를 올리면 대응 번역 문장이 하이라이트** (반대 방향도 동일). 문장 청크 병렬 번역으로 페이지당 수 초~수십 초 |
| **핵심 문장 형광펜** | 번역 시 문단마다 핵심 문장 1개를 골라 원문·번역 양쪽에 형광펜 표시 |
| **Figure/Table 설명** | 캡션 위의 「✨ 설명」 칩 클릭 → Claude가 페이지 이미지를 직접 보고(vision) 구성요소·읽는 법·논문 주장과의 연결을 설명 |
| **Reference 팝업** | 본문의 `[12]` 또는 `(Wu et al., 2016)` 인용에 **마우스를 올리면** 실제 References 항목 크롭 이미지 + 위치로 이동 팝업. 「✨ 핵심 기여 3줄 요약」 버튼으로 웹 검색 기반 요약(기여/방법/인용 맥락) |
| **Q&A 채팅** | 논문 전문·페이지 이미지를 참고해 답변. 세션이 유지되어 이어지는 질문 가능. 모델 선택(Sonnet/Opus/Haiku 등) |
| **텍스트 선택** | 본문 드래그로 선택·복사. 선택 시 뜨는 툴바에서 「💬 이 부분 질문」 → 선택 구절이 인용된 채 채팅으로 |
| **밑줄 + 메모** | 문장 클릭 → 밑줄과 메모. 「밑줄」 탭에서 모아보기 |
| **Notion 읽기 로그** | 밑줄·메모·채팅 Q&A·개요가 내 Notion의 「📚 Paper Reading Log」 DB에 논문별 페이지로 자동 기록 (선택 기능, 토큰 불필요) |
| **논문 추가** | PDF 드래그 업로드 또는 arXiv 링크 붙여넣기 |

요약·번역·설명 결과는 전부 로컬에 캐시되어 두 번째부터는 즉시 표시됩니다. 대형 논문(100p+)도 페이지 가상화로 가볍게 스크롤됩니다.

## 요구사항

1. **Claude Code CLI + 구독 로그인** — [설치](https://claude.com/claude-code) 후 터미널에서 `claude` 실행 → `/login`
   (Windows PowerShell: `irm https://claude.ai/install.ps1 | iex`)
2. **Python 3.10+** — Windows는 단일 exe로 빌드해서 Python 없이 쓸 수도 있습니다

## 시작하기

### Linux / macOS

```bash
git clone https://github.com/Rhatanii/paper-reader.git
cd paper-reader
pip install -r requirements.txt
python server.py          # → http://127.0.0.1:8123 (브라우저 자동 오픈)
```

### Windows

저장소를 클론(또는 ZIP 다운로드)한 뒤:

- **`run-windows.bat` 더블클릭** — 최초 1회 라이브러리 자동 설치 후 실행
- (선택) **`build-windows.bat`** — 단일 `dist\paper-reader.exe` 생성. 이후 이 파일 하나만 있으면 됨

자세한 빌드/배포 방법은 [DISTRIBUTE.md](DISTRIBUTE.md) 참고.

## Notion 읽기 로그 (선택)

Notion API 토큰 없이 동작합니다 — claude.ai 계정에 [Notion 커넥터](https://claude.ai/settings/connectors)만 연결돼 있으면, 앱이 백그라운드에서 claude CLI를 통해 내 워크스페이스에 「📚 Paper Reading Log」 DB를 만들고 밑줄·채팅·개요를 시간순으로 기록합니다. 헤더의 🅝 버튼에서 켜고 끌 수 있습니다.

## 설정

| 환경변수 | 기본값 | 설명 |
|---|---|---|
| `PAPER_READER_PORT` | `8123` | 포트 |
| `PAPER_READER_HOST` | `127.0.0.1` | 바인드 주소 (로컬 전용 권장) |
| `PAPER_READER_DATA` | `~/.paper-reader` | 논문·캐시 저장 위치 |
| `PAPER_READER_NO_BROWSER` | (없음) | 설정 시 브라우저 자동 열기 끔 |

요약·번역 등의 프롬프트는 전부 [`prompts.py`](prompts.py)에 모여 있어, 원하는 스타일(요약 구조, 번역 톤, 대상 언어)로 자유롭게 수정할 수 있습니다.

## 동작 방식

```
브라우저 ── FastAPI 서버 ──┬── PyMuPDF: 페이지 렌더·문장 좌표·인용/참고문헌 파싱
                           └── claude CLI (-p 헤드리스): 요약·번역·vision 설명·채팅·Notion 기록
```

- Claude 호출은 `claude -p --output-format stream-json` 서브프로세스 — **API 키가 아니라 그 컴퓨터에 로그인된 계정**을 사용
- 채팅은 `--resume`으로 세션을 유지하고, Read 도구만 허용해 논문 텍스트·페이지 이미지를 직접 읽음
- 번역은 문장 배열↔배열 JSON 계약으로 원문 좌표와 1:1 정렬 → hover 하이라이트
- 문장 hover·밑줄·인용 클릭은 좌표 판정, 텍스트 선택은 투명 텍스트 레이어(PDF.js 방식)

## 한계

- 인용 팝업은 `[N]` 숫자 스타일과 author-year 스타일(natbib: "Wu et al., 2016")을 지원 — 비정형 서지 포맷에서는 일부 인용을 놓칠 수 있음
- 번역은 영어 → 한국어 중심 (prompts.py 수정으로 다른 언어 가능)
- AI 기능 사용량은 본인의 Claude 구독 한도에서 차감됨
- 서버는 로컬(127.0.0.1) 사용을 전제로 하며 인증 기능이 없음 — 외부 공개 바인드는 권장하지 않음
