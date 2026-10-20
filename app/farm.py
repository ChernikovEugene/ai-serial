"""«Рендерферма»: шоты уходят в Пульт студийной рендер-фермы (MiniMax H3 с синхроном губ, доводка до 1080p).

Контракт Пульта: docs/FARMHUB_SITE_API.md (документ SITE_API.md от владельца фермы). Коротко:
  1. картинки героя, лица и локации загружаются один раз (`POST /uploads`) и дальше подставляются по id;
  2. все шоты серии уходят одним запросом (`POST /batches`, до 50 в пачке); промпт для модели пишет сама ферма;
  3. ферма сообщает о ходе работы событиями на /api/farmhub/webhook (подпись HMAC-SHA256 проверяется) и,
     на случай пропущенных событий, студия раз в минуту опрашивает `GET /batches/{batch}`;
  4. готовый ролик (и черновик после генерации) скачивается в студию, ссылки фермы пользователям не отдаются.

Настройки: FARMHUB_API_URL, FARMHUB_API_KEY, FARMHUB_WEBHOOK_SECRET (переменные сервера; если их нет, берутся поля
из «Настройки» → «Рендерферма»). Описания шота (действие, камера, голос, место) ферма принимает только по-английски,
реплика остаётся русской: русские описания студия переводит сама (ключ ANTHROPIC_API_KEY или поле в настройках)
либо их можно вписать по-английски в блоке «Ферма» карточки шота.
"""
import hashlib
import hmac
import json
import mimetypes
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor

from . import db, veo

BATCH_MAX = 50
UPLOAD_TTL = 24 * 3600          # id загрузки на ферме не вечный: через сутки загружаем картинку заново
POLL_EVERY = 60                 # страховка на случай пропущенных событий
SHOT_SIZES = ("medium shot", "close-up", "wide shot")
ASPECTS = ("9:16", "16:9", "1:1", "3:4", "4:3")
CYR = re.compile(r"[А-Яа-яЁё]")
TRANSLATE_MODEL = "claude-haiku-5-5"
_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="farm")


class FarmError(RuntimeError):
    pass


# ---------- настройки ----------

def config(settings: dict) -> dict:
    """Адрес, ключ и секрет webhook: сначала переменные сервера, затем поля настроек, ключ ещё и из «Нейросети и API-ключи»."""
    url = (os.environ.get("FARMHUB_API_URL") or settings.get("farmhub_api_url") or "").strip().rstrip("/")
    key = (os.environ.get("FARMHUB_API_KEY") or settings.get("farmhub_api_key") or "").strip()
    secret = (os.environ.get("FARMHUB_WEBHOOK_SECRET") or settings.get("farmhub_webhook_secret") or "").strip()
    if not key:  # ключ, добавленный в «Нейросети и API-ключи» с видом «Рендерферма» или с таким именем
        with db.connect() as c:
            rows = db.rows(c.execute("SELECT name, kind, api_key FROM integrations WHERE api_key<>'' ORDER BY id"))
        for r in rows:
            if r["kind"] == "renderfarm" or "рендерферм" in r["name"].lower().replace(" ", ""):
                key = r["api_key"].strip()
                break
    return {"url": url, "key": key, "secret": secret}


def need_config(settings: dict) -> dict:
    cfg = config(settings)
    if not cfg["url"]:
        raise FarmError("Не указан адрес API фермы: FARMHUB_API_URL на сервере или «Настройки» → «Рендерферма»")
    if not cfg["key"]:
        raise FarmError("Не указан ключ API фермы: FARMHUB_API_KEY на сервере или «Настройки» → «Рендерферма»")
    return cfg


def webhook_url(settings: dict) -> str:
    base = (settings.get("public_base_url") or "").strip().rstrip("/")
    return f"{base}/api/farmhub/webhook" if base else ""


# ---------- HTTP ----------

def _detail(body: bytes) -> str:
    try:
        d = json.loads(body)
        d = d.get("detail", d) if isinstance(d, dict) else d
        return d if isinstance(d, str) else json.dumps(d, ensure_ascii=False)
    except ValueError:
        return body.decode(errors="replace")[:500]


def _open(req: urllib.request.Request, timeout: int):
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        body = e.read()
        if e.code == 401:
            raise FarmError("Ферма не приняла ключ API (401): проверьте FARMHUB_API_KEY") from None
        if e.code == 410:
            raise FarmError("Файл уже удалён с хранилища фермы (410)") from None
        raise FarmError(f"Ферма: {_detail(body)}" if e.code == 422 else f"Ферма: HTTP {e.code}: {_detail(body)[:500]}") from None
    except urllib.error.URLError as e:
        raise FarmError(f"Нет связи с фермой ({req.full_url.split('/')[2]}): {e.reason}") from None
    except TimeoutError:
        raise FarmError("Ферма не ответила вовремя") from None


def call(method: str, path: str, cfg: dict, body: dict | None = None, timeout: int = 60):
    req = urllib.request.Request(cfg["url"] + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": f"Bearer {cfg['key']}", "Content-Type": "application/json"})
    data = _open(req, timeout)
    return json.loads(data) if data else {}


def download(url: str, cfg: dict) -> bytes:
    """Файл фермы: ссылка может быть путём от корня сервера (/api/v1/jobs/…) или полным адресом; ключ нужен в обоих случаях."""
    full = urllib.parse.urljoin(cfg["url"] + "/", url) if url.startswith("/") else url
    return _open(urllib.request.Request(full, headers={"Authorization": f"Bearer {cfg['key']}"}), 600)


def upload_image(rel: str, cfg: dict) -> str:
    """Загружает картинку из медиа-папки студии на ферму и запоминает её id (одна и та же картинка не грузится повторно)."""
    path = db.MEDIA_DIR / rel
    data = path.read_bytes()
    digest = hashlib.sha256(cfg["url"].encode() + data).hexdigest()
    with db.connect() as c:
        r = c.execute("SELECT upload_id, created_at FROM farm_uploads WHERE hash=?", (digest,)).fetchone()
    if r and time.time() - r["created_at"] < UPLOAD_TTL:
        return r["upload_id"]
    boundary = uuid.uuid4().hex
    mime = mimetypes.guess_type(path.name)[0] or "image/png"
    body = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{path.name}"\r\n'
            f"Content-Type: {mime}\r\n\r\n").encode() + data + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(cfg["url"] + "/uploads", method="POST", data=body, headers={
        "Authorization": f"Bearer {cfg['key']}", "Content-Type": f"multipart/form-data; boundary={boundary}"})
    out = json.loads(_open(req, 300))
    if not out.get("id"):
        raise FarmError("Ферма не вернула id загруженной картинки: " + json.dumps(out, ensure_ascii=False)[:300])
    with db.connect() as c:
        c.execute("INSERT OR REPLACE INTO farm_uploads(hash, upload_id, created_at) VALUES (?,?,?)", (digest, out["id"], time.time()))
    return out["id"]


# ---------- перевод описаний на английский ----------

def _anthropic_key(settings: dict) -> str:
    return (os.environ.get("ANTHROPIC_API_KEY") or settings.get("anthropic_api_key") or "").strip()


TRANSLATE_RULES = (
    "You translate short scene descriptions of a vertical TikTok series from Russian to English for a video model. "
    "Input: a JSON object whose values are Russian (or mixed) texts. Output: ONLY a JSON object with the same keys and English values, "
    "nothing else. Rules: concise visual wording in the present tense, no names of people or characters anywhere (use she, he, "
    "the girl, the man, the character; if a text says the character with a given gender, keep it), do not quote or translate any "
    "spoken lines, no Cyrillic in the output. Key 'voice': a short voice description like 'whisper', 'excited fast voice', "
    "'normal calm voice'. Key 'camera': a short camera instruction like 'slow push-in'. Key 'location': the place in a few words. "
    "Key 'action': what happens in the frame.")


def translate(fields: dict[str, str], settings: dict) -> dict[str, str]:
    """Переводит русские значения на английский (остальные не трогает). Результат кешируется по тексту."""
    todo = {k: v for k, v in fields.items() if v and CYR.search(v)}
    out = {k: v for k, v in fields.items() if k not in todo}
    if not todo:
        return out
    digest = hashlib.sha256(json.dumps(todo, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    with db.connect() as c:
        r = c.execute("SELECT result FROM translations WHERE hash=?", (digest,)).fetchone()
    if r:
        return {**out, **json.loads(r["result"])}
    key = _anthropic_key(settings)
    if not key:
        raise FarmError("Описание шота написано по-русски, а ферма принимает его только по-английски. Впишите английский вариант "
                        "в блоке «Ферма» карточки шота или укажите ключ перевода (ANTHROPIC_API_KEY на сервере или "
                        "«Настройки» → «Рендерферма»)")
    base = (os.environ.get("ANTHROPIC_BASE_URL") or "https://api.anthropic.com").rstrip("/")
    req = urllib.request.Request(base + "/v1/messages", method="POST", headers={
        "x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
        data=json.dumps({"model": TRANSLATE_MODEL, "max_tokens": 1500, "system": TRANSLATE_RULES,
                         "messages": [{"role": "user", "content": json.dumps(todo, ensure_ascii=False)}]}).encode())
    try:
        resp = json.loads(_open(req, 60))
        text = "".join(b.get("text", "") for b in resp["content"] if b.get("type") == "text")
        tr = json.loads(text[text.index("{"):text.rindex("}") + 1])
        if set(tr) != set(todo) or not all(isinstance(v, str) for v in tr.values()):
            raise ValueError("не те ключи")
    except FarmError as e:
        raise FarmError(f"Перевод описания на английский не удался: {str(e).removeprefix('Ферма: ')}") from None
    except (ValueError, KeyError, TypeError) as e:
        raise FarmError(f"Перевод описания на английский не удался: ответ не разобран ({e})") from None
    if any(CYR.search(v) for v in tr.values()):
        raise FarmError("Перевод описания на английский не удался: в ответе осталась кириллица. Впишите английский вариант в блоке «Ферма»")
    with db.connect() as c:
        c.execute("INSERT OR REPLACE INTO translations(hash, result, created_at) VALUES (?,?,?)",
                  (digest, json.dumps(tr, ensure_ascii=False), db.now()))
    return {**out, **tr}


# ---------- шот студии -> шот Пульта ----------

def _mention_names(text: str) -> str:
    from .breakdown import MENTION_RE
    return MENTION_RE.sub(lambda m: m.group(1).replace("_", " "), text or "")


def plan(shot: dict, v_map: dict, a_map: dict, settings: dict) -> dict:
    """Что из шота пойдёт на ферму, пока без перевода и загрузки: поля, пути картинок, ошибки и предупреждения.
    errors — отправить нельзя; warnings — отправить можно, но автору стоит знать."""
    errors, warns = [], []
    mode = shot.get("sound_mode") or ("off" if shot.get("sound_off") else "full")
    lines = [d for d in shot.get("dialogue") or [] if d.get("voice") != "voiceover" and (d.get("text") or "").strip()]
    line = lines[0] if lines and mode == "full" else None
    if len(lines) > 1 and mode == "full":
        warns.append("Ферма делает одну реплику одного героя на шот: уйдёт первая, остальные нужно вынести в отдельные шоты")
    if mode == "off":
        warns.append("Ферма всегда делает звук: «без звука» не получится, звук можно убрать на монтаже")

    chars = [(c, v_map.get(c.get("version_id"))) for c in shot.get("characters") or []]
    pick = next(((c, v) for c, v in chars if line and c.get("asset_id") == line.get("asset_id") and v and v["images"]), None) \
        or next(((c, v) for c, v in chars if v and v["images"]), None)
    if len(chars) > 1:
        warns.append("Ферма принимает одного героя на шот: уйдёт "
                     + (f"«{pick[0]['name']}»" if pick else "первый с фото") + ", остальные нужно вынести в отдельные шоты")
    images = {}
    if not pick:
        errors.append("Нужен герой с картинкой: добавьте в шот персонажа, у которого в библиотеке есть фото (ферма держит внешность по картинкам)")
    else:
        images["character"] = pick[1]["images"][0]
        if len(pick[1]["images"]) > 1:
            images["face"] = pick[1]["images"][1]
    if shot.get("composition_image"):
        warns.append("Референс композиции на ферму не отправляется: у Пульта нет такого поля")

    text = {"action": (shot.get("farm_action") or "").strip() or _mention_names(shot.get("action") or "")}
    if not text["action"].strip():
        errors.append("Нужно описание действия: заполните «Действие» (если действия нет, напишите, что персонаж просто смотрит в камеру)")
    cam = (shot.get("camera") or "").strip()
    if cam:
        text["camera"] = cam
    voice = (shot.get("farm_voice") or "").strip()
    if not voice and line:
        how = " ".join(x for x in [(line.get("parenthetical") or "").strip(), ((pick[1] or {}).get("voice") or "").strip() if pick else ""] if x)
        voice = how
    if voice:
        text["voice"] = voice

    loc_text = ""
    if shot.get("location_mode") == "text":
        loc_text = (shot.get("location_text") or "").strip()
    else:
        lv = v_map.get(shot.get("location_version_id"))
        if lv and lv["images"]:
            images["location"] = lv["images"][0]
        elif lv:
            loc_text = f"{(a_map.get(lv['asset_id']) or {}).get('name', '')}. {lv.get('description') or ''}".strip(" .")
        elif shot.get("scene"):
            loc_text = shot["scene"]
    if loc_text:
        text["location"] = loc_text

    quality = shot.get("farm_quality") if shot.get("farm_quality") in ("draft", "final") else "final"
    fields = {"ref": None, "action": None, "quality": quality}
    if line:
        fields["line"] = line["text"].strip()
    if shot.get("farm_shot_size") in SHOT_SIZES:
        fields["shot_size"] = shot["farm_shot_size"]
    if settings.get("aspect_ratio") in ASPECTS and settings["aspect_ratio"] != "9:16":
        fields["aspect"] = settings["aspect_ratio"]
    style = (settings.get("style") or "").strip()
    if style and not CYR.search(style):
        fields["style"] = style
    if shot.get("farm_len") == "shot" and shot.get("duration"):
        fields["seconds"] = max(1, min(10, int(shot["duration"])))
    return {"fields": fields, "text": text, "images": images, "errors": errors, "warnings": warns}


def warnings(shot: dict, v_map: dict, a_map: dict, settings: dict) -> list[str]:
    p = plan(shot, v_map, a_map, settings)
    return [*p["errors"], *p["warnings"]]


def finalize(p: dict, ref: str, settings: dict) -> dict:
    """Готовый шот для POST /batches: перевод английских полей и загрузка картинок на ферму."""
    cfg = need_config(settings)
    if p["errors"]:
        raise FarmError("; ".join(p["errors"]))
    tr = translate(p["text"], settings)
    shot = {k: v for k, v in p["fields"].items() if v is not None}
    shot["ref"] = ref
    shot["action"] = tr["action"]
    for k in ("camera", "voice"):
        if tr.get(k):
            shot[k] = tr[k]
    if tr.get("location"):
        shot["location_text"] = tr["location"]
    for k, rel in p["images"].items():
        shot[k] = upload_image(rel, cfg)
    return shot


def preview(shot: dict, v_map: dict, a_map: dict, settings: dict) -> dict:
    """Что уйдёт на ферму, читаемо (картинки по именам файлов, русские описания помечены: их переведёт студия перед отправкой)."""
    p = plan(shot, v_map, a_map, settings)
    body = {**{k: v for k, v in p["fields"].items() if v is not None},
            **{("location_text" if k == "location" else k): (v if not CYR.search(v) else f"{v}  [переведётся на английский перед отправкой]")
               for k, v in p["text"].items()},
            **{k: f"<картинка {rel}>" for k, rel in p["images"].items()}}
    out = {"model": "Рендерферма (Пульт, MiniMax H3)", "url": (config(settings)["url"] or "<адрес API фермы не указан>") + "/batches",
           "body": {"webhook_url": webhook_url(settings) or None, "shots": [body]}}
    if p["errors"] or p["warnings"]:
        out["warning"] = "; ".join([*p["errors"], *p["warnings"]])
    return out


# ---------- отправка ----------

def start_many(items: list[tuple[dict, dict, dict]], settings: dict, user_id: int | None, reason: str = "") -> list[int]:
    """Создаёт дубль на каждый шот и отправляет все шоты на ферму одним запросом (пачками до 50).
    items: [(шот, v_map, a_map)]."""
    take_ids, jobs = [], []
    with db.connect() as c:
        for shot, v_map, a_map in items:
            n = c.execute("SELECT COALESCE(MAX(take_no), 0) + 1 FROM takes WHERE shot_id=?", (shot["id"],)).fetchone()[0]
            tid = c.execute(
                "INSERT INTO takes(shot_id, take_no, status, prompt, duration, reason, provider, created_by, created_at)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (shot["id"], n, "queued", shot.get("prompt") or "", shot["duration"], reason.strip(), "renderfarm", user_id, db.now())).lastrowid
            take_ids.append(tid)
            jobs.append((tid, shot, v_map, a_map))
    _pool.submit(_submit, jobs, settings)
    return take_ids


def _fail(take_id: int, shot_id: int, msg: str):
    veo._finish(take_id, shot_id, "error", msg)


def _submit(jobs: list, settings: dict):
    ready = []
    for tid, shot, v_map, a_map in jobs:
        if veo._cancelled(tid):
            continue
        try:
            ready.append((tid, shot, finalize(plan(shot, v_map, a_map, settings), f"take-{tid}", settings)))
        except Exception as e:  # noqa: BLE001 - показываем причину в карточке шота
            _fail(tid, shot["id"], str(e))
    for i in range(0, len(ready), BATCH_MAX):
        chunk = ready[i:i + BATCH_MAX]
        try:
            cfg = need_config(settings)
            body = {"shots": [r[2] for r in chunk]}
            if hook := webhook_url(settings):
                body["webhook_url"] = hook
            res = call("POST", "/batches", cfg, body, timeout=120)
            by_ref = {j.get("ref"): j for j in res.get("jobs", [])}
            for tid, shot, payload in chunk:
                job = by_ref.get(payload["ref"])
                if not job:
                    _fail(tid, shot["id"], "Ферма не вернула задачу для этого шота")
                    continue
                with db.connect() as c:
                    c.execute("UPDATE takes SET remote_id=?, remote_path='farmhub', batch_id=?, status=CASE WHEN status='queued' THEN ? ELSE status END,"
                              " stage=?, position=? WHERE id=? AND status IN ('queued','running')",
                              (job["id"], res.get("batch", ""), "running" if job.get("status") == "running" else "queued",
                               job.get("stage") or "generate", int(job.get("position") or 0), tid))
        except Exception as e:  # noqa: BLE001
            for tid, shot, _ in chunk:
                _fail(tid, shot["id"], str(e))


def cancel_job(job_id: str, settings: dict):
    """Просит ферму отменить задачу (дубль в студии уже помечен отменённым)."""
    def run():
        try:
            call("DELETE", f"/jobs/{job_id}", need_config(settings))
        except Exception:  # noqa: BLE001 - ферма всё равно не вернёт этот ролик в студию
            pass
    _pool.submit(run)


# ---------- события и опрос ----------

def valid_signature(secret: str, body: bytes, header: str) -> bool:
    want = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(header or "", want)


def handle_event(event: dict):
    """Событие Пульта (webhook). Выполняется в фоне: скачивание роликов не должно держать ответ 2xx."""
    _pool.submit(_apply_event, event)


def _apply_event(event: dict):
    try:
        settings = db.get_settings()
        if event.get("event") == "batch.finished":
            for job in event.get("jobs") or []:
                apply_job(job, settings)
        elif event.get("job"):
            apply_job(event["job"], settings)
    except Exception as e:  # noqa: BLE001 - одно битое событие не должно ронять остальные
        print("Рендерферма: событие не обработано:", e)


def _take_for(job: dict) -> dict | None:
    with db.connect() as c:
        r = c.execute("SELECT t.*, s.episode_id AS episode_id, s.label AS label, s.idx AS idx FROM takes t JOIN shots s ON s.id=t.shot_id "
                      "WHERE t.provider='renderfarm' AND t.remote_id=?", (job.get("id"),)).fetchone()
        if not r and str(job.get("ref") or "").startswith("take-"):  # событие пришло раньше, чем студия записала id задачи
            r = c.execute("SELECT t.*, s.episode_id AS episode_id, s.label AS label, s.idx AS idx FROM takes t JOIN shots s ON s.id=t.shot_id "
                          "WHERE t.provider='renderfarm' AND t.id=?", (job["ref"][5:] if job["ref"][5:].isdigit() else 0,)).fetchone()
    return dict(r) if r else None


def _save_file(take: dict, name: str, data: bytes) -> str:
    veo.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    rel = f"renders/ep{take['episode_id']}_shot{take['label'] or take['idx']}_take{take['id']}{name}.mp4"
    (db.MEDIA_DIR / rel).write_bytes(data)
    return rel


def apply_job(job: dict, settings: dict):
    """Применяет состояние задачи к дублю. События одного шота могут повторяться: старее уже применённого пропускаем."""
    take = _take_for(job)
    if not take or take["status"] not in ("queued", "running"):
        return
    updated = float(job.get("updated") or 0)
    status = job.get("status")
    with db.connect() as c:
        if updated and updated <= take["farm_updated"] and status not in ("done", "failed", "canceled"):
            return
        c.execute("UPDATE takes SET remote_id=?, remote_path='farmhub', batch_id=COALESCE(NULLIF(?, ''), batch_id), stage=?, progress=?, position=?,"
                  " farm_msg=?, farm_updated=MAX(farm_updated, ?), status=CASE WHEN ?='running' THEN 'running' WHEN ?='queued' THEN 'queued' ELSE status END"
                  " WHERE id=?",
                  (job["id"], job.get("batch") or "", job.get("stage") or "generate", float(job.get("progress") or 0),
                   int(job.get("position") or 0), job.get("message") or "", updated, status, status, take["id"]))
    cfg = need_config(settings)
    raw = (job.get("raw_outputs") or [])
    if raw and not take["draft_path"] and status != "done":
        try:
            rel = _save_file(take, "_draft", download(raw[0]["url"], cfg))
            with db.connect() as c:
                c.execute("UPDATE takes SET draft_path=? WHERE id=?", (rel, take["id"]))
        except FarmError as e:
            print("Рендерферма: черновик не скачан:", e)
    if status == "done":
        out = (job.get("outputs") or raw or [None])[0]
        if not out:
            return veo._finish(take["id"], take["shot_id"], "error", "Ферма завершила шот, но не вернула файл")
        try:
            rel = _save_file(take, "", download(out["url"], cfg))
        except FarmError as e:
            return veo._finish(take["id"], take["shot_id"], "error", f"Не удалось скачать готовый ролик с фермы: {e}")
        note = f"Готов черновик: {job['error']}" if job.get("error") else ""
        with db.connect() as c:
            c.execute("UPDATE takes SET progress=1 WHERE id=?", (take["id"],))
        veo._finish(take["id"], take["shot_id"], "done", note, video=rel)
    elif status == "failed":
        veo._finish(take["id"], take["shot_id"], "error", f"Ферма: {job.get('error') or 'шот не получился'}")
    elif status == "canceled":
        veo.cancel(take["id"], notify_farm=False)


def poll_once():
    settings = db.get_settings()
    with db.connect() as c:
        batches = [r[0] for r in c.execute("SELECT DISTINCT batch_id FROM takes WHERE provider='renderfarm' AND batch_id<>'' "
                                           "AND status IN ('queued','running')")]
    if not batches:
        return
    cfg = need_config(settings)
    for b in batches:
        try:
            res = call("GET", f"/batches/{b}", cfg)
            for job in res.get("jobs") or []:
                apply_job(job, settings)
        except Exception as e:  # noqa: BLE001
            print(f"Рендерферма: опрос пачки {b} не удался:", e)


def start_poller():
    def loop():
        while True:
            time.sleep(POLL_EVERY)
            try:
                poll_once()
            except Exception as e:  # noqa: BLE001
                print("Рендерферма: опрос не удался:", e)
    threading.Thread(target=loop, name="farm-poll", daemon=True).start()
