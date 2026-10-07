"""Minimal GitHub REST client for the repository this studio was cloned from.

Token: `gh auth token` (GitHub CLI), else the `github_token` setting, else the GITHUB_TOKEN env var.
"""
import json
import os
import re
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

from . import db

ROOT = Path(__file__).resolve().parent.parent


class GitHubError(Exception):
    def __init__(self, msg: str, code: int = 0):
        super().__init__(msg)
        self.code = code


def repo_slug() -> str:
    try:
        url = subprocess.run(["git", "remote", "get-url", "origin"], cwd=ROOT, capture_output=True,
                             text=True, timeout=10).stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        url = ""
    m = re.search(r"github\.com[:/]([^/]+/[^/]+?)(?:\.git)?/?$", url)
    if not m:
        raise GitHubError("Папка студии не связана с репозиторием на GitHub")
    return m.group(1)


def token() -> str | None:
    try:
        r = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, timeout=10)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return db.get_settings().get("github_token") or os.environ.get("GITHUB_TOKEN") or None


def call(method: str, path: str, body: dict | None = None, raw: bool = False):
    """`path` is relative to the repo (e.g. "/issues") unless it starts with "//" (e.g. "//user")."""
    tok = token()
    if not tok:
        raise GitHubError("Нет доступа к GitHub: войдите через GitHub CLI (gh auth login) "
                          "или вставьте токен в «Настройки»")
    url = "https://api.github.com" + (path[1:] if path.startswith("//") else f"/repos/{repo_slug()}{path}")
    req = urllib.request.Request(
        url, method=method, data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Bearer {tok}", "X-GitHub-Api-Version": "2022-11-28",
                 "Accept": "application/vnd.github.raw" if raw else "application/vnd.github+json",
                 "User-Agent": "ai-serial-studio"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = r.read()
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read()).get("message", "")
        except Exception:
            msg = ""
        raise GitHubError(f"GitHub ответил {e.code}: {msg}".rstrip(": "), e.code)
    except urllib.error.URLError:
        raise GitHubError("Нет связи с GitHub, проверьте интернет")
    if raw:
        return data
    return json.loads(data) if data else None


_logins: dict = {}


def login() -> str:
    tok = token()
    if tok not in _logins:
        _logins[tok] = call("GET", "//user")["login"]
    return _logins[tok]
