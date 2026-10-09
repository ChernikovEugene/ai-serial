#!/bin/bash
# Update the studio code from GitHub (main), stop the old studio and start the new one on macOS.
# The data folder is never touched. Put this file into the studio project folder and double-click it.
cd "$(dirname "$0")"
export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:$PATH"

pause_and_exit() { read -n 1 -s -r -p "Press any key to close"; exit 1; }

if ! command -v git >/dev/null 2>&1; then
    echo "Git is not installed. Run in Terminal: xcode-select --install   and try again."
    pause_and_exit
fi
if [ ! -d .git ]; then
    echo "This folder is not a git copy of the project (no .git). Put this file into the studio project folder."
    pause_and_exit
fi

echo "[1/4] Stopping the old studio (if it is running)..."
OLD=$(lsof -ti tcp:8765 -sTCP:LISTEN 2>/dev/null)
if [ -n "$OLD" ]; then kill $OLD 2>/dev/null; sleep 2; fi

if ! git diff --quiet || ! git diff --cached --quiet; then
    echo "There are unsaved local changes in the code files. Saving them aside (nothing is lost)..."
    git stash push -m "auto-saved before update" || { echo "Could not save local changes."; pause_and_exit; }
fi

echo "[2/4] Switching to main..."
git checkout main || { echo "Update failed (see above). Nothing in the data folder was changed."; pause_and_exit; }

echo "[3/4] Downloading the latest changes from: $(git remote get-url origin)"
git pull --ff-only origin main || { echo "Update failed (see above). Nothing in the data folder was changed."; pause_and_exit; }
echo "Now at version: $(git log -1 --format='%h %s')"

echo "[4/4] Starting the studio..."
bash ./start.command
