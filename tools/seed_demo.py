"""Наполняет ПУСТУЮ студию демо-данными: персонажи, локации, две арки, серии на разных этапах, событие в календаре.

Запуск при работающей студии:  python tools/seed_demo.py http://127.0.0.1:8765
Если база не пустая (уже есть серии), скрипт ничего не делает, чтобы не испортить настоящие данные.
Первый вход после наполнения: логин demo, пароль demo1234 (создаётся, если админа ещё нет).
"""
import http.cookiejar
import json
import sys
import urllib.error
import urllib.request
import uuid
from datetime import date, timedelta
from pathlib import Path

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8765").rstrip("/")
LOGIN, PASSWORD = "demo", "demo1234"
EXAMPLE = Path(__file__).resolve().parent.parent / "examples" / "серия_1_пример.txt"

jar = http.cookiejar.CookieJar()
op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))


def call(method, path, body=None, form=None):
    data, headers = None, {}
    if form is not None:
        boundary = uuid.uuid4().hex
        data = b"".join(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
                        for k, v in form.items()) + f"--{boundary}--\r\n".encode()
        headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
    elif body is not None:
        data, headers["Content-Type"] = json.dumps(body).encode(), "application/json"
    try:
        with op.open(urllib.request.Request(BASE + path, data=data, method=method, headers=headers)) as r:
            return json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        sys.exit(f"{method} {path}: {e.code} {e.read().decode(errors='replace')[:300]}")


try:
    call("POST", "/api/auth/setup", {"login": LOGIN, "password": PASSWORD, "name": "Продюсер"})
except SystemExit:
    pass  # админ уже есть: войдём ниже
call("POST", "/api/auth/login", {"login": LOGIN, "password": PASSWORD})
if call("GET", "/api/episodes"):
    sys.exit("В студии уже есть серии: демо-данные не добавляю.")

# библиотека
ids = {}
for name, kind, desc in (("Маша", "character", "Девушка 20 лет, тёмное каре, чёрная куртка, быстро говорит"),
                         ("Костя", "character", "Парень 22 года, высокий, очки, мятая худи"),
                         ("Кафе", "location", "Небольшое кафе с кирпичной стеной и неоновой вывеской"),
                         ("Лофт", "location", "Светлый лофт с большими окнами и диваном")):
    a = call("POST", "/api/assets", form={"kind": kind, "name": name, "description": desc})
    ids[name] = a

def member(name):
    a = ids[name]
    return {"asset_id": a["id"], "version_id": a["versions"][0]["id"]}

# арки
arc1 = call("POST", "/api/arcs", {"title": "Первый косплей", "notes": "Маша и Костя готовятся к первому косплей-фестивалю и всё идёт не по плану.",
                                  "members": [member("Маша"), member("Костя"), member("Кафе")]})
arc2 = call("POST", "/api/arcs", {"title": "Лето в городе", "notes": "Лёгкая арка о каникулах: кафе, лофт, случайные встречи.",
                                  "members": [member("Маша"), member("Лофт")]})
call("POST", f"/api/arcs/{arc1['id']}/status", {"status": "approved"})

cards = [(arc1, "Костюм не готов", "Маша находит, что парик пришёл не того цвета."),
         (arc1, "Репетиция в кафе", "Костя репетирует реплики на глазах у посетителей."),
         (arc1, "Утро фестиваля", "Опаздывают на электричку."),
         (arc2, "Неожиданная встреча", "В лофте Маша встречает старого друга."),
         (arc2, "Дождь", "Прогулка срывается, зато находится кафе.")]
eps = [call("POST", f"/api/arcs/{a['id']}/episodes", {"title": t, "synopsis": s}) for a, t, s in cards]

# серия со сценарием: свежий разбор, потом статусы вперёд (admin может согласовать принудительно)
script = EXAMPLE.read_text("utf-8")
ep = call("POST", "/api/episodes", {"title": "Месть за видео", "script": script, "arc_id": arc1["id"]})
for st in ("review", "approved"):
    call("POST", f"/api/episodes/{ep['id']}/status", {"status": st, "force": True})
call("POST", f"/api/episodes/{eps[0]['id']}/status", {"status": "dev"})

# событие в календаре
call("POST", "/api/calendar-events", {"date": (date.today() + timedelta(days=5)).isoformat(), "title": "Косплей-фестиваль"})
print("Готово. Войти: логин", LOGIN, "пароль", PASSWORD)
