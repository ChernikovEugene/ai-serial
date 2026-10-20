"""«Рендерферма»: шоты забирает Пульт студийной рендер-фермы (MiniMax H3 с синхроном губ, доводка до 1080p).

Контракт Пульта: docs/FARMHUB_SITE_API.md. Ферма стоит в сети студии, куда снаружи не зайти, поэтому студия никуда не
обращается сама: Пульт сам ходит на сайт.
  1. «Сгенерировать» создаёт дубль и готовит шот (текст как есть, перевод делает ферма); дубль ждёт ферму;
  2. `GET /api/farmhub/jobs/pending`: Пульт раз в 10 секунд забирает готовые шоты, картинки отдаются ссылками;
  3. `POST /api/farmhub/jobs/{id}/status`: принят, отклонён, старт, проценты, этап, конец (повторы отсекаются по `updated`);
  4. `POST /api/farmhub/jobs/{id}/files`: черновик (`draft`, после генерации) и готовый ролик (`final`, перед `job.finished`).
Все точки закрыты секретом FARMHUB_SECRET (`Authorization: Bearer`): переменная сервера, а если её нет, поле в
«Настройки» → «Рендерферма». Описания шота (действие, камера, голос, место, стиль) студия отправляет как написал автор, на любом
языке. Если в них есть русский текст, шот уходит с пометкой `translate: true`, и Пульт переводит его сам (студия ничего не
переводит и ничего не требует). Автор может по желанию вписать своё английское описание в блоке «Ферма»: оно главнее.
"""
import hmac
import json
import os
import re
import shutil
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

from . import db, models, veo

PENDING_MAX = 50                # столько шотов отдаём Пульту за один опрос
FILE_MAX_MB = 300               # ролик фермы обычно 3–20 МБ; документация просит принимать до 200
DISK_RESERVE_MB = 500
SHOT_SIZES = ("medium shot", "close-up", "wide shot")
ASPECTS = ("9:16", "16:9", "1:1", "3:4", "4:3")
CYR = re.compile(r"[А-Яа-яЁё]")
EVENTS = ("job.accepted", "job.rejected", "job.started", "job.progress", "job.requeued", "job.stage", "job.finished")
_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="farm")
_seen = {"at": 0.0}             # когда Пульт в последний раз обращался к студии (память процесса)


class FarmError(RuntimeError):
    pass


# ---------- секрет и связь ----------

def secret(settings: dict) -> str:
    return (os.environ.get("FARMHUB_SECRET") or settings.get("farmhub_secret") or "").strip()


def valid_auth(want: str, header: str) -> bool:
    """`Authorization: Bearer <секрет>`; сравнение за постоянное время."""
    parts = (header or "").split(None, 1)
    return bool(want) and len(parts) == 2 and parts[0].lower() == "bearer" and hmac.compare_digest(parts[1].strip().encode(), want.encode())


def touch():
    _seen["at"] = time.time()


def seen_ago() -> int | None:
    return int(time.time() - _seen["at"]) if _seen["at"] else None


def site_base(settings: dict, headers, scheme: str) -> str:
    """Адрес студии для ссылок на картинки: «Публичный адрес» из настроек, иначе тот, по которому Пульт сюда пришёл."""
    base = (settings.get("public_base_url") or "").strip().rstrip("/")
    if base:
        return base
    proto = (headers.get("x-forwarded-proto") or scheme).split(",")[0].strip()
    host = (headers.get("x-forwarded-host") or headers.get("host") or "").split(",")[0].strip()
    return f"{proto}://{host}"


def image_url(rel: str, base: str) -> str:
    """Подписанная ссылка на картинку из библиотеки (та же, что у Magnific): по подписи открывается только этот файл."""
    return f"{base}/pub/{models.sign(rel)}/{urllib.parse.quote(rel)}"


# ---------- шот студии -> шот Пульта ----------

def _mention_names(text: str) -> str:
    from .breakdown import MENTION_RE
    return MENTION_RE.sub(lambda m: m.group(1).replace("_", " "), text or "")


def plan(shot: dict, v_map: dict, a_map: dict, settings: dict) -> dict:
    """Что из шота пойдёт на ферму: поля, пути картинок, ошибки и предупреждения.
    errors — отправить нельзя; warnings — отправить можно, но автору стоит знать.
    Текст описаний уходит как есть, на любом языке: русский переводит ферма (см. needs_translation и build)."""
    errors, warns = [], []
    mode = shot.get("sound_mode") or ("off" if shot.get("sound_off") else "full")
    chars = [(c, v_map.get(c.get("version_id"))) for c in shot.get("characters") or []]
    lines = [d for d in shot.get("dialogue") or [] if d.get("voice") != "voiceover" and (d.get("text") or "").strip()]
    line = lines[0] if lines and mode == "full" else None
    if line and not chars:  # реплика нужна герою: губы синхронизируются по его картинке
        warns.append("В шоте нет героя, а у фермы реплику произносит герой: реплика не уйдёт. Добавьте героя в шот или уберите реплику")
        line = None
    if len(lines) > 1 and mode == "full" and line:
        warns.append("Ферма делает одну реплику одного героя на шот: уйдёт первая, остальные нужно вынести в отдельные шоты")
    if mode == "off":
        warns.append("Ферма всегда делает звук: «без звука» не получится, звук можно убрать на монтаже")

    pick = next(((c, v) for c, v in chars if line and c.get("asset_id") == line.get("asset_id") and v and v["images"]), None) \
        or next(((c, v) for c, v in chars if v and v["images"]), None)
    if len(chars) > 1:
        warns.append("Ферма принимает одного героя на шот: уйдёт "
                     + (f"«{pick[0]['name']}»" if pick else "первый с фото") + ", остальные нужно вынести в отдельные шоты")
    images = {}
    if pick:
        images["character"] = pick[1]["images"][0]
        if len(pick[1]["images"]) > 1:
            images["face"] = pick[1]["images"][1]
    elif chars:  # герой в шоте есть, а фото у него нет: ферма держит внешность по картинкам
        errors.append("У героя шота нет картинки: добавьте ему фото в библиотеке (ферма держит внешность по картинкам). "
                      "Если героя в кадре нет, уберите его из шота")
    if shot.get("composition_image"):
        warns.append("Референс композиции на ферму не отправляется: у Пульта нет такого поля")

    text = {"action": (shot.get("farm_action") or "").strip() or _mention_names(shot.get("action") or "")}
    if not text["action"].strip():
        errors.append("Нужно описание действия: заполните «Действие» (что происходит в кадре)")
    cam = (shot.get("farm_camera") or "").strip() or (shot.get("camera") or "").strip()
    if cam:
        text["camera"] = cam
    voice = (shot.get("farm_voice") or "").strip()
    if not voice and line:
        paren, lib = (line.get("parenthetical") or "").strip(), ((pick[1] or {}).get("voice") or "").strip() if pick else ""
        voice = " ".join(x for x in (paren, lib) if x)
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
    if not chars and "location" not in images:  # шот без героя (комната, предмет, вид): ферме нужна хотя бы картинка места
        errors.append("В шоте нет героя и нет картинки локации: ферме нужна хотя бы одна картинка. Добавьте локацию с фото или героя")

    quality = shot.get("farm_quality") if shot.get("farm_quality") in ("draft", "final") else "final"
    fields = {"id": None, "batch": f"ep{shot.get('episode_id')}" if shot.get("episode_id") else None, "action": None, "quality": quality}
    if line:
        fields["line"] = line["text"].strip()
    if shot.get("farm_shot_size") in SHOT_SIZES:
        fields["shot_size"] = shot["farm_shot_size"]
    if settings.get("aspect_ratio") in ASPECTS and settings["aspect_ratio"] != "9:16":
        fields["aspect"] = settings["aspect_ratio"]
    style = (settings.get("style") or "").strip()
    if style:
        fields["style"] = style
    if shot.get("farm_len") == "shot" and shot.get("duration"):
        fields["seconds"] = max(1, min(10, int(shot["duration"])))
    return {"fields": fields, "text": text, "images": images, "errors": errors, "warnings": warns}


def warnings(shot: dict, v_map: dict, a_map: dict, settings: dict) -> list[str]:
    p = plan(shot, v_map, a_map, settings)
    return [*p["errors"], *p["warnings"]]


TEXT_KEYS = ("action", "camera", "voice", "location_text", "style")  # описания шота: если в них русский, их переводит ферма


def needs_translation(shot: dict) -> bool:
    return any(CYR.search(str(v)) for k, v in shot.items() if k in TEXT_KEYS)


def build(p: dict, ref: str, settings: dict) -> dict:
    """Готовый шот для Пульта; пути картинок остаются путями, ссылки на них строятся при выдаче.
    Русский текст в описаниях уходит как есть с `translate: true`: перевод делает Пульт."""
    if p["errors"]:
        raise FarmError("; ".join(p["errors"]))
    shot = {k: v for k, v in p["fields"].items() if v is not None}
    shot["id"] = ref
    shot["action"] = p["text"]["action"]
    for k in ("camera", "voice"):
        if p["text"].get(k):
            shot[k] = p["text"][k]
    if p["text"].get("location"):
        shot["location_text"] = p["text"]["location"]
    if needs_translation(shot):
        shot["translate"] = True
    for k, rel in p["images"].items():
        if not (db.MEDIA_DIR / rel).is_file():
            raise FarmError(f"Картинка «{rel}» не найдена на сервере: загрузите её в библиотеку заново")
    return {"shot": shot, "images": p["images"]}


def preview(shot: dict, v_map: dict, a_map: dict, settings: dict) -> dict:
    """Что уйдёт на ферму, читаемо (картинки по именам файлов, русские описания помечены: их переведёт ферма)."""
    p = plan(shot, v_map, a_map, settings)
    body = {**{k: v for k, v in p["fields"].items() if v is not None},
            **{("location_text" if k == "location" else k): (f"{v}  [переведёт ферма]" if CYR.search(v) else v)
               for k, v in p["text"].items()},
            **{k: f"<ссылка на картинку {rel}>" for k, rel in p["images"].items()}}
    if needs_translation({**p["fields"], **{("location_text" if k == "location" else k): v for k, v in p["text"].items()}}):
        body["translate"] = True
    out = {"model": "Рендерферма (Пульт, MiniMax H3)", "url": "GET /api/farmhub/jobs/pending (шот забирает Пульт)", "body": body}
    if not secret(settings):
        out["warning"] = "Не задан секрет связи с фермой (FARMHUB_SECRET): без него Пульт не сможет забрать шот"
    if p["errors"] or p["warnings"]:
        out["warning"] = "; ".join(x for x in [out.get("warning"), *p["errors"], *p["warnings"]] if x)
    return out


# ---------- отправка ----------

def start_many(items: list[tuple[dict, dict, dict]], settings: dict, user_id: int | None, reason: str = "") -> list[int]:
    """Создаёт дубль на каждый шот и готовит его к выдаче Пульту (перевод описаний идёт в фоне). Дубль ждёт ферму.
    items: [(шот, v_map, a_map)]."""
    jobs = []
    with db.connect() as c:
        for shot, v_map, a_map in items:
            n = c.execute("SELECT COALESCE(MAX(take_no), 0) + 1 FROM takes WHERE shot_id=?", (shot["id"],)).fetchone()[0]
            tid = c.execute(
                "INSERT INTO takes(shot_id, take_no, status, prompt, duration, reason, provider, remote_path, batch_id, created_by, created_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (shot["id"], n, "queued", shot.get("prompt") or "", shot["duration"], reason.strip(), "renderfarm", "farmhub",
                 f"ep{shot['episode_id']}" if shot.get("episode_id") else "", user_id, db.now())).lastrowid
            jobs.append((tid, shot, v_map, a_map))
    for job in jobs:
        _pool.submit(_prepare, *job, settings)
    return [j[0] for j in jobs]


def _prepare(tid: int, shot: dict, v_map: dict, a_map: dict, settings: dict):
    try:
        if veo._cancelled(tid):
            return
        if not secret(settings):
            raise FarmError("Не задан секрет связи с фермой: FARMHUB_SECRET на сервере или «Настройки» → «Рендерферма»")
        payload = build(plan(shot, v_map, a_map, settings), f"take-{tid}", settings)
        veo.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        req_rel = f"renders/ep{shot['episode_id']}_shot{shot['label'] or shot['idx']}_take{tid}.request.json"
        (db.MEDIA_DIR / req_rel).write_text(json.dumps(payload, ensure_ascii=False, indent=2), "utf-8")
        with db.connect() as c:  # с этого момента шот виден Пульту в /jobs/pending
            c.execute("UPDATE takes SET farm_payload=?, request_path=? WHERE id=? AND status='queued'",
                      (json.dumps(payload, ensure_ascii=False), req_rel, tid))
    except Exception as e:  # noqa: BLE001 - показываем причину в карточке шота
        veo._finish(tid, shot["id"], "error", str(e))


def pending(base: str) -> list[dict]:
    """Шоты, которые ждут ферму: готовы к выдаче, а Пульт их ещё не принял и не отклонил."""
    with db.connect() as c:
        rows = db.rows(c.execute(
            "SELECT farm_payload FROM takes WHERE provider='renderfarm' AND status='queued' AND remote_id='' AND farm_payload<>''"
            " ORDER BY id LIMIT ?", (PENDING_MAX,)))
    out = []
    for r in rows:
        p = json.loads(r["farm_payload"])
        out.append({**p["shot"], **{k: image_url(rel, base) for k, rel in p["images"].items()}})
    return out


# ---------- статусы и файлы от Пульта ----------

def _take_for(ref: str) -> dict | None:
    m = re.fullmatch(r"take-(\d+)", ref or "")
    if not m:
        return None
    with db.connect() as c:
        r = c.execute("SELECT t.*, s.episode_id AS episode_id, s.label AS label, s.idx AS idx FROM takes t JOIN shots s ON s.id=t.shot_id "
                      "WHERE t.provider='renderfarm' AND t.id=?", (int(m.group(1)),)).fetchone()
    return dict(r) if r else None


def _live(take: dict) -> bool:
    return take["status"] in ("queued", "running")


def apply_status(ref: str, body: dict) -> dict:
    """Событие Пульта по шоту `ref` (наш id). Возвращает пометку для ответа; неизвестный шот — LookupError, плохое тело — ValueError."""
    take = _take_for(ref)
    if not take:
        raise LookupError("Шот не найден")
    event, job = body.get("event"), body.get("job")
    if event not in EVENTS or not isinstance(job, dict):
        raise ValueError("Ожидается {\"event\": \"job.…\", \"job\": {…}}")
    if not _live(take):
        return {"ignored": "дубль уже закончен или отменён"}
    updated = float(job.get("updated") or 0)
    stale = updated and (updated < take["farm_updated"] if event == "job.finished" else updated <= take["farm_updated"])  # конец применяем и при равном updated
    if stale and not (event in ("job.stage", "job.finished") and take["farm_err"]):
        return {"ignored": "повтор"}
    status, err = job.get("status") or "", (job.get("error") or "").strip()
    tid, sid = take["id"], take["shot_id"]

    if event == "job.rejected":
        veo._finish(tid, sid, "error", f"Ферма отклонила шот: {err or 'причина не указана'}")
        return {}
    if event == "job.finished":
        st = str(status).lower()
        if st in ("failed", "error"):
            veo._finish(tid, sid, "error", f"Ферма: {err or 'шот не получился'}")
        elif st in ("canceled", "cancelled"):
            veo.cancel(tid)
        elif st in ("done", "completed", "complete", "success", "succeeded", "finished", "ok"):
            final = take["farm_final"]
            if not final and err:  # ролик не загрузился: Пульт догрузит и повторит это событие
                with db.connect() as c:
                    c.execute("UPDATE takes SET farm_err=?, farm_msg=?, farm_updated=MAX(farm_updated, ?) WHERE id=?", (err, err, updated, tid))
            elif not final:
                veo._finish(tid, sid, "error", "Ферма закончила шот, но видео на сайт не пришло")
            else:
                with db.connect() as c:
                    c.execute("UPDATE takes SET progress=1, farm_err='', farm_updated=MAX(farm_updated, ?) WHERE id=?", (updated, tid))
                veo._finish(tid, sid, "done", f"Готово, но: {err}" if err else "", video=final)
        else:
            raise ValueError("В job.finished ожидается status done, failed или canceled")
        return {}
    with db.connect() as c:  # accepted / started / progress / requeued / stage
        c.execute("UPDATE takes SET remote_id=COALESCE(NULLIF(?, ''), remote_id), stage=?, progress=?, position=?, farm_msg=?,"
                  " farm_err=?, farm_updated=MAX(farm_updated, ?),"
                  " status=CASE WHEN ?='running' THEN 'running' WHEN ?='queued' THEN 'queued' ELSE status END WHERE id=?",
                  (str(job.get("id") or ""), job.get("stage") or take["stage"] or "generate", float(job.get("progress") or 0),
                   int(job.get("position") or 0), err or job.get("message") or "", err if event == "job.stage" else "", updated,
                   status, status, tid))
    return {}


def save_file(ref: str, kind: str, src) -> dict:
    """Черновик или готовый ролик от Пульта: файл `src` (бинарный поток) кладём в медиа-папку и привязываем к дублю.
    Повторная загрузка того же вида заменяет файл."""
    take = _take_for(ref)
    if not take:
        raise LookupError("Шот не найден")
    if kind not in ("draft", "final"):
        raise ValueError("kind: draft или final")
    if not _live(take):
        return {"ignored": "дубль уже закончен или отменён"}
    veo.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(db.MEDIA_DIR).free >> 20 < DISK_RESERVE_MB:
        raise OSError("На сервере почти закончилось место")
    rel = f"renders/ep{take['episode_id']}_shot{take['label'] or take['idx']}_take{take['id']}{'_draft' if kind == 'draft' else ''}.mp4"
    tmp = db.MEDIA_DIR / (rel + ".part")
    size = 0
    try:
        with tmp.open("wb") as out:
            while chunk := src.read(1 << 20):
                size += len(chunk)
                if size > FILE_MAX_MB << 20:
                    raise OverflowError(f"Файл больше {FILE_MAX_MB} МБ")
                out.write(chunk)
        if not size:
            raise ValueError("Пустой файл")
        tmp.replace(db.MEDIA_DIR / rel)
    finally:
        tmp.unlink(missing_ok=True)
    with db.connect() as c:
        c.execute("UPDATE takes SET draft_path=? WHERE id=?" if kind == "draft" else "UPDATE takes SET farm_final=? WHERE id=?", (rel, take["id"]))
    if kind == "final":  # ролик уже на сайте: шот готов, не дожидаясь job.finished (он мог потеряться)
        settle(take["id"], take["shot_id"], rel)
    return {"url": f"/media/{rel}"}


def settle(tid: int, sid: int, final: str):
    """Финальный ролик получен: дубль готов. Последующий job.finished уже ничего не меняет (событие для готового дубля игнорируется)."""
    with db.connect() as c:
        c.execute("UPDATE takes SET progress=1, farm_err='' WHERE id=?", (tid,))
    veo._finish(tid, sid, "done", video=final)
