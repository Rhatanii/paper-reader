# 📄 Paper Reader

Moonlight 대체용 로컬 논문 리더. **이 서버에 로그인된 Claude 구독 계정(claude CLI)** 을
그대로 사용하므로 별도 API 비용이 들지 않는다.

## 실행

```bash
cd /mnt/ssd3/hs/Dataset/paper-reader
./run.sh          # http://127.0.0.1:8123  (데이터: ./data)
```

**로컬 PC 배포용 실행 파일**: `dist/paper-reader` (Linux x86_64, 64MB).
대상 PC는 claude CLI 설치+로그인만 하면 된다. macOS/Windows 빌드 방법과 상세는
[DISTRIBUTE.md](DISTRIBUTE.md) 참고. (배포판 데이터 위치는 `~/.paper-reader`)

- 서버는 127.0.0.1에만 바인딩된다. 로컬 PC에서는 **VSCode 포트 포워딩**(Remote-SSH가
  8123을 자동 감지) 또는 `ssh -L 8123:127.0.0.1:8123 <서버>` 로 접속.
- 포트 변경: `PAPER_READER_PORT=9000 ./run.sh`
- 백그라운드 실행: `nohup ./run.sh > /tmp/paper-reader.log 2>&1 &`

## 기능

| 기능 | 사용법 |
|---|---|
| **논문 개요** | 논문 열면 우측 패널 → 「✨ 개요 생성」. ①연구 트렌드 관점 novelty ②Problem(Task와 구분) ③Solution 직관 요약 ④Main 비교 실험, 4개 섹션으로 생성 |
| **페이지 번역** | 각 페이지 왼쪽 상단 「🌐 번역」 → 우측 패널에 한글 번역. **원문 문장에 마우스를 올리면 대응 번역 문장이 하이라이트**(반대 방향도 동일, 번역 문장 클릭 시 원문 위치로 스크롤) |
| **Figure/Table 설명** | 캡션 위에 뜨는 「✨ Figure N 설명」 칩 클릭 → 페이지 이미지를 Claude가 직접 보고 상세 설명 |
| **논문 Q&A 채팅** | 우측 「채팅」 탭. 논문 전문 + 페이지 이미지를 참고해 답변, 대화 맥락 유지(세션 resume). 헤더의 모델 선택(Sonnet/Opus/Fable/Haiku)이 모든 기능에 적용 |
| **밑줄 + 메모** | 페이지에서 **문장 클릭** → 밑줄 저장(선택적으로 메모). 「밑줄」 탭에서 목록 확인·이동·삭제. 다시 클릭하면 메모 수정/밑줄 해제 |
| **Notion 읽기 로그** | 밑줄·메모·채팅 Q&A·개요가 Notion **「📚 Paper Reading Log」 DB**에 논문별 페이지로 자동 기록(날짜 헤딩 아래 시간순). 헤더 🅝 버튼에서 on/off, 수동 동기화, 페이지 바로가기 |
| **Reference 팝업** | 본문의 `[12]` 같은 인용 표기 클릭 → 팝업에 ①실제 References 항목 크롭 이미지 ②「위치로 이동」(해당 위치 플래시) ③Claude가 웹 검색으로 찾은 "어떤 논문인가 + 인용 맥락" 요약(캐시). `[N]` 숫자 스타일 논문에서 동작 |
| **크기 조절** | 사이드 패널 폭·채팅 입력창 높이를 경계선 드래그로 조절 (localStorage에 기억) |
| **텍스트 선택** | 본문 드래그로 텍스트 선택·복사 가능(투명 텍스트 레이어). 선택 시 뜨는 툴바에서 「💬 이 부분 질문」 → 선택 구절이 인용된 채 채팅으로, 「📋 복사」 → 클립보드 |
| **핵심 문장 형광펜** | 페이지 번역 시 문단마다 핵심 문장 1개를 골라 원문·번역 양쪽에 연두 형광펜 표시. 예전에 번역해 둔 페이지는 재번역 없이 백그라운드로 핵심 문장만 추출 |
| **논문 추가** | PDF 드래그 업로드 또는 arXiv 링크(`/abs/` 링크도 자동 변환) |

요약/번역/figure 설명은 논문별로 디스크에 캐시되어 두 번째부터는 즉시 표시된다.

## 구조

```
server.py          FastAPI 백엔드 (업로드/파싱/번역/요약/채팅 API + 정적 서빙)
claude_client.py   claude CLI(-p 헤드리스) 래퍼 — 구독 계정 사용, 스트리밍 지원
pdf_utils.py       PyMuPDF 파싱: 페이지 PNG, 문장별 좌표(pysbd), figure/table 캡션 탐지
prompts.py         요약/번역/figure/채팅 프롬프트
static/            프론트엔드 (vanilla JS, 빌드 불필요)
data/<id>/         논문별 저장소: paper.pdf, meta.json, fulltext.txt, pages/,
                   sentences/, translations/, figures/, summary.md, chat.json
```

## 동작 방식 메모

- Claude 호출은 `claude -p --output-format stream-json --setting-sources project` 서브프로세스.
  `--setting-sources project` 로 사용자 전역 hook/플러그인(session-log 등)이 매 호출에
  붙지 않게 격리했다.
- 채팅은 논문 디렉토리를 cwd로 `--resume <session_id>` 를 사용해 맥락을 유지하고,
  Read 도구만 허용해 `fulltext.txt`/페이지 PNG를 직접 읽게 한다.
- 번역은 문장 배열 → JSON 배열 계약(개수 일치 검증, 실패 시 1회 재시도).
- 문장 좌표는 PyMuPDF 단어 bbox를 pysbd 문장 span에 매핑해 라인 단위로 병합한 것.
- **Notion 로그**는 Notion 토큰 없이 동작한다: 이벤트(밑줄/메모/채팅/개요)를
  `data/<id>/notion-queue.jsonl` 큐에 쌓고, 백그라운드에서 claude CLI가
  **claude.ai Notion 커넥터(MCP)** 로 기록한다(계정 레벨 연결이라 headless에서도 동작,
  도구는 deferred라 ToolSearch로 로드). 첫 flush 때 DB·논문 페이지를 자동 생성하고
  id를 `data/notion-config.json` / `data/<id>/notion.json`에 저장해 재사용한다.
  밑줄 저장/채팅 완료 4초 뒤 디바운스 flush, 실패 시 큐 유지 + 🅝 메뉴에 에러 표시,
  「지금 동기화」로 수동 재시도. flush 에이전트 모델은 `notion-config.json`의 `model`(기본 sonnet).

## 의존성

`pip install --user pymupdf pysbd fastapi "uvicorn[standard]" python-multipart`
(2026-08-10 설치 완료. claude CLI ≥ 2.x 필요 — `~/bin` PATH에 있음)
