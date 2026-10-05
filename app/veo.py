"""Video generation. Every run creates a take (дубль) for the shot.
`stub` provider writes the exact request to disk instead of calling Veo;
`gemini` calls the Gemini API (Veo 3 / 3.1) with the key from settings."""
import base64
import json
import mimetypes
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from . import db

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


def _finish(take_id: int, shot_id: int, status: str, error="", video="", request=""):
    with db.connect() as c:
        c.execute("UPDATE takes SET status=?, error=?, video_path=COALESCE(NULLIF(?, ''), video_path),"
                  " request_path=COALESCE(NULLIF(?, ''), request_path), finished_at=? WHERE id=?",
                  (status, error, video, request, db.now(), take_id))
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


def _run(take_id: int, shot: dict, refs: list[dict], settings: dict):
    shot_id = shot["id"]
    try:
        with db.connect() as c:
            c.execute("UPDATE takes SET status='running' WHERE id=?", (take_id,))
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        base = f"ep{shot['episode_id']}_shot{shot['label'] or shot['idx']}_take{take_id}"
        req_rel = f"renders/{base}.request.json"
        (db.MEDIA_DIR / req_rel).write_text(
            json.dumps(build_request(shot, refs, settings, inline_images=False), ensure_ascii=False, indent=2), "utf-8")

        if settings.get("veo_provider") != "gemini":
            time.sleep(1.5)
            _finish(take_id, shot_id, "stub", "Заглушка: запрос сохранён, видео не создавалось", request=req_rel)
            return

        key = settings.get("veo_api_key", "").strip()
        if not key:
            raise RuntimeError("Не указан API-ключ Veo в настройках")
        model = settings.get("veo_model", "veo-3.1-generate-preview")
        op = _http("POST", f"{API_BASE}/models/{model}:predictLongRunning", key,
                   build_request(shot, refs, settings, inline_images=True))
        name = op["name"]
        deadline = time.time() + 20 * 60
        while not op.get("done"):
            if time.time() > deadline:
                raise RuntimeError("Veo не ответил за 20 минут")
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
    except Exception as e:  # noqa: BLE001 - surface any failure in the UI
        _finish(take_id, shot_id, "error", str(e))


def start(shot: dict, refs: list[dict], settings: dict, user_id: int | None, reason: str = "") -> int:
    """Creates a take that remembers the exact prompt it was generated from and why it was (re)generated."""
    with db.connect() as c:
        n = c.execute("SELECT COALESCE(MAX(take_no), 0) + 1 FROM takes WHERE shot_id=?", (shot["id"],)).fetchone()[0]
        take_id = c.execute(
            "INSERT INTO takes(shot_id, take_no, status, prompt, duration, reason, provider, created_by, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (shot["id"], n, "queued", shot["prompt"], shot["duration"], reason.strip(),
             settings.get("veo_provider", "stub"), user_id, db.now())).lastrowid
    pool().submit(_run, take_id, shot, refs, settings)
    return take_id


def preview(shot: dict, refs: list[dict], settings: dict) -> dict:
    return build_request(shot, refs, settings, inline_images=False)


def recover():
    """Takes left 'running' by a closed app can never finish — mark them as errors on startup."""
    with db.connect() as c:
        c.execute("UPDATE takes SET status='error', error='Приложение было закрыто во время генерации' "
                  "WHERE status IN ('queued','running')")
