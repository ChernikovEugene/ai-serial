"""Проверка пути «Gemini Omni 1.1»: кадры Nano Banana 2.1, состояния версий, баланс кредитов, выбор нейросети.

Сам запускает студию на чистой папке данных и поддельный Magnific (никуда не ходит, ничего не тратит):
    python tests/smoke_omni.py
"""
import base64
import http.cookiejar
import http.server
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PORT_STUDIO, PORT_FAKE = 8767, 8768
BASE = f"http://127.0.0.1:{PORT_STUDIO}"
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
MP4 = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom" + b"\x00" * 64
fails = []
DATA = Path(tempfile.gettempdir())   # папка данных проверяемой студии: задаётся в main()


# ---------- поддельный Magnific ----------

class Fake(http.server.BaseHTTPRequestHandler):
    log: list = []          # (метод, путь, тело)
    fail_nano = False
    omni_missing = False    # адрес по умолчанию для Omni отвечает 404, живёт только «-720p»

    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json"):
        raw = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        Fake.log.append(("POST", self.path, body, self.headers.get("x-magnific-api-key")))
        if self.path.startswith("/v1/ai/text-to-image/nano-banana-2-1") and Fake.fail_nano:
            return self._send(400, {"message": "Validation error"})
        if Fake.omni_missing and self.path.startswith("/v1/ai/video/gemini-omni") and self.path != "/v1/ai/video/gemini-omni-1-1-720p":
            return self._send(404, {"message": "Not found"})
        if self.path.startswith("/v1/ai/text-to-image/") or self.path.startswith("/v1/ai/video/"):
            return self._send(200, {"data": {"task_id": str(uuid.uuid4()), "status": "IN_PROGRESS", "generated": []}})
        self._send(404, {"message": "nope"})

    def do_GET(self):
        Fake.log.append(("GET", self.path, None, self.headers.get("x-magnific-api-key")))
        if self.path.startswith("/v1/ai/text-to-image/nano-banana-2-1/"):
            return self._send(200, {"data": {"task_id": "x", "status": "COMPLETED", "generated": [f"http://127.0.0.1:{PORT_FAKE}/files/pic.png"]}})
        if self.path.startswith("/v1/ai/video/gemini-omni-1-1/") or self.path.startswith("/v1/ai/video/gemini-omni-1-1-720p/"):
            return self._send(200, {"data": {"task_id": "x", "status": "COMPLETED", "generated": [f"http://127.0.0.1:{PORT_FAKE}/files/clip.mp4"]}})
        if self.path == "/files/pic.png":
            return self._send(200, PNG, "image/png")
        if self.path == "/files/clip.mp4":
            return self._send(200, MP4, "video/mp4")
        self._send(404, {"message": "not found"})


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
                files = v if isinstance(v, list) else [v]
                for item in files:
                    if isinstance(item, tuple):
                        data += (f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"; filename="{item[0]}"\r\n'
                                 f"Content-Type: image/png\r\n\r\n").encode() + item[1] + b"\r\n"
                    else:
                        data += f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{item}\r\n'.encode()
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


def wait_for(fn, seconds=25):
    end = time.time() + seconds
    while time.time() < end:
        v = fn()
        if v:
            return v
        time.sleep(0.4)
    return None


def main():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", PORT_FAKE), Fake)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    global DATA
    data_dir = tempfile.mkdtemp(prefix="studio-omni-")
    DATA = Path(data_dir)
    env = {**os.environ, "STUDIO_DATA_DIR": data_dir, "MAGNIFIC_API_BASE": f"http://127.0.0.1:{PORT_FAKE}"}
    env.pop("STUDIO_LEGACY_ENGINES", None)
    studio = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(PORT_STUDIO)], cwd=ROOT, env=env,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        wait_for(lambda: _up(), 25)
        run()
    finally:
        studio.terminate()
        studio.wait(10)
        shutil.rmtree(data_dir, ignore_errors=True)
        srv.shutdown()
    print("\nFAILED: " + ", ".join(fails) if fails else "\nALL PASSED")
    sys.exit(1 if fails else 0)


def _up():
    try:
        return urllib.request.urlopen(BASE + "/api/auth/state").status == 200
    except OSError:
        return False


def run():
    admin = Client()
    st, _ = admin.call("POST", "/api/auth/setup", {"login": "boss", "password": "pass1234", "name": "Продюсер"})
    check("admin setup", st == 200, st)
    ed = Client()
    admin.call("POST", "/api/users", {"login": "ed", "password": "pass1234", "name": "ed", "role": "editor"})
    ed.call("POST", "/api/auth/login", {"login": "ed", "password": "pass1234"})

    # --- нейросети: остались только Gemini Omni и Рендерферма ---
    st, meta = admin.call("GET", "/api/meta")
    shown = [e["key"] for e in meta["engines"] if not e["legacy"]]
    check("only Gemini Omni and the render farm can be picked", sorted(shown) == ["gemini_omni", "renderfarm"], shown)
    check("Gemini Omni is the default", meta["default_engine"] == "gemini_omni")
    omni = next(e for e in meta["engines"] if e["key"] == "gemini_omni")
    check("Gemini Omni: 3 or 5 s", omni["durations"] == [3, 5], omni["durations"])

    # --- библиотека: версия, состояния с названиями картинок ---
    st, maria = admin.call("POST", "/api/assets", form={"kind": "character", "name": "Маша", "description": "девушка", "images": ("m.png", PNG)})
    st, cafe = admin.call("POST", "/api/assets", form={"kind": "location", "name": "Кафе", "description": "кафе", "images": ("c.png", PNG)})
    check("library: character and location created", st == 200 and cafe["versions"][0]["images"], st)
    vid = maria["versions"][0]["id"]
    check("version has no states by default", maria["versions"][0]["states"] == [])
    st, a = admin.call("POST", f"/api/assets/{maria['id']}/versions/{vid}/states",
                       form={"name": "плачет", "names": json.dumps(["крупно", "в полный рост"]), "images": [("a.png", PNG), ("b.png", PNG)]})
    states = a["versions"][0]["states"]
    check("state created with named images", st == 200 and len(states) == 1 and [i["name"] for i in states[0]["images"]] == ["крупно", "в полный рост"], a)
    st, _ = admin.call("POST", f"/api/assets/{maria['id']}/versions/{vid}/states", form={"name": "ПЛАЧЕТ"})
    check("duplicate state name rejected", st == 400, st)
    sid_state = states[0]["id"]
    st, a = admin.call("PUT", f"/api/assets/{maria['id']}/versions/{vid}/states/{sid_state}",
                       {"name": "рыдает", "images": [{"path": states[0]["images"][1]["path"], "name": "общий план"}]})
    st_ = a["versions"][0]["states"][0]
    check("state renamed, one image left and renamed", st_["name"] == "рыдает" and [i["name"] for i in st_["images"]] == ["общий план"], st_)
    check("removed state image file is deleted from disk", not (DATA / "media" / states[0]["images"][0]["path"]).exists())
    st, _ = admin.call("PUT", f"/api/assets/{maria['id']}/versions/{vid}/states/{sid_state}", {"images": [{"path": "characters/1/foreign.png", "name": "x"}]})
    check("new image paths are not accepted through rename", st == 400, st)
    st, a = admin.call("POST", f"/api/assets/{maria['id']}/versions/{vid}/states/{sid_state}/images", form={"images": ("c.png", PNG)})
    check("image added to state", st == 200 and len(a["versions"][0]["states"][0]["images"]) == 2, a)
    pool = a["versions"][0]["images"]
    if pool:
        st, a2 = admin.call("PUT", f"/api/assets/{maria['id']}/versions/{vid}/image-names", {"names": {pool[0]: "анфас"}})
        check("pool photo gets a short name", st == 200 and a2["versions"][0]["image_names"].get(pool[0]) == "анфас", a2)
        cur = a2["versions"][0]["states"][0]["images"]
        st, a2 = admin.call("PUT", f"/api/assets/{maria['id']}/versions/{vid}/states/{sid_state}", {"images": cur + [{"path": pool[0], "name": "анфас"}]})
        check("pool photo can be put into a state", st == 200 and any(i["path"] == pool[0] for i in a2["versions"][0]["states"][0]["images"]), a2)
        st, a2 = admin.call("PUT", f"/api/assets/{maria['id']}/versions/{vid}/states/{sid_state}", {"images": cur})
        check("pool photo removed from state stays in pool", st == 200 and (DATA / "media" / pool[0]).exists() and pool[0] in a2["versions"][0]["images"], a2)
    st, _ = ed.call("POST", f"/api/assets/{maria['id']}/versions/{vid}/states", form={"name": "x"})
    check("editor cannot add states", st == 403, st)

    # --- серия и шот ---
    script = "Шот 1\nИНТ. КАФЕ — ДЕНЬ\n@Маша заходит в кафе и оглядывается.\nМАША: Привет!\n"
    st, ep = admin.call("POST", "/api/episodes", {"title": "Тест Omni", "script": script})
    check("episode created", st == 200 and len(ep["shots"]) == 1, st)
    eid, shot = ep["id"], ep["shots"][0]
    sid = shot["id"]
    check("new shot uses Gemini Omni", shot["engine"] == "gemini_omni", shot["engine"])
    check("duration snaps to 3 or 5", shot["duration"] in (3, 5), shot["duration"])
    check("omni prompt has no reference tokens", "reference image" not in shot["prompt"] and "start frame" in shot["prompt"], shot["prompt"])
    check("shot has two empty frames", set(shot["frames"]) == {"start", "end"} and not shot["frames_ready"], shot["frames"])
    default_refs = shot["frames"]["start"]["refs"]
    check("frame refs default to the main images of the character and location",
          [r["kind"] for r in default_refs] == ["character", "location"] and default_refs[0]["name"].startswith("Маша"), default_refs)
    st, r = admin.call("PUT", f"/api/shots/{sid}", {"duration": 4})
    check("duration outside 3/5 rejected", st == 400, st)
    st, r = admin.call("PUT", f"/api/shots/{sid}", {"duration": 3})
    check("duration 3 accepted", st == 200 and r["duration"] == 3, st)
    st, _ = admin.call("PUT", f"/api/shots/{sid}", {"engine": "kling"})
    check("removed engines cannot be picked", st == 400, st)

    # --- кадры ---
    admin.call("PUT", "/api/settings", {"gen_mode": "live", "magnific_api_key": "test-key"})
    st, r = admin.call("POST", f"/api/shots/{sid}/frames/start/generate", {})
    check("frame without a prompt is rejected", st == 400, (st, r))
    st, r = admin.call("POST", f"/api/shots/{sid}/generate", {})
    check("video is refused until both frames are ready", st == 400 and "кадры" in json.dumps(r, ensure_ascii=False).lower(), (st, r))
    st, r = admin.call("POST", f"/api/shots/{sid}/frames/start/generate", {"prompt": "Image 1 sits at the table of Image 2, crying"})
    check("start frame is queued", st == 200 and r["frames"]["start"]["status"] in ("queued", "running", "done"), (st, r))
    done = wait_for(lambda: next((s for s in admin.call("GET", f"/api/episodes/{eid}")[1]["shots"]
                                  if s["id"] == sid and s["frames"]["start"]["status"] == "done"), None))
    check("start frame is drawn and saved", bool(done) and done["frames"]["start"]["ready"], done and done["frames"]["start"])
    nano = [x for x in Fake.log if x[0] == "POST" and x[1] == "/v1/ai/text-to-image/nano-banana-2-1"]
    body = nano[0][2] if nano else {}
    check("Nano Banana 2.1 request: 1K, 9:16, prompt, key", body.get("resolution") == "1K" and body.get("aspect_ratio") == "9:16"
          and "crying" in body.get("prompt", "") and nano[0][3] == "test-key", body)
    check("Nano Banana 2.1 request carries the library images as base64 with captions",
          len(body.get("reference_images", [])) == 2 and body["reference_images"][0]["image"] == base64.b64encode(PNG).decode()
          and body["reference_images"][0]["text"].startswith("Image 1: Маша"), body.get("reference_images"))
    check("video is still refused with one frame", admin.call("POST", f"/api/shots/{sid}/generate", {})[0] == 400)

    # референсы: состояние персонажа + своя картинка; потом конец
    state_img = a["versions"][0]["states"][0]["images"][0]["path"]
    st, r = admin.call("PUT", f"/api/shots/{sid}/frames/end", {"prompt": "Same room, she smiles", "refs": [
        {"path": state_img, "name": "Маша · рыдает · общий план", "kind": "character"},
        {"path": "../../etc/passwd", "name": "bad", "kind": "upload"},
        {"path": "characters/1/none.png", "name": "missing", "kind": "upload"}]})
    refs = r["frames"]["end"]["refs"]
    check("chosen refs saved, bad paths dropped", [x["path"] for x in refs] == [state_img] and not r["frames"]["end"]["auto_refs"], refs)
    st, r = admin.call("POST", f"/api/shots/{sid}/frames/end/upload", form={"images": ("mine.png", PNG)})
    refs = r["frames"]["end"]["refs"]
    check("own image added as a reference", st == 200 and len(refs) == 2 and refs[1]["kind"] == "upload" and refs[1]["name"] == "mine", refs)
    up_path = refs[1]["path"]
    start_path = done["frames"]["start"]["path"]
    st, r = admin.call("PUT", f"/api/shots/{sid}/frames/end", {"refs": [refs[0], {"path": start_path, "name": "старт", "kind": "frame"}]})
    check("start frame can be a reference for the end frame", [x["path"] for x in r["frames"]["end"]["refs"]] == [state_img, start_path], r["frames"]["end"]["refs"])
    check("removed own upload is deleted from disk", not (DATA / "media" / up_path).exists())
    st, r = admin.call("POST", f"/api/shots/{sid}/frames/end/generate", {})
    done = wait_for(lambda: next((s for s in admin.call("GET", f"/api/episodes/{eid}")[1]["shots"]
                                  if s["id"] == sid and s["frames_ready"]), None))
    check("both frames ready", bool(done))
    nano = [x for x in Fake.log if x[0] == "POST" and x[1] == "/v1/ai/text-to-image/nano-banana-2-1"]
    check("end frame request used the chosen refs", len(nano) == 2 and len(nano[1][2]["reference_images"]) == 2
          and nano[1][2]["prompt"] == "Same room, she smiles", nano[-1][2].get("reference_images"))
    st, r = admin.call("GET", f"/api/shots/{sid}/request")
    check("video request preview shows both frames", st == 200 and "start_image_url" in r["body"] and "end_image_url" in r["body"], r)

    # --- видео ---
    st, r = admin.call("POST", f"/api/shots/{sid}/generate", {"reason": "тест"})
    check("video generation starts when frames are ready", st == 200, (st, r))
    take = wait_for(lambda: next((t for s in admin.call("GET", f"/api/episodes/{eid}")[1]["shots"] if s["id"] == sid
                                  for t in s["takes"] if t["status"] in ("done", "error")), None))
    check("take finished", bool(take) and take["status"] == "done", take)
    omni_posts = [x for x in Fake.log if x[0] == "POST" and x[1] == "/v1/ai/video/gemini-omni-1-1"]
    ob = omni_posts[0][2] if omni_posts else {}
    check("Gemini Omni request: 720p, 9:16, 3 s, start and end frames", ob.get("resolution") == "720p" and ob.get("aspect_ratio") == "9:16"
          and ob.get("duration") == 3 and ob.get("start_image_url") and ob.get("end_image_url") and "start frame" in ob.get("prompt", ""), {k: str(v)[:60] for k, v in ob.items()})
    # адрес Omni не угадан с первого раза: студия перебирает известные и запоминает рабочий
    admin.call("PUT", "/api/settings", {"omni_path": ""})
    Fake.omni_missing = True
    st, r = admin.call("POST", f"/api/shots/{sid}/generate", {"reason": "перебор адреса"})
    take2 = wait_for(lambda: next((t for s in admin.call("GET", f"/api/episodes/{eid}")[1]["shots"] if s["id"] == sid
                                   for t in s["takes"] if t["id"] != take["id"] and t["status"] in ("done", "error")), None))
    st, cfg = admin.call("GET", "/api/settings")
    Fake.omni_missing = False
    check("Omni address is found among candidates and remembered", bool(take2) and take2["status"] == "done" and cfg.get("omni_path") == "/v1/ai/video/gemini-omni-1-1-720p", (take2, cfg.get("omni_path")))
    admin.call("PUT", "/api/settings", {"omni_path": ""})
    check("video file saved", take and (DATA / "media" / take["video_path"]).is_file(), take)

    # --- пропуск без кадров в «сгенерировать серию» ---
    st, ep2 = admin.call("POST", "/api/episodes", {"title": "Без кадров", "script": script})
    admin.call("POST", f"/api/episodes/{ep2['id']}/status", {"status": "approved", "force": True})
    st, r = admin.call("POST", f"/api/episodes/{ep2['id']}/generate", {"mode": "all", "force": True})
    check("episode generation does not send omni shots without frames, even forced", st == 200 and r["started"] == 0 and r["unready"], r)

    # --- старая нейросеть в базе открывается как Gemini Omni ---
    import sqlite3
    con = sqlite3.connect(DATA / "studio.db")
    con.execute("UPDATE shots SET engine='veo', duration=8 WHERE id=?", (ep2["shots"][0]["id"],))
    con.commit()
    con.close()
    st, ep3 = admin.call("GET", f"/api/episodes/{ep2['id']}")
    s3 = ep3["shots"][0]
    check("a shot saved with an old engine opens as Gemini Omni with a valid length", s3["engine"] == "gemini_omni" and s3["duration"] in (3, 5), (s3["engine"], s3["duration"]))

    # --- кадр с ошибкой Magnific ---
    Fake.fail_nano = True
    admin.call("PUT", f"/api/shots/{s3['id']}/frames/start", {"prompt": "x y z"})
    admin.call("POST", f"/api/shots/{s3['id']}/frames/start/generate", {})
    err = wait_for(lambda: next((s for s in admin.call("GET", f"/api/episodes/{ep2['id']}")[1]["shots"]
                                 if s["id"] == s3["id"] and s["frames"]["start"]["status"] == "error"), None))
    check("Magnific error is shown on the frame", bool(err) and "Validation error" in err["frames"]["start"]["error"], err and err["frames"]["start"])
    Fake.fail_nano = False
    st, r = admin.call("DELETE", f"/api/shots/{s3['id']}/frames/start")
    check("frame can be reset", st == 200 and r["frames"]["start"]["status"] == "" and r["frames"]["start"]["prompt"] == "x y z", r["frames"]["start"])

    # --- готовая картинка вместо кадра ---
    st, r = admin.call("POST", f"/api/shots/{s3['id']}/frames/end/image", form={"image": ("mine.png", PNG)})
    fe = r["frames"]["end"] if st == 200 else {}
    check("own image makes the frame ready", st == 200 and fe.get("ready") and fe.get("own") and (DATA / "media" / fe["path"]).exists(), r)
    st, r = admin.call("DELETE", f"/api/shots/{s3['id']}/frames/end")
    check("own image can be removed", st == 200 and not r["frames"]["end"]["ready"] and not (DATA / "media" / fe["path"]).exists(), r)

    # --- удаление версии убирает картинки состояний ---
    st, a = admin.call("POST", f"/api/assets/{maria['id']}/versions", form={"label": "Зима", "images": ("w.png", PNG)})
    old_state_files = [i["path"] for i in maria_states(admin, maria["id"], vid)]
    st, a = admin.call("POST", f"/api/assets/{maria['id']}/versions/{a['active_version_id']}/activate", {})
    st, a = admin.call("DELETE", f"/api/assets/{maria['id']}/versions/{vid}")
    check("deleting a version removes its states and files", st == 200 and all(not (DATA / "media" / p).exists() for p in old_state_files), (st, a, old_state_files))


def maria_states(admin, aid, vid):
    st, assets = admin.call("GET", "/api/assets")
    a = next(x for x in assets if x["id"] == aid)
    v = next(x for x in a["versions"] if x["id"] == vid)
    return [i for s in v["states"] for i in s["images"]]


if __name__ == "__main__":
    main()
