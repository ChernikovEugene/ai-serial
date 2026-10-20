"""Video generation. Every run creates a take (дубль) for the shot.
Where the shot goes is decided by models.route(): `stub` writes the exact request to disk and calls nothing;
`magnific` sends it to Magnific API (all models, see app/models.py); `google` calls the Gemini API (Veo 3 / 3.1);
`renderfarm` goes to the studio render farm through app/farm.py (the farm hub fetches the shots itself)."""
import base64
import json
import mimetypes
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from . import db, models

API_BASE = "https://generativelanguage.googleapis.com/v1beta"
OUTPUT_DIR = db.MEDIA_DIR / "renders"
_pool: ThreadPoolExecutor | None = None


def pool() -> ThreadPoolExecutor:
    global _pool
    if _pool is None:
        n = max(1, min(8, int(db.get_settings().get("veo_parallel") or 2)))
        _pool = ThreadPoolExecutor(max_workers=n, thread_name_prefix="veo")
    return _pool


def _image(rel: str, inline: bool) -> dict:
    path = db.MEDIA_DIR / rel
    mime = mimetypes.guess_type(path.name)[0] or "image/png"
    data = base64.b64encode(path.read_bytes()).decode() if inline else f"<{rel}>"
    return {"inlineData": {"mimeType": mime, "data": data}}


def build_request(shot: dict, refs: list[dict], settings: dict, inline_images: bool) -> dict:
    instance = {"prompt": shot["prompt"]}
    params = {
        "aspectRatio": settings.get("aspect_ratio", "9:16"),
        "durationSeconds": int(shot["duration"]),
        "resolution": settings.get("resolution", "720p"),
    }
    neg = shot.get("negative_prompt") or settings.get("negative_prompt")
    if neg:
        params["negativePrompt"] = neg
    if shot.get("composition_image") and shot.get("composition_mode") == "first_frame":
        instance["image"] = _image(shot["composition_image"], inline_images)
    elif refs and "3.1" in settings.get("veo_model", ""):
        instance["referenceImages"] = [{"image": _image(r["path"], inline_images), "referenceType": "asset"} for r in refs]
        params["durationSeconds"] = 8
    return {"instances": [instance], "parameters": params}


def _http(method: str, url: str, key: str, body: dict | None = None, raw=False):
    req = urllib.request.Request(url, method=method, headers={"x-goog-api-key": key, "Content-Type": "application/json"},
                                 data=json.dumps(body).encode() if body is not None else None)
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            data = r.read()
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code}: {e.read().decode(errors='replace')[:800]}") from None
    return data if raw else json.loads(data)


def _cancelled(take_id: int) -> bool:
    with db.connect() as c:
        row = c.execute("SELECT status FROM takes WHERE id=?", (take_id,)).fetchone()
    return not row or row["status"] == "cancelled"


def _remember(take_id: int, remote_id: str, remote_path: str):
    with db.connect() as c:
        c.execute("UPDATE takes SET remote_id=?, remote_path=? WHERE id=?", (remote_id, remote_path, take_id))


def _finish(take_id: int, shot_id: int, status: str, error="", video="", request=""):
    with db.connect() as c:
        if c.execute("SELECT status FROM takes WHERE id=?", (take_id,)).fetchone()["status"] == "cancelled":
            return  # отменённый дубль не оживает, даже если нейросеть всё-таки прислала ролик
        c.execute("UPDATE takes SET status=?, error=?, video_path=COALESCE(NULLIF(?, ''), video_path),"
                  " request_path=COALESCE(NULLIF(?, ''), request_path), finished_at=? WHERE id=?",
                  (status, error, video, request, db.now(), take_id))
        _after_take(c, take_id, shot_id, status)


def _after_take(c, take_id: int, shot_id: int, status: str):
    """Дубль закончился (готов, ошибка или отменён): если в серии больше ничего не генерируется — серия на монтаж
    и уведомление нейронщику «серия сгенерировалась»."""
    if status in ("done", "stub"):
        c.execute("UPDATE shots SET selected_take_id=?, needs_redo=0 WHERE id=?", (take_id, shot_id))
    ep = c.execute("SELECT e.id, e.status FROM shots s JOIN episodes e ON e.id=s.episode_id WHERE s.id=?",
                   (shot_id,)).fetchone()
    busy = c.execute("SELECT COUNT(*) FROM takes t JOIN shots s ON s.id=t.shot_id WHERE s.episode_id=? "
                     "AND t.status IN ('queued','running')", (ep["id"],)).fetchone()[0]
    if not busy and ep["status"] == "generating":
        c.execute("UPDATE episodes SET status='fixes', updated_at=? WHERE id=?", (db.now(), ep["id"]))
        c.execute("INSERT INTO events(episode_id, user_id, text, created_at) VALUES (?,?,?,?)",
                  (ep["id"], None, "Генерация завершена — статус «На правках визуала»", db.now()))
        t = c.execute("SELECT title FROM episodes WHERE id=?", (ep["id"],)).fetchone()["title"] or "Без названия"
        c.execute("INSERT INTO notifications(role, kind, episode_id, text, created_at) VALUES ('editor','generated',?,?,?)",
                  (ep["id"], f"Серия «{t}» сгенерировалась", db.now()))


def _run(take_id: int, shot: dict, refs: list[dict], settings: dict):
    shot_id = shot["id"]
    try:
        if _cancelled(take_id):
            return
        with db.connect() as c:
            c.execute("UPDATE takes SET status='running' WHERE id=? AND status='queued'", (take_id,))
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        base = f"ep{shot['episode_id']}_shot{shot['label'] or shot['idx']}_take{take_id}"
        req_rel = f"renders/{base}.request.json"
        (db.MEDIA_DIR / req_rel).write_text(json.dumps(preview(shot, refs, settings), ensure_ascii=False, indent=2), "utf-8")
        where = models.route(shot.get("engine"), settings)

        if where == "renderfarm":  # шоты фермы готовит farm.start_many, а забирает Пульт: сюда они не попадают
            raise RuntimeError("Шоты рендерфермы отправляются через farm.start_many")
        if where in ("stub", "manual"):
            time.sleep(1.5)
            _finish(take_id, shot_id, "stub", "Заглушка: запрос сохранён, видео не создавалось", request=req_rel)
            return
        if where == "magnific":
            tid, path = models.create(shot, refs, settings)
            _remember(take_id, tid, path)
            _wait_magnific(take_id, shot_id, base, tid, path, settings, req_rel)
            return

        key = settings.get("veo_api_key", "").strip()
        if not key:
            raise RuntimeError("Не указан API-ключ Veo в настройках")
        model = settings.get("veo_model", "veo-3.1-generate-preview")
        op = _http("POST", f"{API_BASE}/models/{model}:predictLongRunning", key,
                   build_request(shot, refs, settings, inline_images=True))
        _remember(take_id, op["name"], "google")
        _wait_google(take_id, shot_id, base, op["name"], key, req_rel)
    except Exception as e:  # noqa: BLE001 - surface any failure in the UI
        _finish(take_id, shot_id, "error", str(e))


def _wait_magnific(take_id, shot_id, base, tid, path, settings, req_rel, minutes=30):
    video = models.wait(tid, path, settings, stop=lambda: _cancelled(take_id), minutes=minutes)
    if video is None:
        return  # отменили
    (OUTPUT_DIR / f"{base}.mp4").write_bytes(video)
    _finish(take_id, shot_id, "done", video=f"renders/{base}.mp4", request=req_rel)


def _wait_google(take_id, shot_id, base, name, key, req_rel):
    op: dict = {}
    deadline = time.time() + 20 * 60
    while not op.get("done"):
        if _cancelled(take_id):
            return
        if time.time() > deadline:
            raise RuntimeError("Veo не ответил за 20 минут")
        if op:
            time.sleep(10)
        op = _http("GET", f"{API_BASE}/{name}", key)
    if "error" in op:
        raise RuntimeError(json.dumps(op["error"], ensure_ascii=False))
    samples = op.get("response", {}).get("generateVideoResponse", {}).get("generatedSamples", [])
    if not samples:
        raise RuntimeError("Veo не вернул видео (возможно, сработал фильтр безопасности): "
                           + json.dumps(op.get("response", {}), ensure_ascii=False)[:500])
    video = _http("GET", samples[0]["video"]["uri"], key, raw=True)
    (OUTPUT_DIR / f"{base}.mp4").write_bytes(video)
    _finish(take_id, shot_id, "done", video=f"renders/{base}.mp4", request=req_rel)


def start(shot: dict, refs: list[dict], settings: dict, user_id: int | None, reason: str = "") -> int:
    """Creates a take that remembers the exact prompt it was generated from and why it was (re)generated."""
    with db.connect() as c:
        n = c.execute("SELECT COALESCE(MAX(take_no), 0) + 1 FROM takes WHERE shot_id=?", (shot["id"],)).fetchone()[0]
        take_id = c.execute(
            "INSERT INTO takes(shot_id, take_no, status, prompt, duration, reason, provider, created_by, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (shot["id"], n, "queued", shot["prompt"], shot["duration"], reason.strip(),
             models.route(shot.get("engine"), settings), user_id, db.now())).lastrowid
    pool().submit(_run, take_id, shot, refs, settings)
    return take_id


def preview(shot: dict, refs: list[dict], settings: dict) -> dict:
    """Readable request (images as file names, not base64): for Magnific the endpoint and body, for Google the Veo body."""
    engine = shot.get("engine") or "veo"
    if engine == "renderfarm":  # сюда шот фермы попадает только в режиме «Заглушка»: настоящий запрос см. farm.preview
        return {"model": models.spec(engine)["name"], "note": "Заглушка: шот ферме не отправлялся"}
    if engine == "veo" and models.veo_route(settings) == "google":
        return build_request(shot, refs, settings, inline_images=False)
    try:
        req = models.build(shot, refs, settings, inline=False)
    except RuntimeError as e:
        return {"model": models.spec(engine)["name"], "error": str(e)}
    out = {"model": models.spec(engine)["name"], "url": models.API_BASE + req["path"], "body": req["body"]}
    if warn := models.link_warning(shot, refs, settings):
        out["warning"] = warn
    return out


def recover():
    """Студия перезапустилась (например, после обновления на сервере), а дубли ещё генерировались.
    Задачи, уже отправленные в нейросеть, ждём дальше и забираем готовые ролики. Дубли, которые не успели уйти,
    помечаем ошибкой: их надо отправить ещё раз."""
    with db.connect() as c:
        rows = db.rows(c.execute(
            "SELECT t.id, t.shot_id, t.take_no, t.remote_id, t.remote_path, t.request_path, t.farm_payload, t.farm_final, s.episode_id, s.label, s.idx "
            "FROM takes t JOIN shots s ON s.id=t.shot_id WHERE t.status IN ('queued','running')"))
    settings = db.get_settings()
    for r in rows:
        if r["remote_path"] == "farmhub":  # шоты фермы живут в базе: Пульт сам заберёт шот и пришлёт статусы и ролик
            if r["farm_final"]:  # финал уже на сайте, а «готово» от Пульта не дошло
                with db.connect() as c:
                    c.execute("UPDATE takes SET progress=1, farm_err='' WHERE id=?", (r["id"],))
                _finish(r["id"], r["shot_id"], "done", video=r["farm_final"])
            elif not r["remote_id"] and not r["farm_payload"]:
                _finish(r["id"], r["shot_id"], "error", "Студия перезапускалась до подготовки шота для фермы: отправьте шот ещё раз")
            continue
        if not r["remote_id"]:
            _finish(r["id"], r["shot_id"], "error", "Студия перезапускалась до отправки в нейросеть: отправьте шот ещё раз")
            continue
        base = f"ep{r['episode_id']}_shot{r['label'] or r['idx']}_take{r['id']}"
        pool().submit(_resume, r, base, settings)


def _resume(r: dict, base: str, settings: dict):
    try:
        if r["remote_path"] == "google":
            _wait_google(r["id"], r["shot_id"], base, r["remote_id"], settings.get("veo_api_key", "").strip(), r["request_path"])
        else:
            _wait_magnific(r["id"], r["shot_id"], base, r["remote_id"], r["remote_path"], settings, r["request_path"])
    except Exception as e:  # noqa: BLE001
        _finish(r["id"], r["shot_id"], "error", str(e))


def cancel(take_id: int) -> bool:
    """Отмена дубля: студия перестаёт ждать результат. Задачу, которая уже ушла в нейросеть, та может всё равно
    доделать (и списать кредиты), но в студию такой ролик уже не попадёт. Шот, который Пульт ещё не забрал, он не получит;
    уже принятый шот ферма доделает сама, отменить его можно только в Пульте."""
    with db.connect() as c:
        t = c.execute("SELECT shot_id, status FROM takes WHERE id=?", (take_id,)).fetchone()
        if not t or t["status"] not in ("queued", "running"):
            return False
        c.execute("UPDATE takes SET status='cancelled', error='Генерация отменена', finished_at=? WHERE id=?",
                  (db.now(), take_id))
        _after_take(c, take_id, t["shot_id"], "cancelled")
    return True
