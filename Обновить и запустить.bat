@echo off
rem Update the studio code from GitHub (main), stop the old studio and start the new one.
rem The data folder is never touched.
cd /d "%~dp0"

where git >nul 2>nul
if errorlevel 1 (
    echo Git is not installed or not in PATH. Install Git for Windows and try again.
    pause
    exit /b 1
)
if not exist ".git" (
    echo This folder is not a git copy of the project. Put this file into the studio project folder.
    pause
    exit /b 1
)

echo [1/4] Stopping the old studio (if it is running)...
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":8765 " ^| findstr LISTENING') do taskkill /F /PID %%p >nul 2>nul

git diff --quiet
if errorlevel 1 goto stash
git diff --cached --quiet
if errorlevel 1 goto stash
goto update

:stash
echo There are unsaved local changes in the code files. Saving them aside (nothing is lost)...
git stash push -m "auto-saved before update"
if errorlevel 1 goto fail

:update
echo [2/4] Switching to main...
git checkout main
if errorlevel 1 goto fail

echo [3/4] Downloading the latest changes...
git remote get-url origin
git pull --ff-only origin main
if errorlevel 1 goto fail
for /f "delims=" %%v in ('git log -1 "--format=%%h %%s"') do echo Now at version: %%v

echo [4/4] Starting the studio...
call start.bat
goto :eof

:fail
echo Update failed (see the message above). Nothing in the data folder was changed.
pause
exit /b 1
