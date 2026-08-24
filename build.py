#!/usr/bin/env python
"""Paper Reader 단일 실행 파일 빌드 (PyInstaller).

사용법 — 배포하려는 OS에서 실행 (PyInstaller는 크로스 빌드 불가):
    pip install -r requirements.txt pyinstaller
    python build.py

결과물: dist/paper-reader  (Windows: dist/paper-reader.exe)
"""
import os
import subprocess
import sys

sep = ";" if os.name == "nt" else ":"

args = [
    sys.executable, "-m", "PyInstaller",
    "--noconfirm", "--clean", "--onefile",
    "--name", "paper-reader",
    "--add-data", f"static{sep}static",
    # uvicorn/fastapi가 런타임에 동적 import 하는 모듈들
    "--collect-all", "uvicorn",
    "--hidden-import", "multipart",
    "--hidden-import", "python_multipart",
    "--collect-all", "pysbd",
    "server.py",
]

# 앱과 무관한데 빌드 환경(anaconda 등)에 깔려 있으면 딸려 들어오는 패키지 차단
for mod in ["PyQt5", "PyQt6", "PySide2", "PySide6", "matplotlib", "tkinter",
            "IPython", "jupyter", "notebook", "pandas", "scipy", "torch", "cv2"]:
    args[args.index("server.py"):args.index("server.py")] = ["--exclude-module", mod]

print("$", " ".join(args[2:]))
subprocess.check_call(args, cwd=os.path.dirname(os.path.abspath(__file__)))

exe = os.path.join("dist", "paper-reader" + (".exe" if os.name == "nt" else ""))
print(f"\n빌드 완료 → {exe}")
print("대상 PC에서: claude CLI 설치 + 로그인 후 이 파일을 실행하면 됩니다.")
