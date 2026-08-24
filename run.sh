#!/usr/bin/env bash
# Paper Reader 실행 (소스 버전, 기본: http://127.0.0.1:8123)
cd "$(dirname "$0")"
export PATH="$HOME/bin:$HOME/.local/bin:$PATH"
# 이 서버에서는 데이터를 저장소 폴더에 유지 (배포판 기본값은 ~/.paper-reader)
export PAPER_READER_DATA="$(pwd)/data"
export PAPER_READER_NO_BROWSER=1
exec python server.py "$@"
