#!/bin/bash
# Запуск студии на macOS (аналог start.bat). Двойной клик в Finder.
# Нужен uv (brew install uv): он сам скачает подходящий Python и зависимости.
cd "$(dirname "$0")"
export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:$PATH"

if [ ! -x ".venv/bin/python" ]; then
    echo "First run: creating environment and installing dependencies..."
    if ! command -v uv >/dev/null 2>&1; then
        echo "Setup failed: uv not found. Install it: brew install uv"
        read -n 1 -s -r -p "Press any key to close"; exit 1
    fi
    uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -r requirements.txt \
        || { rm -rf .venv; echo "Setup failed. Make sure you are online."; read -n 1 -s -r -p "Press any key to close"; exit 1; }
fi

echo "Studio is running: http://127.0.0.1:8765  - close this window to stop it."
(sleep 2; open http://127.0.0.1:8765) &
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8765
