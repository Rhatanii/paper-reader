@echo off
rem Paper Reader - dist\paper-reader.exe 빌드용. 실행에는 필요 없습니다.
cd /d "%~dp0"
title Paper Reader Build

set "PYCMD="
for %%P in ("py -3.14" "py -3.13" "py -3.12" "py -3.11" "py -3.10" "py -3" "python" "python3") do (
    if not defined PYCMD (
        %%~P -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" >nul 2>nul
        if not errorlevel 1 set "PYCMD=%%~P"
    )
)

if not defined PYCMD (
    echo.
    echo   [오류] Python 3.10 이상을 찾지 못했습니다.
    echo.
    echo   https://www.python.org/downloads/ 에서 최신 Python 을 설치하세요.
    echo   설치 첫 화면에서 "Add python.exe to PATH" 를 체크해야 합니다.
    echo   이미 있는 3.9 등 구버전은 지우지 않아도 됩니다.
    echo.
    pause
    exit /b 1
)

echo.
echo   사용할 Python: %PYCMD%

if exist buildvenv (
    buildvenv\Scripts\python.exe -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" >nul 2>nul
    if errorlevel 1 rmdir /s /q buildvenv
)

if not exist buildvenv (
    echo.
    echo   [1/2] 빌드 환경 준비 중입니다. 몇 분 걸립니다.
    echo.
    %PYCMD% -m venv buildvenv
    if errorlevel 1 (
        echo.
        echo   [오류] 가상환경 생성에 실패했습니다.
        pause
        exit /b 1
    )
    buildvenv\Scripts\python.exe -m pip install --upgrade pip
    buildvenv\Scripts\python.exe -m pip install -r requirements.txt pyinstaller
    if errorlevel 1 (
        echo.
        echo   [오류] 설치에 실패했습니다. 인터넷 연결을 확인하세요.
        pause
        exit /b 1
    )
)

echo.
echo   [2/2] exe 빌드 중입니다. 수 분 걸립니다.
echo.
buildvenv\Scripts\python.exe build.py
if errorlevel 1 (
    echo.
    echo   [오류] 빌드에 실패했습니다.
    pause
    exit /b 1
)

echo.
echo   완료! dist\paper-reader.exe 가 만들어졌습니다.
echo   이제 이 exe 파일 하나만 복사해서 쓰면 됩니다.
echo.
pause
