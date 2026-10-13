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

# защита от пустых шотов: незаполненный шот в генерацию не уходит, пункты закрываются явно
st, cur = editor.call("GET", f"/api/episodes/{eid}")
cur1 = next(x for x in cur["shots"] if x["id"] == s1["id"])
if not cur1["ready"]:
    st, _ = editor.call("POST", f"/api/shots/{s1['id']}/generate", {"reason": "проверка"})
    check("unready shot is not generated", st == 400, st)
    admin.call("PUT", f"/api/shots/{s1['id']}", {"sound_mode": "ambient", **({} if cur1["characters"] else {"no_characters": True}),
                                                 **({} if cur1["action"].strip() else {"action": "-"})})
st, cur = editor.call("GET", f"/api/episodes/{eid}")
check("shot closed explicitly -> ready", next(x for x in cur["shots"] if x["id"] == s1["id"])["ready"],
      next(x for x in cur["shots"] if x["id"] == s1["id"])["checks"])
st, r = editor.call("POST", f"/api/shots/{s1['id']}/generate", {"reason": "длина", "duration": 7})
check("regenerate: duration outside the model is rejected", st == 400, st)

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

# нейросеть на шот: выбирает монтажёр, через API (Magnific) идут все, кроме «Другой»
st, shot = editor.call("PUT", f"/api/shots/{s1['id']}", {"engine": "other"})
check("editor picks another engine for a shot", st == 200 and shot["engine"] == "other", f"{st} {shot if st != 200 else ''}")
st, _ = editor.call("PUT", f"/api/shots/{s1['id']}", {"engine": "nope"})
check("unknown engine rejected", st == 400, st)
st, _ = writer.call("PUT", f"/api/shots/{s1['id']}", {"engine": "veo"})
check("writer cannot pick the engine", st == 403, st)
st, _ = editor.call("POST", f"/api/shots/{s1['id']}/generate", {})
check("manual-engine shot is not sent to the API", st == 400, st)
s2 = ep["shots"][1]
editor.call("PUT", f"/api/shots/{s2['id']}", {"engine": "other"})
st, r = editor.call("POST", f"/api/episodes/{eid}/generate", {"mode": "all"})
check("episode generate skips manual-engine shots", st == 200 and r["skipped"] >= 1, r)
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
# много фото в одной версии: догрузка, порядок, удаление
av = alt["versions"][0]
st, _ = editor.call("POST", f"/api/assets/{alt['id']}/versions/{av['id']}/images", form={"images": ("b.png", PNG)})
check("editor cannot add photos", st == 403, st)
st, a2 = writer.call("POST", f"/api/assets/{alt['id']}/versions/{av['id']}/images", form={"images": ("b.png", PNG)})
imgs = next(v for v in a2["versions"] if v["id"] == av["id"])["images"] if st == 200 else []
check("photos appended to the version", len(imgs) == 2, (st, imgs))
st, a3 = writer.call("PUT", f"/api/assets/{alt['id']}/versions/{av['id']}/images", {"images": imgs[::-1]})
check("photos reordered", st == 200 and next(v for v in a3["versions"] if v["id"] == av["id"])["images"] == imgs[::-1], st)
st, _ = writer.call("PUT", f"/api/assets/{alt['id']}/versions/{av['id']}/images", {"images": ["/etc/passwd"]})
check("foreign photo path rejected", st == 400, st)
st, a4 = writer.call("PUT", f"/api/assets/{alt['id']}/versions/{av['id']}/images", {"images": imgs[:1]})
check("photo removed", st == 200 and len(next(v for v in a4["versions"] if v["id"] == av["id"])["images"]) == 1, st)
st, meta = writer.call("GET", "/api/meta")
check("meta has series title", st == 200 and "series_title" in meta, st)

check("character without image keeps the name", "Character Костя" in ep3["shots"][1]["prompt"] or "Костя" in ep3["shots"][1]["prompt"], ep3["shots"][1]["prompt"])

# --- готовность шота, звук, архив, ключи нейросетей ---
s4 = ep4["shots"][0]
check("shot has checks", [c["key"] for c in s4["checks"]] == ["characters", "location", "action", "duration", "prompt", "sound"], s4.get("checks"))
check("veo shot without replica: sound check open", not s4["ready"] and not s4["checks"][-1]["ok"], s4["checks"])
st, r = writer.call("PUT", f"/api/shots/{s4['id']}", {"sound_off": True})
check("writer ticks sound_off -> ready", st == 200 and r["sound_off"] and r["ready"], (st, r.get("checks")))
# все шоты заполнены и зелёные -> серия сама переходит в «ТЗ готово»; вернуть в «Пишется ТЗ», чтобы проверять дальше
check("all shots green -> episode auto-moves to approved", st == 200 and r.get("episode_status") == "approved", (st, r.get("episode_status")))
st, _ = writer.call("PUT", f"/api/shots/{s4['id']}", {"camera": "Slow push-in"})
check("writer locked after auto approve", st == 403, st)
admin.call("POST", f"/api/episodes/{ep4['id']}/status", {"status": "dev"})
st, r = editor.call("PUT", f"/api/shots/{s4['id']}", {"engine": "kling", "voiceover": True, "sound_off": False})
check("editor sets voiceover; replica required again", st == 200 and r["voiceover"] and not r["ready"], (st, r.get("checks")))
# --- камера по умолчанию статичная ---
check("default camera is static", s4["camera"] == "Static camera, locked-off, no camera movement", s4["camera"])
st, r = writer.call("PUT", f"/api/shots/{s4['id']}", {"camera": "Slow push-in"})
check("camera preset saved and used in prompt", st == 200 and "Camera: Slow push-in." in r["prompt"], (st, r["prompt"][-200:]))
st, r = writer.call("PUT", f"/api/shots/{s4['id']}", {"camera": "Медленный облёт вокруг героини"})
check("custom camera text saved", st == 200 and r["camera"] == "Медленный облёт вокруг героини", st)
# --- локация шота: текстом вместо библиотеки ---
loc_before = s4["location_version_id"]
st, r = writer.call("PUT", f"/api/shots/{s4['id']}", {"clear_location": True, "location_mode": "text"})
check("text location mode: empty text -> no location", st == 200 and r["location_mode"] == "text" and not next(c for c in r["checks"] if c["key"] == "location")["ok"], (st, r.get("checks")))
st, r = writer.call("PUT", f"/api/shots/{s4['id']}", {"location_text": "Пустая парковка ночью"})
check("text location counts as chosen", st == 200 and next(c for c in r["checks"] if c["key"] == "location")["ok"] and "не выбрана локация" not in r["missing"], (st, r.get("checks"), r.get("missing")))
check("text location goes into the prompt", "Location: Пустая парковка ночью." in r["prompt"], r["prompt"])
st, _ = writer.call("PUT", f"/api/shots/{s4['id']}", {"location_mode": "bogus"})
check("bad location_mode rejected", st == 400, st)
st, r = writer.call("PUT", f"/api/shots/{s4['id']}", {"location_mode": "library"})
check("back to library mode: location needed again", st == 200 and "не выбрана локация" in r["missing"], (st, r.get("missing")))
st, r = writer.call("PUT", f"/api/shots/{s4['id']}", {"location_version_id": loc_before})
check("library location restored", st == 200 and "не выбрана локация" not in r["missing"], (st, r.get("missing")))
st, _ = writer.call("GET", "/api/integrations")
check("writer cannot read integrations", st == 403, st)
st, lst = admin.call("POST", "/api/integrations", {"name": "Kling основной", "kind": "kling", "api_key": "sk-secret-1234"})
check("integration added, key masked", st == 200 and lst[-1]["key_mask"] == "••••1234" and "api_key" not in lst[-1], (st, lst))
iid = lst[-1]["id"]
st, lst = admin.call("PUT", f"/api/integrations/{iid}", {"name": "Kling 2", "api_key": "••••1234"})
check("masked key keeps the old value", st == 200 and lst[-1]["name"] == "Kling 2" and lst[-1]["key_mask"] == "••••1234", lst)
st, _ = admin.call("DELETE", f"/api/integrations/{iid}")
check("integration deleted", st == 200)
st, _ = admin.call("GET", f"/api/episodes/{ep4['id']}/download")
check("download blocked before approval", st == 400, st)
import io, shutil, subprocess, tempfile, time, zipfile
ffm = shutil.which("ffmpeg")
if not ffm:
    try:
        import imageio_ffmpeg
        ffm = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        ffm = None
video = b"\x00\x00fakevideo"
if ffm:  # настоящий короткий ролик 540x960, 25 кадров/с: архив должен перекодировать его в 1080x1920, 60 кадров/с
    tmpv = Path(tempfile.mkdtemp()) / "src.mp4"
    subprocess.run([ffm, "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=540x960:rate=25:duration=1",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=1", "-shortest", "-pix_fmt", "yuv420p", str(tmpv)], check=True)
    video = tmpv.read_bytes()
st, up = admin.call("POST", f"/api/shots/{s4['id']}/upload-take", form={"video": ("a.mp4", video)})
check("manual video uploaded", st == 200 and up["selected_take_id"], st)
editor.call("PUT", f"/api/shots/{s4['id']}", {"engine": "veo"})
st, r = editor.call("POST", f"/api/episodes/{ep4['id']}/generate", {"mode": "all"})
check("episode generate skips shots with an uploaded video", st == 200 and r["started"] == 0 and r["uploaded"] == 1, (st, r))
st, sh = admin.call("GET", f"/api/episodes/{ep4['id']}")
check("uploaded shot got no new take", len(sh["shots"][0]["takes"]) == 1, [t["source"] for t in sh["shots"][0]["takes"]])
st, _ = writer.call("POST", f"/api/episodes/{ep4['id']}/approve-version", {"version": 1})
check("writer cannot approve a version", st == 403, st)
st, _ = admin.call("POST", f"/api/episodes/{ep4['id']}/approve-version", {"version": 5})
check("version without video cannot be approved", st == 400, st)
st, ep4a = admin.call("POST", f"/api/episodes/{ep4['id']}/approve-version", {"version": 1})
check("version v1 approved", st == 200 and ep4a["approved_version"] == 1, (st, ep4a.get("approved_version")))
for _ in range(120):
    st, ep4a = admin.call("GET", f"/api/episodes/{ep4['id']}")
    if ep4a["export_status"] != "building":
        break
    time.sleep(0.5)
if ffm:
    check("archive built", ep4a["export_status"] == "ready", (ep4a["export_status"], ep4a["export_error"]))
    req = urllib.request.Request(BASE + f"/api/episodes/{ep4['id']}/download")
    with admin.op.open(req) as resp:
        z = zipfile.ZipFile(io.BytesIO(resp.read()))
    folder = f"{ep4a['number']:02d}" if ep4a["number"] is not None else f"id{ep4['id']}"
    check("archive: episode number / title / shot number", z.namelist() == [f"{folder}/referensy/referensy_01.mp4"], z.namelist())
    out = Path(tempfile.mkdtemp()) / "o.mp4"
    out.write_bytes(z.read(z.namelist()[0]))
    info = subprocess.run([ffm, "-i", str(out)], capture_output=True, text=True).stderr
    check("archive video is 1080x1920 at 60 fps", "1080x1920" in info and "60 fps" in info, info[-400:])
else:
    print("  (ffmpeg не найден: проверка архива пропущена)")

# длина клипа по нейросети
st, r = editor.call("PUT", f"/api/shots/{s4['id']}", {"engine": "runway"})
check("runway: duration snapped to 5/8/10", st == 200 and r["duration"] in (5, 8, 10), (st, r.get("duration")))
st, _ = editor.call("PUT", f"/api/shots/{s4['id']}", {"duration": 6})
check("runway: 6 s rejected", st == 400, st)
st, r = editor.call("PUT", f"/api/shots/{s4['id']}", {"engine": "kling", "duration": 13})
check("kling 3 omni: 13 s accepted", st == 200 and r["duration"] == 13, (st, r.get("duration")))
st, _ = editor.call("PUT", f"/api/shots/{s4['id']}", {"engine": "sora"})
check("sora is retired", st == 400, st)
st, r = editor.call("PUT", f"/api/shots/{s4['id']}", {"engine": "veo"})
check("back to veo: duration fits 4/6/8", st == 200 and r["duration"] in (4, 6, 8), (st, r.get("duration")))
st, meta = editor.call("GET", "/api/meta")
check("meta: engines carry durations and notes", all(e["durations"] and e["duration_note"] for e in meta["engines"]), meta["engines"])

# свои события календаря: цвет, список, удаление
st, _ = writer.call("POST", "/api/calendar-events", {"date": "2030-05-01", "title": "Съёмка", "color": "#e0457b", "end_date": "2030-05-03"})
st, evs = writer.call("GET", "/api/calendar-events")
ev = next((e for e in evs if e["title"] == "Съёмка"), None) if st == 200 else None
check("calendar event saved with color", ev and ev["color"] == "#e0457b" and ev["end_date"] == "2030-05-03", evs)
st, sched = writer.call("GET", "/api/schedule?start=2030-05-01&end=2030-05-03")
check("schedule events carry color", st == 200 and [e["color"] for e in sched["events"] if not e["builtin"]] == ["#e0457b"] * 3, sched.get("events"))
writer.call("PUT", f"/api/calendar-events/{ev['id']}", {"date": "2030-05-01", "title": "Съёмка", "color": "javascript:x"})
st, evs = writer.call("GET", "/api/calendar-events")
check("bad color ignored", next(e for e in evs if e["id"] == ev["id"])["color"] == "", evs)
st, _ = writer.call("DELETE", f"/api/calendar-events/{ev['id']}")
st, evs = writer.call("GET", "/api/calendar-events")
check("calendar event deleted", all(e["id"] != ev["id"] for e in evs), evs)

# лимит «не влезает» зависит от нейросети шота
st, longp = writer.call("POST", "/api/episodes", {"title": "Длинный шот", "script": "Шот 1\nЛОКАЦИЯ: @Лофт\nМаша идёт по комнате, останавливается у окна, долго смотрит на улицу, потом медленно возвращается к дивану и садится, не отводя взгляда от двери.\n"})
ls = longp["shots"][0]
st, r = admin.call("PUT", f"/api/shots/{ls['id']}", {"engine": "veo"})
veo_max = r["max_s"]
st, r = admin.call("PUT", f"/api/shots/{ls['id']}", {"engine": "kling"})
check("per-engine limit: kling shot limit is 15 s, veo 8 s", st == 200 and r["max_s"] == 15 and veo_max == 8, (st, r.get("max_s"), veo_max))
check("kling limit: est within 15 s is not over", r["over_limit"] == (r["est_seconds"] > 15), (r["est_seconds"], r["over_limit"]))

# нейросети Magnific: ссылки на героев и локации в промпте и запрос — по правилам каждой модели
st, meta = editor.call("GET", "/api/meta")
check("meta: generation starts as a stub", meta["gen_mode"] == "stub" and meta["veo_route"] == "magnific", (meta.get("gen_mode"), meta.get("veo_route")))
check("meta: engines carry ref rules and tips", all("refs_note" in e and "tips" in e for e in meta["engines"]), meta["engines"])
admin.call("PUT", f"/api/shots/{s4['id']}", {"prompt_locked": False})
st, r = admin.call("PUT", f"/api/shots/{s4['id']}", {"engine": "seedance"})
check("seedance: @Image tokens in prompt", st == 200 and "Character @Image1" in r["prompt"] and "shown in @Image2" in r["prompt"], r.get("prompt"))
st, q = admin.call("GET", f"/api/shots/{s4['id']}/request")
check("seedance: request to seedance-2-pro with reference_images", q["url"].endswith("/v1/ai/video/seedance-2-pro-720p")
      and len(q["body"]["reference_images"]) == 2 and q["body"]["aspect_ratio"] == "social_story_9_16", q)
st, r = admin.call("PUT", f"/api/shots/{s4['id']}", {"engine": "wan"})
check("wan: Image N labels, location only in words", "Character Image 1" in r["prompt"] and [x["kind"] for x in r["references"]] == ["character"]
      and any("только персонажей" in w for w in r["warnings"]), (r.get("prompt"), r.get("warnings")))
st, r = admin.call("PUT", f"/api/shots/{s4['id']}", {"engine": "runway"})
check("runway: no references, warned", r["references"] == [] and any("не принимает референсы" in w for w in r["warnings"]), r.get("warnings"))
st, r = admin.call("PUT", f"/api/shots/{s4['id']}", {"engine": "veo"})
check("veo: natural-language references", "the character from reference image 1" in r["prompt"] and r["duration"] == 8, r.get("prompt"))
st, r = admin.call("PUT", f"/api/shots/{s4['id']}", {"engine": "kling"})
check("kling: @Image tokens", "Character @Image1" in r["prompt"], r.get("prompt"))
st, q = admin.call("GET", f"/api/shots/{s4['id']}/request")
check("kling without public address: explained", "ссылкой" in q.get("warning", "") and any("ссылкой" in w for w in r["warnings"]), q)
admin.call("PUT", "/api/settings", {"public_base_url": BASE})
st, q = admin.call("GET", f"/api/shots/{s4['id']}/request")
urls = q.get("body", {}).get("image_urls", [])
check("kling: images as signed public links", q.get("url", "").endswith("/kling-v3-omni-pro") and len(urls) == 2
      and all(u.startswith(BASE + "/pub/") for u in urls), q)
if urls:
    with urllib.request.urlopen(urls[0]) as resp:
        check("signed link opens without login", resp.status == 200 and resp.read() == PNG, resp.status)
    bad = urls[0].replace("/pub/", "/pub/0")
    try:
        urllib.request.urlopen(bad)
        check("tampered link rejected", False)
    except urllib.error.HTTPError as e:
        check("tampered link rejected", e.code == 404, e.code)
admin.call("PUT", "/api/settings", {"public_base_url": ""})
st, sset = admin.call("GET", "/api/settings")
check("media secret not exposed", "media_secret" not in sset, list(sset))

# описание поста по соцсетям
st, e0 = writer.call("PUT", f"/api/episodes/{ep4['id']}?reparse=false", {"post_text": "общее описание"})
check("post: existing text is kept, same-for-all by default", st == 200 and e0["post_text"] == "общее описание" and e0["post_same"] == 1, e0.get("post_same"))
st, e1 = writer.call("PUT", f"/api/episodes/{ep4['id']}?reparse=false", {"post_same": False, "post_text_tiktok": "для тикток", "post_text_reels": "для рилс"})
check("post: per-network texts saved", st == 200 and e1["post_same"] == 0 and e1["post_text_tiktok"] == "для тикток" and e1["post_text_reels"] == "для рилс" and e1["post_text"] == "общее описание", e1.get("post_same"))
st, _ = editor.call("PUT", f"/api/episodes/{ep4['id']}?reparse=false", {"post_text_shorts": "x"})
check("post: editor cannot write descriptions", st == 403, st)

# --- «Сообщить о проблеме»: заявки видит только админ, снимок не публичный ---
st, r = writer.call("POST", "/api/reports", form={"kind": "bug", "text": "Не сохраняется синопсис", "page": "#/series", "viewport": "1440×900",
                                                  "image": ("s.png", PNG)})
check("report: writer sends a report with screenshot", st == 200 and r.get("id"), (st, r))
st, _ = writer.call("POST", "/api/reports", form={"kind": "bug", "text": "  "})
check("report: empty text rejected", st == 400, st)
st, _ = writer.call("GET", "/api/reports")
check("report: writer cannot read reports", st == 403, st)
st, rl = admin.call("GET", "/api/reports")
mine = next((x for x in rl["reports"] if x["id"] == r["id"]), {}) if st == 200 else {}
check("report: admin sees it with author and screenshot", mine.get("author") == "wr" and mine.get("has_shot") and mine.get("shot_path") is None, mine)
st, _ = writer.call("GET", f"/api/reports/{r['id']}/shot")
check("report: screenshot closed for writer", st == 403, st)
st, _ = admin.call("PUT", f"/api/reports/{r['id']}", {"status": "rejected"})
check("report: admin changes status", st == 200, st)
st, b = admin.call("GET", "/api/badges")
check("badges: admin sees new reports count", st == 200 and "reports" in b, b)

# --- срочная серия ---
st, ue = writer.call("POST", "/api/episodes", {"title": "Срочная", "synopsis": "к событию", "urgent": True})
check("urgent: writer creates urgent episode", st == 200 and ue["urgent"] == 1, (st, ue.get("urgent") if st == 200 else ue))
st, b = editor.call("GET", "/api/badges")
check("urgent: badge counts it", st == 200 and b["urgent"] >= 1, b)
st, prod = editor.call("GET", "/api/production")
check("urgent: production carries the flag", any(e["id"] == ue["id"] and e["urgent"] for e in prod))
# срочная на конкретный день: встаёт на него и закрепляется, остальные сдвигаются (одна серия в день)
def day_of(i):
    return next(e["date"] for e in admin.call("GET", "/api/episodes")[1] if e["id"] == i)
q = [writer.call("POST", "/api/episodes", {"title": f"Очередь {i}"})[1]["id"] for i in range(3)]
d1, d2 = day_of(q[1]), day_of(q[2])
st, u2 = writer.call("POST", "/api/episodes", {"title": "Срочная к событию", "urgent": True, "date": d1})
check("urgent day: takes the chosen day, pinned", st == 200 and day_of(u2["id"]) == d1 and u2["pinned"] == 1, (st, d1, day_of(u2["id"]) if st == 200 else u2))
check("urgent day: the episode of that day moved one day later", day_of(q[1]) == d2, (day_of(q[1]), d2))
check("urgent day: one episode per day", len({day_of(i) for i in [*q, u2["id"]]}) == 4, [day_of(i) for i in [*q, u2["id"]]])
st, u3 = writer.call("POST", "/api/episodes", {"title": "Ещё срочная", "urgent": True})
st, _ = writer.call("POST", f"/api/episodes/{u3['id']}/move", {"date": d1, "pin": True})
check("urgent day: wins a day taken by a pinned episode", st == 200 and day_of(u3["id"]) == d1 and day_of(u2["id"]) != d1, (st, day_of(u3["id"]), day_of(u2["id"])))

st, ue2 = writer.call("PUT", f"/api/episodes/{ue['id']}?reparse=false", {"urgent": False})
check("urgent: writer unchecks it", st == 200 and ue2["urgent"] == 0, st)

# --- отмена генерации и уведомление «серия сгенерировалась» нейронщику ---
admin.call("POST", f"/api/episodes/{eid}/status", {"status": "approved", "force": True})  # серия на этапе нейронщика
for _ in range(30):  # дождаться, пока закончатся прошлые генерации этой серии
    st, sh = editor.call("GET", f"/api/episodes/{eid}")
    if not any(x["gen_status"] in ("queued", "running") for x in sh["shots"]):
        break
    time.sleep(1)
s1r = next(x for x in sh["shots"] if x["ready"])
st, gs = editor.call("POST", f"/api/shots/{s1r['id']}/generate", {"reason": "отмена"})
tk = gs["takes"][-1] if st == 200 else {}
st, c1 = editor.call("POST", f"/api/takes/{tk.get('id')}/cancel", {})
check("cancel: queued take is cancelled", st == 200 and c1["takes"][-1]["status"] == "cancelled", (st, c1 if st != 200 else c1["takes"][-1]))
st, _ = editor.call("POST", f"/api/takes/{tk.get('id')}/cancel", {})
check("cancel: second cancel refused", st == 400, st)
st, _ = writer.call("POST", f"/api/takes/{tk.get('id')}/cancel", {})
check("cancel: writer cannot cancel", st == 403, st)
st, gen = editor.call("POST", f"/api/episodes/{eid}/generate", {"mode": "all"})
check("generate: unready shots listed, not sent", st == 200 and "unready" in gen, gen)
time.sleep(4)
st, b = editor.call("GET", "/api/badges")
note = next((n for n in b.get("notifications", []) if n["episode_id"] == eid), None)
check("notify: editor gets «серия сгенерировалась»", note is not None, b)
st, bw = writer.call("GET", "/api/badges")
check("notify: writer does not get it", not bw.get("notifications"), bw)
editor.call("POST", "/api/notifications/seen", {"episode_id": eid})
st, b = editor.call("GET", "/api/badges")
check("notify: opened episode clears it", not any(n["episode_id"] == eid for n in b["notifications"]), b)

print("\nFAILED: " + ", ".join(fails) if fails else "\nALL PASSED")
sys.exit(1 if fails else 0)
