"""Стартовый и финальный кадры шота для Gemini Omni: Nano Banana 2.1 через API Magnific.

Gemini Omni получает на вход два кадра и промпт, поэтому перед видео нужно нарисовать оба кадра. Кадр делает Nano Banana 2.1
по промпту, который пишет человек, и по картинкам-референсам: героям и локации из библиотеки (с выбором версии и состояния)
и своим картинкам. Размер всегда 1K, формат 9:16. Каждый кадр (`start`, `end`) хранится в шоте как JSON
(`shots.frame_start`, `shots.frame_end`):

    prompt   текст для Nano Banana 2.1 (пишет человек руками)
    refs     [{"path", "name", "kind": character|location|upload|frame}]; нет ключа: берутся главные картинки героев и локации шота
    status   "" | queued | running | done | error | stub
    task_id  задача в Magnific (нужна, чтобы после перезапуска студии дождаться уже оплаченной картинки)
    path     готовая картинка (media/frames/<шот>/…)
    error    причина ошибки
    request  файл с точным запросом (media/frames/<шот>/…request.json)

Когда оба кадра `done`, карточка шота становится зелёной и шот можно отправлять в Gemini Omni."""
import base64
import json
import mimetypes
import time

from . import db, models

NANO_PATH = "/v1/ai/text-to-image/nano-banana-2-1"
SIDES = ("start", "end")
COLS = {"start": "frame_start", "end": "frame_end"}
SIDE_NAMES = {"start": "стартовый", "end": "финальный"}
MAX_REFS = 14            # столько картинок принимает Nano Banana 2.1
RESOLUTION = "1K"
REF_DIRS = ("characters/", "locations/", "frames/")   # откуда можно брать референсы: библиотека, свои загрузки, готовые кадры
RATIOS = {"auto", "1:1", "2:3", "3:2", "3:4", "4:3", "4:5", "5:4", "9:16", "16:9", "21:9", "1:4", "4:1", "1:8", "8:1"}
WAIT_MINUTES = 10


# ---------- хранение ----------

def load(shot_id: int, side: str) -> dict:
    with db.connect() as c:
        r = c.execute(f"SELECT {COLS[side]} FROM shots WHERE id=?", (shot_id,)).fetchone()
    data = json.loads(r[0] or "{}") if r else {}
    return data if isinstance(data, dict) else {}


def update(shot_id: int, side: str, **kw) -> dict:
    """Дописать поля в кадр (чтение и запись под одной блокировкой базы, чтобы параллельные изменения не затирали друг друга)."""
    with db.connect() as c:
        r = c.execute(f"SELECT {COLS[side]} FROM shots WHERE id=?", (shot_id,)).fetchone()
        if not r:
            return {}
        data = json.loads(r[0] or "{}")
        data = data if isinstance(data, dict) else {}
        data.update(kw)
        data["updated"] = time.time()
        c.execute(f"UPDATE shots SET {COLS[side]}=? WHERE id=?", (db.dumps(data), shot_id))
    return data


def is_ready(frame: dict) -> bool:
    return frame.get("status") == "done" and bool(frame.get("path")) and (db.MEDIA_DIR / frame["path"]).is_file()


# ---------- референсы ----------

def default_refs(shot: dict, versions: dict, assets: dict) -> list[dict]:
    """Пока человек сам ничего не выбирал: главные картинки героев шота и локации."""
    out = []
    for c in shot.get("characters", []):
        v = versions.get(c.get("version_id"))
        if v and v.get("images"):
            label = f" ({v['label']})" if v.get("label") else ""
            out.append({"path": v["images"][0], "name": f"{c['name']}{label}", "kind": "character"})
    if shot.get("location_mode") != "text":
        v = versions.get(shot.get("location_version_id"))
        if v and v.get("images"):
            a = assets.get(v["asset_id"], {})
            label = f" ({v['label']})" if v.get("label") else ""
            out.append({"path": v["images"][0], "name": f"{a.get('name', 'Локация')}{label}", "kind": "location"})
    return out[:MAX_REFS]


def clean_refs(refs: list[dict]) -> list[dict]:
    """Референсы приходят с браузера: оставляем только настоящие файлы из библиотеки и папки кадров."""
    out, seen = [], set()
    for r in refs or []:
        p = str(r.get("path") or "")
        if ".." in p or p.startswith("/") or not p.startswith(REF_DIRS) or p in seen:
            continue
        f = db.MEDIA_DIR / p
        if not f.is_file():
            continue
        seen.add(p)
        kind = r.get("kind") if r.get("kind") in ("character", "location", "upload", "frame") else "upload"
        out.append({"path": p, "name": str(r.get("name") or "")[:120], "kind": kind})
    return out[:MAX_REFS]


def resolved(shot: dict, side: str, versions: dict, assets: dict) -> dict:
    """Кадр для карточки шота: сохранённые поля + список референсов (свой или по умолчанию) + готовность."""
    f = dict(shot.get(COLS[side]) or {})
    explicit = "refs" in f
    f["refs"] = clean_refs(f["refs"]) if explicit else default_refs(shot, versions, assets)
    f["auto_refs"] = not explicit
    f["ready"] = is_ready(f)
    f.setdefault("status", "")
    f.setdefault("prompt", "")
    f.setdefault("error", "")
    return f


def all_ready(shot_id: int) -> bool:
    return all(is_ready(load(shot_id, side)) for side in SIDES)


# ---------- запрос ----------

def _mime(rel: str) -> str:
    return mimetypes.guess_type(rel)[0] or "image/png"


def build_request(shot: dict, side: str, settings: dict, versions: dict, assets: dict, inline: bool) -> dict:
    f = resolved(shot, side, versions, assets)
    prompt = (f.get("prompt") or "").strip()
    if len(prompt) < 2:
        raise RuntimeError(f"Напишите промпт для {SIDE_NAMES[side]} кадра")
    ratio = settings.get("aspect_ratio") or "9:16"
    body: dict = {"prompt": prompt, "aspect_ratio": ratio if ratio in RATIOS else "9:16", "resolution": RESOLUTION}
    if f["refs"]:
        body["reference_images"] = []
        for i, r in enumerate(f["refs"], 1):
            data = base64.b64encode((db.MEDIA_DIR / r["path"]).read_bytes()).decode() if inline else f"<base64 {r['path']}>"
            body["reference_images"].append({"image": data, "mime_type": _mime(r["path"]),
                                             "text": f"Image {i}: {r['name']}" if r.get("name") else f"Image {i}"})
    return {"path": NANO_PATH, "status_path": NANO_PATH, "body": body}


def preview(shot: dict, side: str, settings: dict, versions: dict, assets: dict) -> dict:
    try:
        req = build_request(shot, side, settings, versions, assets, inline=False)
    except RuntimeError as e:
        return {"model": "Nano Banana 2.1", "error": str(e)}
    return {"model": "Nano Banana 2.1", "url": models.API_BASE + req["path"], "body": req["body"]}


# ---------- запуск ----------

def start(shot: dict, side: str, settings: dict, versions: dict, assets: dict) -> None:
    """Проверяет запрос и запускает рисование кадра в фоне. Ошибки запроса (нет промпта) возвращаются сразу."""
    build_request(shot, side, settings, versions, assets, inline=False)   # RuntimeError: нет промпта
    stamp = f"{int(time.time())}"
    update(shot["id"], side, status="queued", error="", task_id="", started=stamp)
    from . import veo   # пул потоков генерации общий с видео
    veo.pool().submit(_run, shot, side, settings, versions, assets, stamp)


def _alive(shot_id: int, side: str, stamp: str) -> bool:
    """Запуск ещё актуален: шот не удалён и по кадру не запустили новую попытку."""
    f = load(shot_id, side)
    return bool(f) and f.get("started") == stamp and f.get("status") in ("queued", "running")


def _run(shot: dict, side: str, settings: dict, versions: dict, assets: dict, stamp: str) -> None:
    sid = shot["id"]
    try:
        update(sid, side, status="running")
        folder = db.MEDIA_DIR / "frames" / str(sid)
        folder.mkdir(parents=True, exist_ok=True)
        req_rel = f"frames/{sid}/{side}_{stamp}.request.json"
        (db.MEDIA_DIR / req_rel).write_text(json.dumps(preview(shot, side, settings, versions, assets), ensure_ascii=False, indent=2), "utf-8")
        if models.route(models.DEFAULT_ENGINE, settings) == "stub":
            update(sid, side, status="stub", request=req_rel, error="Заглушка: запрос сохранён, картинка не создавалась. "
                                                                    "Включите «Реальная генерация» в настройках")
            return
        req = build_request(shot, side, settings, versions, assets, inline=True)
        task = models._http("POST", models.API_BASE + req["path"], models._key(settings), req["body"])["data"]
        update(sid, side, task_id=task["task_id"], request=req_rel)
        _wait(sid, side, task["task_id"], settings, stamp)
    except Exception as e:  # noqa: BLE001 - причина видна на карточке
        if load(sid, side).get("started") == stamp:
            update(sid, side, status="error", error=str(e)[:900])


def _sniff_ext(data: bytes, url: str) -> str:
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if data[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    tail = url.split("?")[0].lower().rsplit(".", 1)[-1]
    return "." + tail if tail in ("png", "jpg", "jpeg", "webp") else ".png"


def _wait(sid: int, side: str, tid: str, settings: dict, stamp: str, minutes: int = WAIT_MINUTES) -> None:
    key = models._key(settings)
    deadline = time.time() + minutes * 60
    task: dict = {}
    while task.get("status") not in ("COMPLETED", "FAILED"):
        if not _alive(sid, side, stamp):
            return
        if time.time() > deadline:
            raise RuntimeError(f"Nano Banana 2.1 не закончил за {minutes} минут (задача {tid})")
        if task:
            time.sleep(3)
        task = models._http("GET", f"{models.API_BASE}{NANO_PATH}/{tid}", key)["data"]
    if task["status"] == "FAILED" or not task.get("generated"):
        raise RuntimeError("Nano Banana 2.1 не сделал картинку: " + json.dumps(task, ensure_ascii=False)[:500])
    url = task["generated"][0]
    data = models._http("GET", url, None, raw=True)   # ссылка Magnific живёт недолго: скачиваем сразу
    if not _alive(sid, side, stamp):
        return
    rel = f"frames/{sid}/{side}_{stamp}{_sniff_ext(data, url)}"
    (db.MEDIA_DIR / rel).write_bytes(data)
    old = load(sid, side).get("path")
    update(sid, side, status="done", path=rel, error="")
    if old and old != rel:
        _drop_file(old, sid)


def _drop_file(rel: str, sid: int) -> None:
    """Старую картинку кадра стираем, чтобы не копились файлы (но только свои: из папки этого шота)."""
    if rel.startswith(f"frames/{sid}/") and ".." not in rel:
        (db.MEDIA_DIR / rel).unlink(missing_ok=True)


def reset(shot_id: int, side: str) -> None:
    """Убрать готовый кадр (или сбросить зависшую попытку): промпт и референсы остаются."""
    f = load(shot_id, side)
    if f.get("path"):
        _drop_file(f["path"], shot_id)
    update(shot_id, side, status="", path="", error="", task_id="", started="", own=False)


def remove_files(shot_id: int) -> None:
    import shutil
    shutil.rmtree(db.MEDIA_DIR / "frames" / str(shot_id), ignore_errors=True)


def recover() -> None:
    """Студия перезапустилась, пока рисовались кадры. Задачи, уже ушедшие в Magnific, дожидаемся, остальные помечаем ошибкой."""
    with db.connect() as c:
        rows = db.rows(c.execute("SELECT id, frame_start, frame_end FROM shots"))
    settings = db.get_settings()
    from . import veo
    for r in rows:
        for side in SIDES:
            f = r[COLS[side]]
            if not isinstance(f, dict) or f.get("status") not in ("queued", "running"):
                continue
            if f.get("task_id"):
                veo.pool().submit(_resume, r["id"], side, f["task_id"], settings, f.get("started", ""))
            else:
                update(r["id"], side, status="error", error="Студия перезапускалась до отправки в Magnific: нажмите «Сделать кадр» ещё раз")


def _resume(sid: int, side: str, tid: str, settings: dict, stamp: str) -> None:
    try:
        _wait(sid, side, tid, settings, stamp)
    except Exception as e:  # noqa: BLE001
        if load(sid, side).get("started") == stamp:
            update(sid, side, status="error", error=str(e)[:900])
