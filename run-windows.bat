@echo off
rem Paper Reader - 실행용. 이 파일을 더블클릭하세요.
cd /d "%~dp0"
title Paper Reader

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

if exist venv (
    venv\Scripts\python.exe -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" >nul 2>nul
    if errorlevel 1 (
        echo   구버전으로 만들어진 환경을 발견했습니다. 새로 만듭니다.
        rmdir /s /q venv
    )
)

if not exist venv (
    echo.
    echo   [1/2] 최초 1회 준비 중입니다. 몇 분 걸립니다.
    echo.
    %PYCMD% -m venv venv
    if errorlevel 1 (
        echo.
        echo   [오류] 가상환경 생성에 실패했습니다.
        pause
        exit /b 1
    )
    venv\Scripts\python.exe -m pip install --upgrade pip
    venv\Scripts\python.exe -m pip install -r requirements.txt
    if errorlevel 1 (
        echo.
        echo   [오류] 라이브러리 설치에 실패했습니다. 인터넷 연결을 확인하세요.
        pause
        exit /b 1
    )
)

echo.
echo   [2/2] Paper Reader 를 시작합니다. 잠시 후 브라우저가 열립니다.
echo   이 창을 닫으면 앱이 종료됩니다.
echo.
venv\Scripts\python.exe server.py
echo.
pause
