@echo off
setlocal
set ROOT=%~dp0
set VENV=%ROOT%backend\venv
set PYTHON=%VENV%\Scripts\python.exe
set PIP=%VENV%\Scripts\pip.exe
set UVICORN=%VENV%\Scripts\uvicorn.exe

echo.
echo  German ^<-^> English Translator
echo  ================================
echo.

:: ── Create venv if it does not exist ────────────────────────────────────────
if not exist "%VENV%\Scripts\activate.bat" (
    echo  [Setup] Creating virtual environment...
    python -m venv "%VENV%"
    if errorlevel 1 (
        echo.
        echo  ERROR: Could not create venv. Make sure Python 3.11+ is installed and on PATH.
        pause
        exit /b 1
    )
    echo  [Setup] Virtual environment created.
    echo.
)

:: ── Install / upgrade requirements ──────────────────────────────────────────
echo  [Setup] Installing requirements (this may take a while on first run)...
"%PIP%" install -q --upgrade pip
"%PIP%" install -q -r "%ROOT%backend\requirements.txt"
if errorlevel 1 (
    echo.
    echo  ERROR: pip install failed. Check your internet connection or requirements.txt.
    pause
    exit /b 1
)
echo  [Setup] Requirements OK.
echo.

:: ── Start backend ────────────────────────────────────────────────────────────
start "Translator - Backend" cmd /k "cd /d "%ROOT%" && echo [Backend] Starting... && "%UVICORN%" backend.server:app --reload --port 8000"

:: Give uvicorn time to bind before the frontend proxy tries to connect
timeout /t 5 /nobreak >nul

:: ── Start frontend ───────────────────────────────────────────────────────────
start "Translator - Frontend" cmd /k "cd /d "%ROOT%frontend" && echo [Frontend] Starting... && npm run dev"

:: Open browser once Vite is up
timeout /t 6 /nobreak >nul
start http://localhost:5173

echo  Backend  ^>  http://localhost:8000
echo  Frontend ^>  http://localhost:5173
echo.
echo  Both services are running in separate windows.
echo  Close those windows (or press Ctrl+C inside them) to stop.
echo.
pause
endlocal
