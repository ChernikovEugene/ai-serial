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
            data = b""
            for k, v in form.items():
                if isinstance(v, tuple):  # (имя файла, байты)
                    data += (f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"; filename="{v[0]}"\r\n'
                             f"Content-Type: image/png\r\n\r\n").encode() + v[1] + b"\r\n"
                else:
                    data += f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
            data += f"--{boundary}--\r\n".encode()
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

# нейросеть на шот: выбирает монтажёр, через API идёт только Veo
st, shot = editor.call("PUT", f"/api/shots/{s1['id']}", {"engine": "kling"})
check("editor picks another engine for a shot", st == 200 and shot["engine"] == "kling", f"{st} {shot if st != 200 else ''}")
st, _ = editor.call("PUT", f"/api/shots/{s1['id']}", {"engine": "nope"})
check("unknown engine rejected", st == 400, st)
st, _ = writer.call("PUT", f"/api/shots/{s1['id']}", {"engine": "veo"})
check("writer cannot pick the engine", st == 403, st)
st, _ = editor.call("POST", f"/api/shots/{s1['id']}/generate", {})
check("non-Veo shot is not sent to the API", st == 400, st)
s2 = ep["shots"][1]
editor.call("PUT", f"/api/shots/{s2['id']}", {"engine": "kling"})
st, r = editor.call("POST", f"/api/episodes/{eid}/generate", {"mode": "all"})
check("episode generate skips non-Veo shots", st == 200 and r["skipped"] >= 1, r)
editor.call("PUT", f"/api/shots/{s1['id']}", {"engine": "veo"})
editor.call("PUT", f"/api/shots/{s2['id']}", {"engine": "veo"})

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

# реплики из кавычек в тексте действия
st, ep3 = writer.call("POST", "/api/episodes", {"title": "Кавычки", "script": (
    "Шот 1\nМаша входит в кафе и тихо говорит: «Привет, Костя!»\n\n"
    "Шот 2\n«Ты опоздал», — сказала Маша.\nКостя пожимает плечами.\n\n"
    "Шот 3\nНа вывеске «Кафе» горит свет. Кто-то шепчет: «Не уходи».\n")})
check("quotes episode created", st == 200 and len(ep3["shots"]) == 3, st)
q1, q2, q3 = ep3["shots"]
check("quote becomes a dialogue line with speaker and manner",
      [(d["speaker"], d["parenthetical"], d["text"]) for d in q1["dialogue"]] == [("Маша", "тихо", "Привет, Костя!")], q1["dialogue"])
check("quote removed from the action", "«" not in q1["action"] and "Привет" not in q1["action"] and "говорит" not in q1["action"], q1["action"])
check("dash attribution removed", [d["speaker"] for d in q2["dialogue"]] == ["Маша"] and "сказала" not in q2["action"], (q2["dialogue"], q2["action"]))
check("title in quotes stays in the action", "«Кафе»" in q3["action"], q3["action"])
check("on-screen message in quotes is not speech", "«Встретимся?»" in shot3["action"] and all(d["text"] != "Встретимся?" for d in shot3["dialogue"]), shot3["action"])
check("unknown speaker stays empty and blocks completeness",
      q3["dialogue"][0]["speaker"] == "" and q3["dialogue"][0].get("auto") and any("не выбран говорящий" in m for m in q3["missing"]), q3)
check("empty speaker does not break the prompt", '"Не уходи"' in q3["prompt"], q3["prompt"])
# выбранный говорящий появляется в кадре, имя в действии тоже
maria = next(a for a in assets if a["name"] == "Маша")
st, upd = writer.call("PUT", f"/api/shots/{q3['id']}", {"dialogue": [{**q3["dialogue"][0], "speaker": "Маша", "asset_id": maria["id"]}]})
check("chosen speaker joins the characters in frame", any(c["asset_id"] == maria["id"] for c in upd["characters"]), upd["characters"])
st, upd = writer.call("PUT", f"/api/shots/{q3['id']}", {"action": upd["action"] + " Костя смотрит на неё."})
check("name typed into the action joins the characters", any(c["name"] == "Костя" for c in upd["characters"]), upd["characters"])

# герой и локация с картинкой в промпте идут как «reference image N», а не по имени
PNG = bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d49444154789c6360000002000001e221bc330000000049454e44ae426082")
st, alt = writer.call("POST", "/api/assets", form={"kind": "character", "name": "Альтушка", "description": "девушка в чёрной куртке", "images": ("a.png", PNG)})
check("character with image created", st == 200 and alt["versions"][0]["images"], st)
st, loc = writer.call("POST", "/api/assets", form={"kind": "location", "name": "Лофт", "description": "светлый лофт", "images": ("l.png", PNG)})
st, ep4 = writer.call("POST", "/api/episodes", {"title": "Референсы", "script": "Шот 1\nЛОКАЦИЯ: @Лофт\nАльтушка смотрит в камеру, не улыбаясь.\n"})
p4 = ep4["shots"][0]["prompt"]
check("character with image is a reference in the prompt", "the character from reference image 1" in p4 and "Альтушка" not in p4.split("Action:")[1], p4)
check("preserve-identity rules in the prompt", "Strictly preserve" in p4 and "Recreate the location exactly" in p4, p4)
check("location reference numbered", "shown in reference image 2" in p4, p4)
check("reference order matches the references list", [r["kind"] for r in ep4["shots"][0]["references"]] == ["character", "location"], ep4["shots"][0]["references"])
check("character without image keeps the name", "Character Костя" in ep3["shots"][1]["prompt"] or "Костя" in ep3["shots"][1]["prompt"], ep3["shots"][1]["prompt"])

print("\nFAILED: " + ", ".join(fails) if fails else "\nALL PASSED")
sys.exit(1 if fails else 0)
