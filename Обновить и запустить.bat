@echo off
rem Update the studio code from GitHub (main) and start it. The data folder is never touched.
cd /d "%~dp0"

where git >nul 2>nul
if errorlevel 1 (
    echo Git is not installed or not in PATH. Install Git for Windows and try again.
    pause
    exit /b 1
)

git diff --quiet
if errorlevel 1 goto dirty
git diff --cached --quiet
if errorlevel 1 goto dirty

echo [1/3] Switching to main...
git checkout main
if errorlevel 1 goto fail

echo [2/3] Downloading the latest changes...
git pull --ff-only origin main
if errorlevel 1 goto fail

echo [3/3] Starting the studio...
echo If the studio is already open in another window, close that window first.
call start.bat
goto :eof

:dirty
echo There are unsaved local changes in the code files. Nothing was updated, nothing was lost.
echo Ask Eugene or Claude to help save them first.
pause
exit /b 1

:fail
echo Update failed (see the message above). Nothing in the data folder was changed.
pause
exit /b 1
