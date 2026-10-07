"""Accounts: PBKDF2 password hashes, cookie sessions, four roles.

admin  (Продюсер)   - everything: approves scripts, manages users and settings, can act as any role
writer (Сценарист)  - writes episodes: script, shots, characters, locations; sends the script for approval
editor (Монтажёр)   - sees only approved episodes; generates video, edits prompts, picks takes, flags re-dos
viewer (Зритель)    - read and comment only
"""
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta

from fastapi import HTTPException, Request

from . import db

COOKIE = "studio_session"
SESSION_DAYS = 30
ROLES = {"admin": "Продюсер", "writer": "Сценарист-креативщик", "editor": "Нейронщик-монтажёр",
         "viewer": "Зритель (смотрит и комментирует)"}
# Обязанности ролей — показываются на странице «Команда».
ROLE_DUTIES = {
    "writer": ["Пишет синопсисы арок и серий (раздел «Серии»)",
               "Отправляет синопсисы клиенту на согласование (PDF арки)",
               "Прописывает подробное ТЗ серии: сценарий, шоты, персонажи, локации",
               "Пишет описание для поста к каждой серии"],
    "editor": ["Берёт серии с готовым ТЗ (раздел «Продакшн»)",
               "Генерирует шоты в нейросети и монтирует ролик",
               "Сдаёт ролик продюсеру и клиенту на согласование",
               "Публикует готовые ролики и отмечает «Выложено»"],
    "admin": ["Следит за всеми статусами и сроками (раздел «Статус»)",
              "Проверяет ТЗ и ролики, ведёт согласование с клиентом",
              "Управляет командой и настройками"],
}

# Who may do what. The API checks these; the UI mirrors them (see `can` in static/js/core.js).
WRITE_ROLES = ("admin", "writer")   # script, shots content, library, schedule
GEN_ROLES = ("admin", "editor")     # prompts, generation, takes, re-dos

# Statuses a role may set. The editor can also send a script back to the writer ("review").
# Сценарист ведёт серию от синопсиса до ТЗ (включая согласование синопсисов с клиентом).
# Монтажёр берёт серию с готовым ТЗ: генерация, монтаж, показ ролика клиенту, выкладка.
WRITER_STATUSES = {"synopsis", "synopsis_review", "synopsis_ok", "dev", "review"}
STATUS_TARGETS = {
    "admin": None,  # any
    "writer": WRITER_STATUSES,
    "editor": {"review", "generating", "fixes", "client_review", "ready", "posted"},
}
# Status a role may change an episode FROM (None = any).
STATUS_SOURCES = {
    "admin": None,
    "writer": WRITER_STATUSES,
    "editor": {"approved", "generating", "fixes", "client_review", "ready"},
}
# The editor only sees episodes whose script (ТЗ) is ready.
EDITOR_VISIBLE = {"approved", "generating", "fixes", "client_review", "ready", "posted"}


def can_see(role: str, status: str) -> bool:
    return role != "editor" or status in EDITOR_VISIBLE


def allowed_statuses(role: str, current: str) -> list[str]:
    """Statuses this role may move an episode to from `current` (empty = read-only)."""
    if role not in STATUS_TARGETS:
        return []
    src, dst = STATUS_SOURCES[role], STATUS_TARGETS[role]
    if src is not None and current not in src:
        return []
    keys = [k for k, _ in db.STATUSES]
    return [k for k in keys if (dst is None or k in dst) and k != current]


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 200_000).hex()
    return f"pbkdf2${salt}${h}"


def check_password(password: str, stored: str) -> bool:
    try:
        _, salt, h = stored.split("$")
    except ValueError:
        return False
    cand = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 200_000).hex()
    return hmac.compare_digest(cand, h)


def has_users() -> bool:
    with db.connect() as c:
        return c.execute("SELECT 1 FROM users LIMIT 1").fetchone() is not None


def create_session(user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    exp = (datetime.now() + timedelta(days=SESSION_DAYS)).isoformat(timespec="seconds")
    with db.connect() as c:
        c.execute("DELETE FROM sessions WHERE expires_at < ?", (db.now(),))
        c.execute("INSERT INTO sessions(token, user_id, expires_at) VALUES (?,?,?)", (token, user_id, exp))
    return token


def user_for_token(token: str | None):
    if not token:
        return None
    with db.connect() as c:
        r = c.execute("SELECT u.id, u.login, u.name, u.role FROM sessions s JOIN users u ON u.id=s.user_id "
                      "WHERE s.token=? AND s.expires_at > ?", (token, db.now())).fetchone()
    return dict(r) if r else None


def drop_session(token: str | None):
    if token:
        with db.connect() as c:
            c.execute("DELETE FROM sessions WHERE token=?", (token,))


def current(request: Request) -> dict:
    return request.state.user


def require(request: Request, *roles: str) -> dict:
    u = request.state.user
    if u["role"] not in roles:
        raise HTTPException(403, "Недостаточно прав")
    return u


def writer(request: Request) -> dict:
    return require(request, *WRITE_ROLES)


def editor(request: Request) -> dict:
    return require(request, *GEN_ROLES)


def writer_or_editor(request: Request) -> dict:
    return require(request, "admin", "writer", "editor")


def admin(request: Request) -> dict:
    return require(request, "admin")
