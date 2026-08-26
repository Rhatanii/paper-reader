"""경로 해석 — 소스 실행과 PyInstaller 실행 파일 양쪽 지원.

- 리소스(static 등): 소스 실행 시 앱 디렉토리, 실행 파일에서는 번들 추출 디렉토리
- 데이터(data): 기본 ~/.paper-reader — 실행 파일은 임시 폴더에서 돌므로 앱 내부에 두면 안 됨.
  PAPER_READER_DATA 환경변수로 변경 가능.
"""
import os
import pathlib
import sys
from pathlib import Path

# ── 텍스트 파일 I/O 기본 인코딩을 UTF-8로 고정 ──
# 한글 Windows는 기본 인코딩이 cp949라서 논문 텍스트의 특수문자(∗, é 등)를
# 저장/로드할 때 UnicodeEncodeError가 난다. 앱 전체 Path.read_text/write_text/open이
# 인코딩 미지정 시 UTF-8을 쓰도록 이 모듈 import 시점에 패치한다.
_orig_read_text = pathlib.Path.read_text
_orig_write_text = pathlib.Path.write_text
_orig_open = pathlib.Path.open


def _read_text_utf8(self, encoding=None, errors=None):
    return _orig_read_text(self, encoding=encoding or "utf-8", errors=errors)


def _write_text_utf8(self, data, encoding=None, errors=None, newline=None):
    return _orig_write_text(
        self, data, encoding=encoding or "utf-8", errors=errors, newline=newline
    )


def _open_utf8(self, mode="r", buffering=-1, encoding=None, errors=None, newline=None):
    if "b" not in mode and encoding is None:
        encoding = "utf-8"
    return _orig_open(self, mode, buffering, encoding, errors, newline)


pathlib.Path.read_text = _read_text_utf8
pathlib.Path.write_text = _write_text_utf8
pathlib.Path.open = _open_utf8

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
