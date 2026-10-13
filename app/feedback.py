"""Правки для Claude через GitHub Issues: админ отправляет сюда заявку из «Сообщить о проблеме» (вкладка «Заявки»).

Each note is an issue with the label LABEL. A screenshot (only if the admin ticked it — the repo is public) is committed
to the SHOTS_BRANCH branch and linked from the issue. The hidden `<!-- pravka: {...} -->` line in the issue body keeps the details machine-readable,
so Claude can pick the notes up on the command «Запусти правки» (see CLAUDE.md).
"""
import base64
import json
import re
from datetime import datetime
from urllib.parse import quote

from .github import GitHubError, call, login, repo_slug

LABEL = "правка"
IN_WORK = "в работе"
SHOTS_BRANCH = "pravki-shots"
LABELS = {LABEL: ("ff3b6b", "Правка, оставленная кнопкой «Правка» в студии"),
          IN_WORK: ("f5b642", "Claude или человек уже вносит эту правку")}
META = re.compile(r"<!-- pravka: (\{.*?\}) -->", re.S)


def _ensure_labels() -> None:
    for name, (color, desc) in LABELS.items():
        try:
            call("POST", "/labels", {"name": name, "color": color, "description": desc})
        except GitHubError as e:
            if e.code != 422:  # 422 = already exists
                raise


def _ensure_shots_branch() -> None:
    try:
        call("GET", f"/branches/{SHOTS_BRANCH}")
    except GitHubError as e:
        if e.code != 404:
            raise
        sha = call("GET", "/git/ref/heads/main")["object"]["sha"]
        call("POST", "/git/refs", {"ref": f"refs/heads/{SHOTS_BRANCH}", "sha": sha})


def _note(issue: dict) -> dict:
    body = issue.get("body") or ""
    m = META.search(body)
    meta = json.loads(m.group(1)) if m else {}
    return {
        "number": issue["number"], "title": issue["title"], "url": issue["html_url"],
        "text": meta.get("text", body.split("\n---\n")[0].strip()),
        "page": meta.get("page", ""), "author": meta.get("author") or issue["user"]["login"],
        "shots": meta.get("shots", []), "created_at": issue["created_at"], "comments": issue["comments"],
        "status": "done" if issue["state"] == "closed" and issue.get("state_reason") == "completed"
        else "closed" if issue["state"] == "closed"
        else "work" if any(lb["name"] == IN_WORK for lb in issue["labels"]) else "new",
    }


def list_notes() -> list[dict]:
    issues = call("GET", f"/issues?labels={quote(LABEL)}&state=all&sort=created&direction=desc&per_page=50")
    return [_note(i) for i in issues if "pull_request" not in i]


def create(text: str, page: str, viewport: str, author: str, images: list[tuple[bytes, str]]) -> dict:
    _ensure_labels()
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    shots = []
    if images:
        _ensure_shots_branch()
        for i, (data, ext) in enumerate(images, 1):
            path = f"shots/{stamp}-{i}.{ext}"
            call("PUT", f"/contents/{path}", {"message": f"Скриншот к правке ({author})", "branch": SHOTS_BRANCH,
                                               "content": base64.b64encode(data).decode()})
            shots.append(path)
    slug = repo_slug()
    first = text.strip().splitlines()[0] if text.strip() else "Правка по скриншоту"
    title = first if len(first) <= 80 else first[:77] + "…"
    meta = {"text": text.strip(), "page": page, "viewport": viewport, "author": author, "shots": shots}
    body = "\n".join([
        text.strip() or "_(без текста, см. скриншот)_", "", "---",
        f"**Где:** `{page or '—'}` · **Окно:** {viewport or '—'} · **Автор:** {author} (через {login()})",
        *[f"![скриншот {i}](https://github.com/{slug}/blob/{SHOTS_BRANCH}/{p}?raw=true)" for i, p in enumerate(shots, 1)],
        "", f"<!-- pravka: {json.dumps(meta, ensure_ascii=False)} -->",
    ])
    return _note(call("POST", "/issues", {"title": title, "body": body, "labels": [LABEL]}))
