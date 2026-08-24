"""경로 해석 — 소스 실행과 PyInstaller 실행 파일 양쪽 지원.

- 리소스(static 등): 소스 실행 시 앱 디렉토리, 실행 파일에서는 번들 추출 디렉토리
- 데이터(data): 기본 ~/.paper-reader — 실행 파일은 임시 폴더에서 돌므로 앱 내부에 두면 안 됨.
  PAPER_READER_DATA 환경변수로 변경 가능.
"""
import os
import sys
from pathlib import Path

IS_FROZEN = bool(getattr(sys, "frozen", False))

# PyInstaller onefile은 리소스를 sys._MEIPASS 에 풀어 놓는다
_BUNDLE = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))


def resource_path(name: str) -> Path:
    """번들된 읽기 전용 리소스(static 등) 경로."""
    return _BUNDLE / name


def data_dir() -> Path:
    env = os.environ.get("PAPER_READER_DATA")
    d = Path(env).expanduser() if env else Path.home() / ".paper-reader"
    d.mkdir(parents=True, exist_ok=True)
    return d


DATA = data_dir()
