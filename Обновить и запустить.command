#!/bin/bash
# Обновить студию из GitHub (ветка main) и запустить её на macOS. Папку data/ не трогает никогда.
# Файл можно запускать откуда угодно (хоть из «Загрузок»): он сам найдёт папку студии,
# а если её нет, скачает студию в ~/ai-serial.
REPO_URL="https://github.com/ChernikovEugene/ai-serial.git"
PORT=8765
export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:$PATH"

fail() { echo; echo "Не получилось: $1"; echo "Папка data/ не изменялась."; read -n 1 -s -r -p "Нажмите любую клавишу, чтобы закрыть"; exit 1; }

is_studio() { [ -f "$1/app/main.py" ] && [ -d "$1/.git" ] && git -C "$1" remote get-url origin 2>/dev/null | grep -q "ai-serial"; }

command -v git >/dev/null 2>&1 || fail "не установлен Git. Откройте «Терминал», выполните  xcode-select --install  и запустите файл снова."

# 1. Найти папку студии.
HERE="$(cd "$(dirname "$0")" && pwd)"
STUDIO=""
for d in "$HERE" "$HERE/ai-serial" "$HOME/ai-serial" "$HOME/Projects/ai-serial" "$HOME/Documents/ai-serial" \
         "$HOME/Desktop/ai-serial" "$HOME/Downloads/ai-serial"; do
    if is_studio "$d"; then STUDIO="$d"; break; fi
done
if [ -z "$STUDIO" ]; then
    while IFS= read -r f; do
        d="$(dirname "$(dirname "$f")")"
        if is_studio "$d"; then STUDIO="$d"; break; fi
    done < <(find "$HOME" -maxdepth 5 -path "*/app/main.py" -not -path "*/.venv/*" -not -path "$HOME/Library/*" 2>/dev/null)
fi
if [ -z "$STUDIO" ]; then
    echo "Папка студии не найдена, скачиваю студию в $HOME/ai-serial ..."
    git clone "$REPO_URL" "$HOME/ai-serial" || fail "не удалось скачать студию (нет интернета?)."
    STUDIO="$HOME/ai-serial"
fi
cd "$STUDIO" || fail "не открывается папка $STUDIO"
echo "Папка студии: $STUDIO"

# 2. Остановить старую студию, если она ещё работает (иначе браузер покажет старую версию).
OLD=$(lsof -ti tcp:$PORT -sTCP:LISTEN 2>/dev/null)
if [ -n "$OLD" ]; then
    echo "Останавливаю старую студию..."
    kill $OLD 2>/dev/null; sleep 2
    OLD=$(lsof -ti tcp:$PORT -sTCP:LISTEN 2>/dev/null); [ -n "$OLD" ] && kill -9 $OLD 2>/dev/null
fi

# 3. Скачать свежий main. Свои несохранённые правки в коде не теряются: они откладываются в git stash.
echo "Скачиваю обновления..."
git fetch origin main || fail "не удалось связаться с GitHub (нет интернета?)."
if ! git diff --quiet || ! git diff --cached --quiet || [ -n "$(git ls-files --others --exclude-standard)" ]; then
    echo "В коде были локальные изменения, откладываю их в сторону (git stash)."
    git stash push -u -m "auto-backup $(date '+%Y-%m-%d %H:%M')" || fail "не удалось отложить локальные изменения."
fi
git checkout -q main 2>/dev/null || git checkout -q -b main origin/main || fail "не удалось переключиться на main."
if ! git merge --ff-only -q origin/main; then
    BACKUP="backup/main-$(date '+%Y%m%d-%H%M%S')"
    echo "Локальный main разошёлся с GitHub, сохраняю его в ветку $BACKUP и беру версию с GitHub."
    git branch "$BACKUP" && git reset -q --hard origin/main || fail "не удалось обновить main."
fi
echo "Версия студии: $(git log -1 --format='%h  %s')"

# 4. Обновить зависимости, если они изменились, и запустить.
if [ -x ".venv/bin/python" ] && command -v uv >/dev/null 2>&1; then
    uv pip install -q --python .venv/bin/python -r requirements.txt || echo "Внимание: зависимости не обновились, пробую запустить так."
fi
chmod +x ./start.command 2>/dev/null
exec bash ./start.command
