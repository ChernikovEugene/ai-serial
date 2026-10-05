@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo First run: creating environment and installing dependencies...
    python -m venv .venv
    if errorlevel 1 goto error
    ".venv\Scripts\python.exe" -m pip install --upgrade pip
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
    if errorlevel 1 goto error
)
echo Studio is running: http://127.0.0.1:8765  - close this window to stop it.
start "" http://127.0.0.1:8765
".venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8765
pause
goto :eof
:error
echo Setup failed. Make sure Python 3.10+ is installed and you are online.
pause