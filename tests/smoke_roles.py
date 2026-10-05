"""Smoke test of roles, voice types, completeness gate and re-generation against a running server.

Run the server on a FRESH data folder first, e.g.:  python tests/smoke_roles.py http://127.0.0.1:8766
It creates users and data, so never point it at your real database.
"""
import http.cookiejar
import json
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8766"
EXAMPLE = Path(__file__).resolve().parent.parent / "examples" / "серия_1_пример.txt"
fails = []


class Client:
    def __init__(self):
        self.jar = http.cookiejar.CookieJar()
        self.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))

    def call(self, method, path, body=None, form=None):
        data, headers = None, {}
        if form is not None:
            boundary = uuid.uuid4().hex
            parts = [f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n' for k, v in form.items()]
            data = ("".join(parts) + f"--{boundary}--\r\n").encode()
            headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
        elif body is not None:
            data, headers["Content-Type"] = json.dumps(body).encode(), "application/json"
        req = urllib.request.Request(BASE + path, data=data, method=method, headers=headers)
        try:
            with self.op.open(req) as r:
                return r.status, json.loads(r.read() or b"null")
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                return e.code, json.loads(raw)
            except ValueError:
                return e.code, raw.decode(errors="replace")


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  -> {extra}" if not cond and extra else ""))
    if not cond:
        fails.append(name)


admin, writer, editor = Client(), Client(), Client()
st, _ = admin.call("POST", "/api/auth/setup", {"login": "boss", "password": "pass1234", "name": "Продюсер"})
check("admin setup", st == 200, st)
for login, role in (("wr", "writer"), ("ed", "editor")):
    admin.call("POST", "/api/users", {"login": login, "password": "pass1234", "name": login, "role": role})
writer.call("POST", "/api/auth/login", {"login": "wr", "password": "pass1234"})
editor.call("POST", "/api/auth/login", {"login": "ed", "password": "pass1234"})

# library: only writer/admin may create; editor may not
st, _ = editor.call("POST", "/api/assets", form={"kind": "character", "name": "Х"})
check("editor cannot create characters", st == 403, st)
for name, kind in (("Маша", "character"), ("Костя", "character"), ("Кафе", "location")):
    st, _ = writer.call("POST", "/api/assets", form={"kind": kind, "name": name, "description": f"{name} описание"})
    check(f"writer creates {name}", st == 200, st)

script = EXAMPLE.read_text("utf-8")
st, ep = writer.call("POST", "/api/episodes", {"title": "Тест", "script": script})
check("writer creates episode", st == 200 and len(ep["shots"]) == 7, f"{st} shots={len(ep['shots']) if st == 200 else ''}")
eid = ep["id"]

shot3 = ep["shots"][2]
vo = [d for d in shot3["dialogue"] if d.get("voice") == "voiceover"]
check("voice-over line parsed from «(за кадром)»", len(vo) == 1 and vo[0]["parenthetical"] == "", shot3["dialogue"])
check("voice-over not in Veo prompt", "Он даже не представляет" not in shot3["prompt"] and "Nobody on screen speaks" in shot3["prompt"])
direct = [d for s in ep["shots"] for d in s["dialogue"] if d.get("voice") == "direct"]
check("other lines stay direct", len(direct) >= 4, len(direct))

# visibility / completeness
st, eps = editor.call("GET", "/api/episodes")
check("editor does not see unapproved episode", st == 200 and all(e["id"] != eid for e in eps), eps)
st, _ = editor.call("GET", f"/api/episodes/{eid}")
check("editor gets 404 on unapproved episode", st == 404, st)
st, _ = writer.call("POST", f"/api/episodes/{eid}/status", {"status": "approved"})
check("writer cannot approve", st == 403, st)
st, r = admin.call("POST", f"/api/episodes/{eid}/status", {"status": "approved"})
incomplete = [s for s in ep["shots"] if s["missing"]]
check("approval blocked while shots incomplete", (st == 409) == bool(incomplete), f"{st} incomplete={len(incomplete)} {r}")

# fix every incomplete shot as the writer: pick location for shots without one
st, assets = writer.call("GET", "/api/assets")
cafe = next(a for a in assets if a["name"] == "Кафе")
for s in incomplete:
    if not s["location_version_id"]:
        writer.call("PUT", f"/api/shots/{s['id']}", {"location_version_id": cafe["versions"][0]["id"]})
    if any("не влезает" in m for m in s["missing"]):  # the example's shot 5 is deliberately too long
        short = [{**d, "text": "Пошутил? Серьёзно?"} for d in s["dialogue"]]
        writer.call("PUT", f"/api/shots/{s['id']}", {"dialogue": short})
st, ep = writer.call("GET", f"/api/episodes/{eid}")
check("no incomplete shots after fill", ep["incomplete"] == 0, [(s["label"], s["missing"]) for s in ep["shots"] if s["missing"]])
st, _ = writer.call("POST", f"/api/episodes/{eid}/status", {"status": "review"})
check("writer sends to review", st == 200, st)
st, _ = admin.call("POST", f"/api/episodes/{eid}/status", {"status": "approved"})
check("producer approves complete script", st == 200, st)

# after approval
st, eps = editor.call("GET", "/api/episodes")
check("editor sees approved episode", any(e["id"] == eid for e in eps))
st, _ = writer.call("PUT", f"/api/episodes/{eid}", {"script": script + "\nШот 8\nещё"})
check("writer cannot edit approved script", st == 403, st)
s1 = ep["shots"][0]
st, _ = editor.call("PUT", f"/api/shots/{s1['id']}", {"action": "поменял сюжет"})
check("editor cannot change shot content", st == 403, st)
st, _ = writer.call("PUT", f"/api/shots/{s1['id']}", {"prompt": "x"})
check("writer cannot change prompt", st == 403, st)

# re-generation with edited prompt
st, shot = editor.call("POST", f"/api/shots/{s1['id']}/generate", {"prompt": "Better prompt v2", "reason": "лицо поплыло"})
check("editor regenerates with new prompt", st == 200 and shot["prompt"] == "Better prompt v2", f"{st} {shot if st != 200 else ''}")
import time
time.sleep(3)
st, shot = editor.call("POST", f"/api/shots/{s1['id']}/generate", {"reason": "ещё раз"})
st, ep2 = editor.call("GET", f"/api/episodes/{eid}")
sh = next(s for s in ep2["shots"] if s["id"] == s1["id"])
check("takes keep prompt and reason", [t["reason"] for t in sh["takes"]] == ["лицо поплыло", "ещё раз"]
      and sh["takes"][0]["prompt"] == "Better prompt v2", sh["takes"])
check("revisions counted", sh["revisions"] == 1, sh["revisions"])

# comments
st, _ = writer.call("POST", f"/api/episodes/{eid}/comments", {"text": "кадр тёмный", "shot_id": s1["id"], "is_fix": True})
st, cs = writer.call("GET", f"/api/episodes/{eid}/comments")
check("writer's comment is feedback, not a re-do flag", cs and not cs[-1]["is_fix"], cs[-1] if cs else cs)
st, _ = editor.call("POST", f"/api/episodes/{eid}/comments", {"text": "переделать", "shot_id": s1["id"], "is_fix": True})
st, cs = editor.call("GET", f"/api/episodes/{eid}/comments")
check("editor can flag a re-do", cs[-1]["is_fix"] == 1, cs[-1])

st, _ = editor.call("POST", f"/api/episodes/{eid}/status", {"status": "review"})
check("editor can return script to writer", st == 200, st)
st, eps = editor.call("GET", "/api/episodes")
check("returned episode disappears for editor", all(e["id"] != eid for e in eps))

print("\nFAILED: " + ", ".join(fails) if fails else "\nALL PASSED")
sys.exit(1 if fails else 0)
