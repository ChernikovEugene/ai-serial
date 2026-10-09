#!/bin/bash
# Update the studio code from GitHub (main) and start it on macOS. The data folder is never touched.
# Put this file into the studio project folder (next to start.command) and double-click it.
cd "$(dirname "$0")"
export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:$PATH"

pause_and_exit() { read -n 1 -s -r -p "Press any key to close"; exit 1; }

if ! command -v git >/dev/null 2>&1; then
    echo "Git is not installed. Run in Terminal: xcode-select --install   and try again."
    pause_and_exit
fi

if ! git diff --quiet || ! git diff --cached --quiet; then
    echo "There are unsaved local changes in the code files. Nothing was updated, nothing was lost."
    echo "Ask Eugene or Claude to help save them first."
    pause_and_exit
fi

echo "[1/3] Switching to main..."
git checkout main || { echo "Update failed (see above). Nothing in the data folder was changed."; pause_and_exit; }

echo "[2/3] Downloading the latest changes..."
git pull --ff-only origin main || { echo "Update failed (see above). Nothing in the data folder was changed."; pause_and_exit; }

echo "[3/3] Starting the studio..."
echo "If the studio is already open in another window, close that window first."
bash ./start.command
