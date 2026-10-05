"""Accounts: PBKDF2 password hashes, cookie sessions, three roles.

admin  - everything, including managing users and settings
editor - create and edit episodes, assets, run generation
viewer - read and comment only
"""
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta

from fastapi import HTTPException, Request

from . import db

COOKIE = "studio_session"
SESSION_DAYS = 30
ROLES = {"admin": "Администратор", "editor": "Редактор", "viewer": "Зритель (смотрит и комментирует)"}


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


def editor(request: Request) -> dict:
    return require(request, "admin", "editor")


def admin(request: Request) -> dict:
    return require(request, "admin")
