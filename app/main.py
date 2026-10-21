import os
import hmac
import shutil
import json
import re
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path

from fastapi import Body, Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import auth, breakdown, db, exporter, farm, feedback, models, schedule, veo
from .github import GitHubError

STATIC_DIR = Path(__file__).resolve().parent / "static"
REPORTS_DIR = db.DATA_DIR / "reports"  # снимки «Сообщить о проблеме»: не в media, отдаются только админу


def _site_version() -> str:
    """Версия сайта для заявок: коммит, с которого запущен сервер (git или переменная хостинга)."""
    import os
    import subprocess
    for k in ("RENDER_GIT_COMMIT", "GIT_COMMIT", "SOURCE_VERSION"):
        if os.environ.get(k):
            return os.environ[k][:7]
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=Path(__file__).resolve().parent.parent,
                              capture_output=True, text=True, timeout=5).stdout.strip()
    except Exception:  # noqa: BLE001
        return ""


SITE_VERSION = _site_version()
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp"}
VIDEO_EXT = {".mp4", ".mov", ".webm"}
# Лимиты загрузки: один ролик шота — до VIDEO_MAX_MB, картинка — до IMAGE_MAX_MB. И не загружаем, если на диске
# с данными (на сервере — 10 ГБ) остаётся меньше DISK_RESERVE_MB: иначе студия перестанет сохранять базу.
VIDEO_MAX_MB, IMAGE_MAX_MB, DISK_RESERVE_MB = 300, 20, 1024
# В какой нейросети делается шот: выбирает монтажёр. Все, кроме «Другой», уходят через Magnific API (app/models.py).
ENGINES = models.ENGINES
ENGINE_NAMES = dict(ENGINES)

HOLIDAYS = {
    "01-01": "Новый год", "01-07": "Рождество", "01-25": "Татьянин день", "02-14": "День святого Валентина",
    "02-23": "23 февраля", "03-08": "8 марта", "04-01": "День смеха", "04-12": "День космонавтики",
    "05-01": "Праздник весны и труда", "05-09": "День Победы", "06-01": "День защиты детей",
    "06-12": "День России", "07-08": "День семьи, любви и верности", "09-01": "День знаний",
    "10-31": "Хэллоуин", "11-04": "День народного единства", "12-31": "Новогодняя ночь",
}

db.init()
veo.recover()
exporter.recover()
app = FastAPI(title="Студия сериала")


# ---------- auth middleware ----------

OPEN_PATHS = ("/api/auth/",)
VIEWER_WRITE_OK = re.compile(r"^/api/(episodes/\d+/comments|comments/\d+|auth/.*|reports|notifications/seen)$")


@app.middleware("http")
async def session_guard(request: Request, call_next):
    path = request.url.path
    request.state.user = None
    if path.startswith("/api/farmhub/"):  # Пульт фермы ходит без входа в студию: его пускает только секрет
        want = farm.secret(db.get_settings())
        if not want:
            return JSONResponse({"detail": "Секрет связи с фермой не задан на сервере (FARMHUB_SECRET)"}, status_code=503)
        if not farm.valid_auth(want, request.headers.get("Authorization", "")):
            return JSONResponse({"detail": "Неверный секрет"}, status_code=401)
        farm.touch()
    elif path.startswith("/api/") or path.startswith("/media/"):
        user = auth.user_for_token(request.cookies.get(auth.COOKIE))
        request.state.user = user
        if not path.startswith(OPEN_PATHS):
            if not user:
                return JSONResponse({"detail": "Нужно войти"}, status_code=401)
            if (user["role"] in auth.READ_ONLY_ROLES and request.method != "GET" and not VIEWER_WRITE_OK.match(path)
                    and not auth.access_of(user)["caps"]):
                return JSONResponse({"detail": f"Недостаточно прав: роль «{auth.ROLES[user['role']]}»"}, status_code=403)
    response = await call_next(request)
    if path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache"  # always pick up a new app version
    return response


def uid(request: Request) -> int | None:
    return request.state.user["id"] if request.state.user else None


class Credentials(BaseModel):
    login: str
    password: str
    name: str = ""


def _login_response(user_id: int) -> Response:
    resp = JSONResponse({"ok": True})
    resp.set_cookie(auth.COOKIE, auth.create_session(user_id), max_age=auth.SESSION_DAYS * 86400,
                    httponly=True, samesite="lax")
    return resp


@app.get("/api/auth/state")
def auth_state(request: Request):
    return {"has_users": auth.has_users(), "user": request.state.user}


@app.post("/api/auth/setup")
def auth_setup(c: Credentials):
    if auth.has_users():
        raise HTTPException(400, "Администратор уже создан")
    if len(c.login.strip()) < 2 or len(c.password) < 4:
        raise HTTPException(400, "Логин от 2 символов, пароль от 4")
    with db.connect() as conn:
        user_id = conn.execute("INSERT INTO users(login, name, pass_hash, role, created_at) VALUES (?,?,?,?,?)",
                               (c.login.strip().lower(), c.name.strip() or c.login.strip(),
                                auth.hash_password(c.password), "admin", db.now())).lastrowid
    return _login_response(user_id)


@app.post("/api/auth/login")
def auth_login(c: Credentials):
    with db.connect() as conn:
        u = conn.execute("SELECT * FROM users WHERE login=?", (c.login.strip().lower(),)).fetchone()
    if not u or not auth.check_password(c.password, u["pass_hash"]):
        raise HTTPException(401, "Неверный логин или пароль")
    return _login_response(u["id"])


@app.post("/api/auth/logout")
def auth_logout(request: Request):
    auth.drop_session(request.cookies.get(auth.COOKIE))
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(auth.COOKIE)
    return resp


class UserIn(BaseModel):
    login: str | None = None
    name: str | None = None
    password: str | None = None
    role: str | None = None
    tg: str | None = None      # ник в Telegram для связи
    tasks: str | None = None   # чем занят на проекте (свободный текст)
    access: dict | None = None  # {"pages": [...], "caps": [...]}; {} — вернуть «как у роли»


def _tg(v: str | None) -> str:
    """Ник в Telegram без собаки и пробелов: «@ksu » -> «ksu»."""
    return (v or "").strip().lstrip("@").strip()


@app.get("/api/users")
def list_users():
    with db.connect() as c:
        users = db.rows(c.execute("SELECT id, login, name, role, created_at, tg, tasks, access FROM users ORDER BY id"))
    return [{**u, "access": auth.access_of(u)} for u in users]


@app.post("/api/users", dependencies=[Depends(auth.admin)])
def create_user(u: UserIn):
    if not u.login or not u.password or len(u.password) < 4:
        raise HTTPException(400, "Нужны логин и пароль (от 4 символов)")
    if u.role and u.role not in auth.ROLES:
        raise HTTPException(400, "Неизвестная роль")
    with db.connect() as c:
        if c.execute("SELECT 1 FROM users WHERE login=?", (u.login.strip().lower(),)).fetchone():
            raise HTTPException(400, "Такой логин уже есть")
        c.execute("INSERT INTO users(login, name, pass_hash, role, created_at, tg, tasks) VALUES (?,?,?,?,?,?,?)",
                  (u.login.strip().lower(), (u.name or u.login).strip(), auth.hash_password(u.password),
                   u.role or "writer", db.now(), _tg(u.tg), (u.tasks or "").strip()))
    return list_users()


@app.put("/api/users/{user_id}")
def update_user(user_id: int, u: UserIn, request: Request):
    me = request.state.user
    if me["role"] != "admin" and me["id"] != user_id:
        raise HTTPException(403, "Недостаточно прав")
    with db.connect() as c:
        if u.name is not None:
            c.execute("UPDATE users SET name=? WHERE id=?", (u.name.strip(), user_id))
        if u.tg is not None:
            c.execute("UPDATE users SET tg=? WHERE id=?", (_tg(u.tg), user_id))
        if u.tasks is not None and me["role"] == "admin":
            c.execute("UPDATE users SET tasks=? WHERE id=?", (u.tasks.strip(), user_id))
        if u.password:
            if len(u.password) < 4:
                raise HTTPException(400, "Пароль от 4 символов")
            c.execute("UPDATE users SET pass_hash=? WHERE id=?", (auth.hash_password(u.password), user_id))
        if u.access is not None and me["role"] == "admin":
            pages = [x for x in u.access.get("pages", []) if x in auth.PAGES]
            caps = [x for x in u.access.get("caps", []) if x in auth.CAPS]
            c.execute("UPDATE users SET access=? WHERE id=?",
                      (json.dumps({"pages": pages, "caps": caps}) if u.access else "", user_id))
        if u.role is not None and me["role"] == "admin":
            if u.role not in auth.ROLES:
                raise HTTPException(400, "Неизвестная роль")
            admins = c.execute("SELECT COUNT(*) FROM users WHERE role='admin' AND id<>?", (user_id,)).fetchone()[0]
            if u.role != "admin" and not admins:
                raise HTTPException(400, "Должен остаться хотя бы один администратор")
            old = c.execute("SELECT role FROM users WHERE id=?", (user_id,)).fetchone()
            c.execute("UPDATE users SET role=? WHERE id=?", (u.role, user_id))
            if old and old["role"] != u.role and u.access is None:  # новая роль — доступы снова как у неё
                c.execute("UPDATE users SET access='' WHERE id=?", (user_id,))
    return list_users()


@app.delete("/api/users/{user_id}", dependencies=[Depends(auth.admin)])
def delete_user(user_id: int, request: Request):
    if user_id == request.state.user["id"]:
        raise HTTPException(400, "Нельзя удалить себя")
    with db.connect() as c:
        c.execute("DELETE FROM users WHERE id=?", (user_id,))
    return list_users()


def status_flow(role: str) -> dict:
    """Which statuses the role may move an episode from / to (null = any). The UI uses it to build the status menu."""
    sources, targets = auth.STATUS_SOURCES.get(role, set()), auth.STATUS_TARGETS.get(role, set())
    return {"from": None if sources is None else sorted(sources), "to": None if targets is None else sorted(targets)}


@app.get("/api/meta")
def meta(request: Request):
    s = db.get_settings()
    user = request.state.user
    return {
        "user": {k: user[k] for k in ("id", "login", "name", "role")},
        "access": auth.access_of(user),
        "pages": auth.PAGES, "caps": auth.CAPS, "role_access": auth.ROLE_ACCESS,
        "statuses": [{"key": k, "name": n} for k, n in db.STATUSES],
        "roles": auth.ROLES,
        "role_duties": auth.ROLE_DUTIES,
        "status_flow": status_flow(request.state.user["role"]),
        "gen_mode": s.get("gen_mode") or "stub", "veo_route": models.veo_route(s), "veo_model": s["veo_model"],
        "max_shot_seconds": int(s["max_shot_seconds"]), "words_per_second": float(s["words_per_second"]),
        "anchor_number": int(s["anchor_number"]), "anchor_date": s["anchor_date"],
        "today": date.today().isoformat(),
        "engines": [{"key": k, "name": m["name"], "durations": list(m["durations"]), "duration_note": m["duration_note"],
                     "refs_note": m["refs_note"], "tips": m["tips"], "audio": m["audio"], "api": models.has_api(k),
                     "max_refs": m["max_refs"], "first_frame": m["first_frame"], "prompt_max": m["prompt_max"],
                     "ref_example": models.token(k, 1) if m["ref_token"] else ""}
                    for k, m in models.MODELS.items()],
        "series_title": s.get("series_title") or "Сериал",
        "version": SITE_VERSION,
        "sound_modes": SOUND_MODES,
        "upload_limits": {"video_mb": VIDEO_MAX_MB, "image_mb": IMAGE_MAX_MB},
    }


# ---------- helpers ----------

def load_assets(kind: str | None = None) -> list[dict]:
    with db.connect() as c:
        q = "SELECT * FROM assets" + (" WHERE kind=?" if kind else "") + " ORDER BY name"
        assets = db.rows(c.execute(q, (kind,) if kind else ()))
        versions = db.rows(c.execute("SELECT * FROM asset_versions ORDER BY version_no"))
    by_asset: dict[int, list] = {}
    for v in versions:
        by_asset.setdefault(v["asset_id"], []).append(v)
    for a in assets:
        a["versions"] = by_asset.get(a["id"], [])
    return assets


def get_asset(asset_id: int) -> dict:
    a = next((a for a in load_assets() if a["id"] == asset_id), None)
    if not a:
        raise HTTPException(404, "Не найдено")
    return a


def save_upload(f: UploadFile, folder: str, allowed=IMAGE_EXT) -> str:
    ext = Path(f.filename or "").suffix.lower()
    if ext not in allowed:
        raise HTTPException(400, f"Неподдерживаемый формат файла: {f.filename}. Подходят: {', '.join(sorted(allowed))}")
    limit_mb = VIDEO_MAX_MB if allowed is VIDEO_EXT else IMAGE_MAX_MB
    db.MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    free_mb = shutil.disk_usage(db.MEDIA_DIR).free // (1 << 20)
    if free_mb - ((f.size or 0) >> 20) < DISK_RESERVE_MB:
        raise HTTPException(507, f"На сервере почти закончилось место (свободно {free_mb} МБ). Удалите старые видео или увеличьте диск")
    rel = f"{folder}/{uuid.uuid4().hex[:12]}{ext}"
    dest = db.MEDIA_DIR / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    size = 0
    with dest.open("wb") as out:
        while chunk := f.file.read(1 << 20):
            size += len(chunk)
            if size > limit_mb << 20:
                out.close()
                dest.unlink(missing_ok=True)
                raise HTTPException(413, f"Файл «{f.filename}» больше {limit_mb} МБ. Сожмите его или загрузите короче")
            out.write(chunk)
    return rel


class Ctx:
    """Everything needed to build prompts, loaded once per request."""

    def __init__(self):
        self.assets = load_assets()
        self.a_map = {a["id"]: a for a in self.assets}
        self.v_map = {v["id"]: v for a in self.assets for v in a["versions"]}
        self.settings = db.get_settings()
        self.max_s = int(self.settings.get("max_shot_seconds") or 8)
        self.wps = float(self.settings.get("words_per_second") or 2.5)


def refresh_shot(s: dict, ctx: Ctx) -> dict:
    """Recompute estimate, Veo-forced duration and (unless locked) the prompt."""
    s["est_seconds"], s["speech_seconds"] = breakdown.estimate(s["action"], s["dialogue"], ctx.wps)
    s["words"] = sum(breakdown.words(d["text"]) for d in s["dialogue"])
    s["duration"] = breakdown.snap_duration(s.get("engine"), s["duration"], s["est_seconds"])
    prompt, refs, _ = breakdown.build_prompt(s, ctx.v_map, ctx.a_map, ctx.settings)
    forced = breakdown.forced_duration(refs, s, ctx.settings)
    if forced and s["duration"] != forced:
        s["duration"] = forced
        prompt, refs, _ = breakdown.build_prompt(s, ctx.v_map, ctx.a_map, ctx.settings)
    if not s.get("prompt_locked"):
        s["prompt"] = prompt
    return s


SOUND_MODES = {"full": "речь и звук", "ambient": "только естественный фон", "off": "без звука"}


def sound_mode(s: dict) -> str:
    return s.get("sound_mode") if s.get("sound_mode") in SOUND_MODES else ("off" if s.get("sound_off") else "full")


def shot_checks(s: dict) -> list[dict]:
    """Готовность шота к генерации (защита от пустых шотов): без всех зелёных пунктов шот не уходит в нейросеть,
    отправить его всё равно может только продюсер. Если пункт не нужен, его закрывают явно: «без персонажей»,
    локация текстом «-», действие «-». Отдельно от shot_missing, который решает, можно ли утверждать сценарий."""
    has_text = any((d.get("text") or "").strip() for d in s.get("dialogue") or [])
    mode = sound_mode(s)
    return [
        {"key": "characters", "label": "Персонажи: выбраны или «без персонажей»",
         "ok": bool(s.get("characters")) or bool(s.get("no_characters"))},
        {"key": "location", "label": "Локация: выбрана или описана текстом (можно «-»)", "ok": breakdown.has_location(s)},
        {"key": "action", "label": "Действие: прописано (если нечего — «-»)", "ok": bool((s.get("action") or "").strip())},
        {"key": "duration", "label": "Длина клипа выбрана", "ok": bool(s.get("duration"))},
        {"key": "prompt", "label": "Промпт собран", "ok": bool((s.get("prompt") or "").strip())},
        {"key": "sound", "label": "Звук: есть реплика или выбран «только фон» / «без звука»" if mode == "full"
         else f"Звук: {SOUND_MODES[mode]}", "ok": mode != "full" or has_text},
    ]


def unready_items(s: dict) -> list[str]:
    return [c["label"] for c in s["checks"] if not c["ok"]]


def shot_out(s: dict, ctx: Ctx, takes: list[dict]) -> dict:
    for t in takes:  # шот фермы подготовлен (переведён) и ждёт Пульт; сам текст шота клиенту не нужен
        if "farm_payload" in t:
            t["farm_ready"] = bool(t.pop("farm_payload"))
    _, refs, warns = breakdown.build_prompt(s, ctx.v_map, ctx.a_map, ctx.settings)
    s["max_s"] = max_s = breakdown.engine_limit(s.get("engine"), ctx.settings)  # лимит шота у каждой нейросети свой
    w = breakdown.shot_warnings(s, max_s)
    if not breakdown.has_location(s) and s.get("scene") and not breakdown.location_text_mode(s):
        w.append(f"Локация «{s['scene']}» не найдена в библиотеке")
    m = models.spec(s.get("engine"))
    if m["prompt_max"] and len(s.get("prompt") or "") > m["prompt_max"]:
        warns.append(f"Промпт {len(s['prompt'])} знаков, а {m['name']} принимает до {m['prompt_max']}: сократите стиль или правила")
    if s.get("prompt_locked") and refs and m["ref_token"]:
        want = models.token(s.get("engine"), 1).rstrip("1")
        if want.strip() not in (s.get("prompt") or ""):
            warns.append(f"Промпт изменён вручную: для {m['name']} картинки называются «{models.token(s.get('engine'), 1)}», "
                         "«↻ пересобрать» соберёт его заново")
    if link := models.link_warning(s, refs, ctx.settings):
        warns.append(link)
    if s.get("engine") == "renderfarm":
        warns.extend(farm.warnings(s, ctx.v_map, ctx.a_map, ctx.settings))
    if not m["audio"] and sound_mode(s) != "off" and models.has_api(s.get("engine")):
        warns.append(f"{m['name']} не делает звук: видео придёт без звука, "
                     + ("фон" if sound_mode(s) == "ambient" else "речь и фон") + " придётся добавить на монтаже")
    s["warnings"] = w + warns + models.image_warnings(s.get("engine"), refs)
    s["missing"] = breakdown.shot_missing(s, max_s)
    s["checks"] = shot_checks(s)
    s["ready"] = all(c["ok"] for c in s["checks"])
    s["revisions"] = max(0, len(takes) - 1)  # how many times this shot was re-generated
    s["over_limit"] = s["est_seconds"] > max_s
    s["forced_8"] = bool(breakdown.forced_duration(refs, s, ctx.settings))
    s["references"] = refs
    # промпт для ручной генерации: картинки по латинским меткам (@altushka), файлы скачиваются с такими же именами
    s["prompt_manual"], s["manual_refs"], _ = breakdown.build_prompt(s, ctx.v_map, ctx.a_map, ctx.settings, manual=True)
    s["takes"] = takes
    sel = next((t for t in takes if t["id"] == s.get("selected_take_id")), None)
    s["selected_take"] = sel
    last = takes[-1] if takes else None
    s["gen_status"] = last["status"] if last else "idle"
    return s


SHOT_COLS = ["idx", "label", "scene", "duration", "est_seconds", "speech_seconds", "words", "camera", "action",
             "dialogue", "characters", "location_version_id", "location_mode", "location_text", "composition_image",
             "composition_mode", "composition_note", "prompt", "negative_prompt", "prompt_locked", "selected_take_id", "needs_redo", "engine", "sound_off", "sound_mode", "no_characters", "voiceover",
             "farm_action", "farm_voice", "farm_camera", "farm_shot_size", "farm_quality", "farm_len"]


def save_shot(c, s: dict):
    vals = [db.dumps(s[k]) if k in ("dialogue", "characters") else (s.get(k) or "" if k.startswith("farm_") else s.get(k))
            for k in SHOT_COLS]
    vals = [int(v) if isinstance(v, bool) else v for v in vals]
    if s.get("id"):
        c.execute(f"UPDATE shots SET {', '.join(f'{k}=?' for k in SHOT_COLS)}, updated_at=? WHERE id=?",
                  (*vals, db.now(), s["id"]))
    else:
        s["id"] = c.execute(
            f"INSERT INTO shots(episode_id, {', '.join(SHOT_COLS)}, updated_at) VALUES (?, {', '.join('?' * len(SHOT_COLS))}, ?)",
            (s["episode_id"], *vals, db.now())).lastrowid


def refresh_all_prompts() -> int:
    """Пересобрать промпты шотов после смены общих настроек или шаблонов ТТ. Шоты с закреплённым вручную промптом
    и выложенные серии не трогаются. Возвращает, сколько шотов пересобрано."""
    ctx = Ctx()
    with db.connect() as c:
        shots = db.rows(c.execute("SELECT s.* FROM shots s JOIN episodes e ON e.id=s.episode_id "
                                  "WHERE s.prompt_locked=0 AND e.status<>'posted'"))
        for sh in shots:
            refresh_shot(sh, ctx)
            save_shot(c, sh)
    return len(shots)


def episode_cast(eid: int) -> dict:
    with db.connect() as c:
        return {r["asset_id"]: r["version_id"] for r in c.execute("SELECT * FROM episode_cast WHERE episode_id=?", (eid,))}


def load_episode_row(eid: int) -> dict:
    with db.connect() as c:
        ep = db.row(c.execute("SELECT * FROM episodes WHERE id=?", (eid,)).fetchone())
    if not ep:
        raise HTTPException(404, "Серия не найдена")
    if ep["deleted_at"]:
        raise HTTPException(404, "Серия в корзине: верните её на странице «Серии» → «Корзина»")
    return ep


def writer_may_edit(request: Request, status: str) -> None:
    """Once the script is approved only the producer can change it, so the editor works on a stable text."""
    if request.state.user["role"] != "admin" and status not in auth.WRITER_STATUSES:
        raise HTTPException(403, "ТЗ уже готово — править серию может только продюсер. "
                                 "Верните серию в «Пишется ТЗ» или попросите продюсера")


def incomplete_shots(eid: int) -> list[str]:
    ep = get_episode(eid)
    if not ep["shots"]:
        return ["В серии нет ни одного шота"]
    return [f"Шот {s['label']}: {'; '.join(s['missing'])}" for s in ep["shots"] if s["missing"]]


def promote_if_ready(eid: int, user_id) -> None:
    """Серия в «Пишется ТЗ» сама переходит в «ТЗ готово», как только все её шоты заполнены, чтобы монтажёр её увидел.
    Из «ТЗ на проверке» не переводим: туда серию возвращает монтажёр, и решать, что ТЗ исправлено, должен человек."""
    if load_episode_row(eid)["status"] != "dev":
        return
    shots = get_episode(eid)["shots"]
    if not shots or any(s["missing"] or not s["ready"] for s in shots):  # все шоты заполнены и все галочки зелёные
        return
    with db.connect() as c:
        c.execute("UPDATE episodes SET status='approved', updated_at=? WHERE id=? AND status='dev'", (db.now(), eid))
    db.log(eid, user_id, "Все шоты заполнены: статус «Пишется ТЗ» → «ТЗ готово» (автоматически)")


def user_names() -> dict:
    with db.connect() as c:
        return {r["id"]: r["name"] or r["login"] for r in c.execute("SELECT id, name, login FROM users")}


# ---------- assets: characters & locations ----------

@app.get("/api/assets")
def list_assets(kind: str | None = None):
    return load_assets(kind)


@app.post("/api/assets", dependencies=[Depends(auth.need("library"))])
def create_asset(request: Request, kind: str = Form(...), name: str = Form(...), aliases: str = Form(""),
                 label: str = Form("Базовый"), description: str = Form(""), voice: str = Form(""),
                 notes: str = Form(""), images: list[UploadFile] = File(default=[])):
    if kind not in ("character", "location"):
        raise HTTPException(400, "kind must be character|location")
    name = name.strip()
    if not name:
        raise HTTPException(400, "Укажите название")
    alias_list = [x.strip() for x in aliases.split(",") if x.strip()]
    with db.connect() as c:
        tag = db.make_tag(name, {r[0] for r in c.execute("SELECT tag FROM assets")}, kind)
        asset_id = c.execute("INSERT INTO assets(kind, name, aliases, tag, created_by, created_at) VALUES (?,?,?,?,?,?)",
                             (kind, name, db.dumps(alias_list), tag, uid(request), db.now())).lastrowid
    paths = [save_upload(f, f"{kind}s/{asset_id}") for f in images if f.filename]
    with db.connect() as c:
        vid = c.execute(
            "INSERT INTO asset_versions(asset_id, version_no, label, description, voice, notes, images, created_by, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (asset_id, 1, label or "Базовый", description, voice, notes, db.dumps(paths), uid(request), db.now())).lastrowid
        c.execute("UPDATE assets SET active_version_id=? WHERE id=?", (vid, asset_id))
    return get_asset(asset_id)


class AssetPatch(BaseModel):
    name: str | None = None
    aliases: list[str] | None = None
    tag: str | None = None


@app.put("/api/assets/{asset_id}", dependencies=[Depends(auth.need("library"))])
def update_asset(asset_id: int, p: AssetPatch):
    with db.connect() as c:
        if p.tag is not None:
            tag = p.tag.strip().lstrip("@").lower()
            if not db.TAG_RE.match(tag):
                raise HTTPException(400, "Метка — только латиница, цифры и «_», без пробелов: например, altushka")
            if c.execute("SELECT 1 FROM assets WHERE tag=? AND id<>?", (tag, asset_id)).fetchone():
                raise HTTPException(400, f"Метка @{tag} уже занята: у каждого персонажа и локации своя")
            c.execute("UPDATE assets SET tag=? WHERE id=?", (tag, asset_id))
        if p.name is not None and p.name.strip():
            c.execute("UPDATE assets SET name=? WHERE id=?", (p.name.strip(), asset_id))
        if p.aliases is not None:
            c.execute("UPDATE assets SET aliases=? WHERE id=?", (db.dumps([a.strip() for a in p.aliases if a.strip()]), asset_id))
    return get_asset(asset_id)


@app.delete("/api/assets/{asset_id}", dependencies=[Depends(auth.need("library"))])
def delete_asset(asset_id: int):
    with db.connect() as c:
        c.execute("DELETE FROM assets WHERE id=?", (asset_id,))
        # убрать удалённого из участников арок, чтобы не висели ссылки в пустоту
        for arc in db.rows(c.execute("SELECT id, members FROM arcs")):
            kept = [m for m in arc["members"] if m.get("asset_id") != asset_id]
            if len(kept) != len(arc["members"]):
                c.execute("UPDATE arcs SET members=? WHERE id=?", (db.dumps(kept), arc["id"]))
    return {"ok": True}


@app.post("/api/assets/{asset_id}/versions", dependencies=[Depends(auth.need("library"))])
def create_version(request: Request, asset_id: int, label: str = Form(""), description: str = Form(""),
                   voice: str = Form(""), notes: str = Form(""), keep_images: str = Form("[]"),
                   base_version_id: int | None = Form(None), activate: bool = Form(True),
                   images: list[UploadFile] = File(default=[])):
    """A new version is an edited copy; old versions never change, so any of them can be used again."""
    a = get_asset(asset_id)
    kept = [p for p in json.loads(keep_images or "[]") if isinstance(p, str)]
    paths = kept + [save_upload(f, f"{a['kind']}s/{asset_id}") for f in images if f.filename]
    next_no = max((v["version_no"] for v in a["versions"]), default=0) + 1
    with db.connect() as c:
        vid = c.execute(
            "INSERT INTO asset_versions(asset_id, version_no, label, description, voice, notes, images,"
            " parent_version_id, created_by, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (asset_id, next_no, label.strip() or f"Версия {next_no}", description, voice, notes, db.dumps(paths),
             base_version_id, uid(request), db.now())).lastrowid
        if activate:
            c.execute("UPDATE assets SET active_version_id=? WHERE id=?", (vid, asset_id))
    return get_asset(asset_id)


@app.post("/api/assets/{asset_id}/versions/{version_id}/activate", dependencies=[Depends(auth.need("library"))])
def activate_version(asset_id: int, version_id: int):
    with db.connect() as c:
        if not c.execute("SELECT 1 FROM asset_versions WHERE id=? AND asset_id=?", (version_id, asset_id)).fetchone():
            raise HTTPException(404, "Версия не найдена")
        c.execute("UPDATE assets SET active_version_id=? WHERE id=?", (version_id, asset_id))
    return get_asset(asset_id)


def _version_usage(c, asset_id: int, version_id: int) -> dict:
    """Где используется версия персонажа или локации: шоты, «Версии для этой серии», участники арок."""
    shots = []
    for r in c.execute("SELECT id, characters, location_version_id FROM shots"):
        chars = json.loads(r["characters"] or "[]")
        if r["location_version_id"] == version_id or any(ch.get("version_id") == version_id for ch in chars):
            shots.append(r["id"])
    cast = c.execute("SELECT COUNT(*) FROM episode_cast WHERE version_id=?", (version_id,)).fetchone()[0]
    arcs = [a["id"] for a in db.rows(c.execute("SELECT id, members FROM arcs"))
            if any(m.get("version_id") == version_id for m in a["members"])]
    return {"shots": shots, "cast": cast, "arcs": arcs}


@app.get("/api/assets/{asset_id}/versions/{version_id}/usage")
def version_usage(asset_id: int, version_id: int):
    with db.connect() as c:
        if not c.execute("SELECT 1 FROM asset_versions WHERE id=? AND asset_id=?", (version_id, asset_id)).fetchone():
            raise HTTPException(404, "Версия не найдена")
        u = _version_usage(c, asset_id, version_id)
        eps = c.execute(f"SELECT COUNT(DISTINCT episode_id) FROM shots WHERE id IN ({','.join('?' * len(u['shots'])) or 'NULL'})",
                        u["shots"]).fetchone()[0]
    return {"shots": len(u["shots"]), "episodes": eps, "cast": u["cast"], "arcs": len(u["arcs"])}


@app.delete("/api/assets/{asset_id}/versions/{version_id}", dependencies=[Depends(auth.need("library"))])
def delete_version(asset_id: int, version_id: int):
    """Удалить версию. Активную нельзя (у персонажа всегда есть рабочая версия). Где версия использовалась —
    в шотах, «Версиях для этой серии», участниках арок — подставляется активная; промпты таких шотов пересобираются.
    Фото версии удаляются с диска, если их не использует другая версия."""
    with db.connect() as c:
        a = c.execute("SELECT active_version_id FROM assets WHERE id=?", (asset_id,)).fetchone()
        v = c.execute("SELECT images FROM asset_versions WHERE id=? AND asset_id=?", (version_id, asset_id)).fetchone()
        if not a or not v:
            raise HTTPException(404, "Версия не найдена")
        active = a["active_version_id"]
        if version_id == active:
            raise HTTPException(400, "Это активная версия: сначала сделайте активной другую, потом удаляйте эту")
        u = _version_usage(c, asset_id, version_id)
        for sid in u["shots"]:
            r = c.execute("SELECT characters, location_version_id FROM shots WHERE id=?", (sid,)).fetchone()
            chars = [{**ch, "version_id": active} if ch.get("version_id") == version_id else ch
                     for ch in json.loads(r["characters"] or "[]")]
            loc = active if r["location_version_id"] == version_id else r["location_version_id"]
            c.execute("UPDATE shots SET characters=?, location_version_id=? WHERE id=?", (db.dumps(chars), loc, sid))
        c.execute("UPDATE episode_cast SET version_id=? WHERE version_id=?", (active, version_id))
        for arc in db.rows(c.execute("SELECT id, members FROM arcs")):
            if any(m.get("version_id") == version_id for m in arc["members"]):
                members = [{**m, "version_id": None} if m.get("version_id") == version_id else m for m in arc["members"]]
                c.execute("UPDATE arcs SET members=? WHERE id=?", (db.dumps(members), arc["id"]))
        c.execute("UPDATE asset_versions SET parent_version_id=NULL WHERE parent_version_id=?", (version_id,))
        images = json.loads(v["images"] or "[]")
        c.execute("DELETE FROM asset_versions WHERE id=?", (version_id,))
        db.renumber_versions(c, asset_id)
        still = {p for r in c.execute("SELECT images FROM asset_versions") for p in json.loads(r[0] or "[]")}
    # пересобрать промпты затронутых шотов (кроме закреплённых вручную и выложенных серий)
    ctx = Ctx()
    with db.connect() as c:
        for sh in db.rows(c.execute(f"SELECT s.* FROM shots s JOIN episodes e ON e.id=s.episode_id WHERE s.id IN "
                                    f"({','.join('?' * len(u['shots'])) or 'NULL'}) AND e.status<>'posted'", u["shots"])):
            refresh_shot(sh, ctx)
            save_shot(c, sh)
    root = db.MEDIA_DIR.resolve()
    for rel in images:
        f = (db.MEDIA_DIR / rel).resolve()
        if rel not in still and root in f.parents:
            f.unlink(missing_ok=True)
    return get_asset(asset_id)


def load_version(asset_id: int, version_id: int) -> tuple[dict, dict]:
    a = get_asset(asset_id)
    v = next((v for v in a["versions"] if v["id"] == version_id), None)
    if not v:
        raise HTTPException(404, "Версия не найдена")
    return a, v


@app.post("/api/assets/{asset_id}/versions/{version_id}/images", dependencies=[Depends(auth.need("library"))])
def add_version_images(asset_id: int, version_id: int, images: list[UploadFile] = File(default=[])):
    """Догрузить фото в версию (ракурсы со всех сторон). Первое фото версии остаётся главным референсом."""
    a, v = load_version(asset_id, version_id)
    paths = v["images"] + [save_upload(f, f"{a['kind']}s/{asset_id}") for f in images if f.filename]
    with db.connect() as c:
        c.execute("UPDATE asset_versions SET images=? WHERE id=?", (db.dumps(paths), version_id))
    refresh_all_prompts()
    return get_asset(asset_id)


class ImagesIn(BaseModel):
    images: list[str]


@app.put("/api/assets/{asset_id}/versions/{version_id}/images", dependencies=[Depends(auth.need("library"))])
def set_version_images(asset_id: int, version_id: int, body: ImagesIn):
    """Порядок фото (первое — главный референс) и удаление лишних. Новые пути сюда не принимаются."""
    _, v = load_version(asset_id, version_id)
    if not set(body.images) <= set(v["images"]):
        raise HTTPException(400, "Можно только переставить или убрать фото этой версии")
    with db.connect() as c:
        c.execute("UPDATE asset_versions SET images=? WHERE id=?", (db.dumps(list(dict.fromkeys(body.images))), version_id))
    refresh_all_prompts()
    return get_asset(asset_id)


# ---------- episodes ----------

def visible_or_404(request: Request, eid: int) -> None:
    """The editor (монтажёр) only sees episodes whose script is approved."""
    role = request.state.user["role"]
    if role == "editor" and not auth.can_see(role, load_episode_row(eid)["status"]):
        raise HTTPException(404, "Серия пока недоступна: сценарий ещё не согласован")


def episode_summaries(role: str = "admin") -> list[dict]:
    settings = db.get_settings()
    max_s = int(settings.get("max_shot_seconds") or 8)
    with db.connect() as c:
        eps = db.rows(c.execute("SELECT id, number, pinned, planned_date, title, status, posted_at, updated_at, "
                                "arc_id, position, synopsis, post_text, post_same, post_text_tiktok, post_text_reels, post_text_shorts, urgent FROM episodes "
                                "WHERE deleted_at='' ORDER BY number IS NULL, number, position, id"))
        stats = {r["episode_id"]: dict(r) for r in c.execute(
            "SELECT episode_id, COUNT(*) AS shots, COALESCE(SUM(duration),0) AS seconds, "
            "COALESCE(SUM(est_seconds),0) AS est, SUM(est_seconds > ?) AS over, "
            "SUM(selected_take_id IS NOT NULL) AS with_take, SUM(needs_redo) AS redo FROM shots GROUP BY episode_id",
            (max_s,))}
        fixes = {r[0]: r[1] for r in c.execute(
            "SELECT episode_id, COUNT(*) FROM comments WHERE is_fix=1 AND resolved=0 GROUP BY episode_id")}
    arc_nums = schedule.arc_numbers(eps, schedule.load_arcs())
    for e in eps:
        e.update(arc_nums[e["id"]])
        st = stats.get(e["id"], {})
        e.update(shots=st.get("shots", 0), seconds=st.get("seconds", 0), est=round(st.get("est", 0), 1),
                 over=st.get("over", 0) or 0, with_take=st.get("with_take", 0) or 0, redo=st.get("redo", 0) or 0,
                 open_fixes=fixes.get(e["id"], 0), date=schedule.date_for(e["number"], settings),
                 status_name=db.STATUS_NAMES.get(e["status"], e["status"]))
    return [e for e in eps if auth.can_see(role, e["status"])]


def _blurb(script: str, limit: int = 170) -> str:
    """Короткое описание серии из сценария: только действие и реплики, без «Шот N», локаций и камеры."""
    out = []
    for line in (script or "").splitlines():
        t = line.strip()
        if (not t or re.match(r"^(шот|кадр|серия)", t, re.I) or re.match(r"^(инт|нат)\.", t, re.I)
                or t.upper().startswith("КАМЕРА")):
            continue
        out.append(re.sub(r"@([^\s:]+)(?::\S+)?", r"\1", t))  # @Ксю:Мерч → Ксю
    text = " / ".join(out)
    return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0] + "…"


STATUS_PAST, STATUS_TOTAL = 5, 30


@app.get("/api/status-window")
def status_window(request: Request, month: str | None = None):
    """Страница «Статус»: 30 слотов выхода — 5 прошедших дней, сегодня и дальше вперёд (если сериал начался
    недавно, недостающие прошедшие дни добавляются впереди). Окно сдвигается само каждый день.
    Серии, которые роль не видит (монтажёр до согласования сценария), показываются только этапом: без названия,
    описания и ссылки (`locked`), чтобы картина оставалась полной.
    С `month=ГГГГ-ММ` вместо окна берутся слоты выхода этого календарного месяца (для сводки, плиток и полоски)."""
    settings = db.get_settings()
    if month:
        try:
            y, m = map(int, month.split("-"))
            d1 = date(y, m, 1)
        except ValueError:
            raise HTTPException(400, "Месяц в формате ГГГГ-ММ")
        d2 = (d1.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
        first = max(1, schedule.number_for(d1.isoformat(), settings))
        last = schedule.number_for(d2.isoformat(), settings)
    else:
        today_n = schedule.number_for(date.today().isoformat(), settings)
        first = max(1, today_n - STATUS_PAST)
        last = first + STATUS_TOTAL - 1
    role = request.state.user["role"]
    all_eps = episode_summaries()
    eps = [e for e in all_eps if e["number"] is not None and first <= e["number"] <= last]
    with db.connect() as c:
        scripts = {r["id"]: r["script"] or "" for r in c.execute("SELECT id, script FROM episodes")}
    for e in eps:
        e["has_script"] = bool(scripts.get(e["id"], "").strip())
        e["locked"] = not auth.can_see(role, e["status"])
        synopsis = " ".join((e.get("synopsis") or "").split())
        e["blurb"] = "" if e["locked"] else (_blurb(scripts.get(e["id"], "")) or synopsis[:170])
        if e["locked"]:
            e["title"] = "Сценарий ещё не согласован"
    arcs = schedule.load_arcs()
    by_num = {e["number"]: e for e in eps}
    none = {"arc_id": None, "arc_title": "", "arc_number": None}
    slots = [{"number": n, "date": schedule.date_for(n, settings),
              **({k: by_num[n][k] for k in none} if n in by_num else none)} for n in range(first, last + 1)]
    return {"today": date.today().isoformat(), "slots": slots, "episodes": eps,
            "arcs": [a for a in arcs if a["start_number"] and first <= a["start_number"] <= last],
            "backlog": sum(1 for e in all_eps if e["number"] is None and auth.can_see(role, e["status"]))}


# ---------- story arcs ----------
# Арка — группа серий (episodes.arc_id) со своим синопсисом, участниками и статусом согласования синопсисов.
# Серии арки идут в общей очереди выхода (1 серия в день); номер серии внутри арки считается заново с 1.

class ArcIn(BaseModel):
    title: str
    notes: str = ""           # синопсис арки / ключевые детали
    members: list[dict] = []  # [{asset_id, version_id|null}]
    period_start: str | None = None  # период арки (по желанию): когда она идёт; серии в неё добавляют вручную.
    period_end: str | None = None    # None — оставить как было


def _arc_values(a: ArcIn) -> tuple:
    title = a.title.strip()
    if not title:
        raise HTTPException(400, "Назовите арку")
    members = [{"asset_id": int(m["asset_id"]), "version_id": int(m["version_id"]) if m.get("version_id") else None}
               for m in a.members if m.get("asset_id")]
    for d in (a.period_start, a.period_end):
        try:
            _ = d and date.fromisoformat(d)
        except ValueError:
            raise HTTPException(400, "Дата периода в формате ГГГГ-ММ-ДД")
    if a.period_start and a.period_end and a.period_end < a.period_start:
        raise HTTPException(400, "Конец периода арки раньше начала")
    return title, a.notes.strip(), db.dumps(members), a.period_start, a.period_end


def _arc_or_404(c, arc_id: int) -> dict:
    row = c.execute("SELECT * FROM arcs WHERE id=?", (arc_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Арка не найдена")
    return db.row(row)


def _arc_id(arc_id: str) -> int | None:
    """В адресах «none» = серии без арки."""
    return None if arc_id == "none" else int(arc_id)


@app.get("/api/arcs")
def list_arcs():
    settings = db.get_settings()
    names = dict(db.ARC_STATUSES)
    return [{**a, "status_name": names.get(a["status"], a["status"]),
             "start_date": schedule.date_for(a["start_number"], settings),
             "end_date": schedule.date_for(a["end_number"], settings)} for a in schedule.load_arcs()]


@app.post("/api/arcs", dependencies=[Depends(auth.need("arcs"))])
def create_arc(a: ArcIn, request: Request):
    title, notes, members, p1, p2 = _arc_values(a)
    with db.connect() as c:
        # start_number больше не используется (день начала считается по сериям арки), но в базах до v2 он NOT NULL
        arc_id = c.execute("INSERT INTO arcs(title, start_number, notes, members, period_start, period_end, created_by, "
                           "created_at) VALUES (?,0,?,?,?,?,?,?)",
                           (title, notes, members, p1 or "", p2 or "", uid(request), db.now())).lastrowid
    return next(x for x in list_arcs() if x["id"] == arc_id)


@app.put("/api/arcs/{arc_id}", dependencies=[Depends(auth.need("arcs"))])
def update_arc(arc_id: int, a: ArcIn):
    title, notes, members, p1, p2 = _arc_values(a)
    with db.connect() as c:
        _arc_or_404(c, arc_id)
        old = _arc_or_404(c, arc_id)
        p1 = old["period_start"] if p1 is None else p1
        p2 = old["period_end"] if p2 is None else p2
        if p1 and p2 and p2 < p1:
            raise HTTPException(400, "Конец периода арки раньше начала")
        c.execute("UPDATE arcs SET title=?, notes=?, members=?, period_start=?, period_end=? WHERE id=?",
                  (title, notes, members, p1, p2, arc_id))
    return next(x for x in list_arcs() if x["id"] == arc_id)


@app.delete("/api/arcs/{arc_id}", dependencies=[Depends(auth.need("arcs"))])
def delete_arc(arc_id: int):
    """Серии не удаляются: они остаются в очереди «без арки»."""
    with db.connect() as c:
        c.execute("UPDATE episodes SET arc_id=NULL WHERE arc_id=?", (arc_id,))
        c.execute("DELETE FROM arcs WHERE id=?", (arc_id,))
    return list_arcs()


class WeaveIn(BaseModel):
    with_arc: int
    pattern: str = "1:1"  # сколько серий этой арки, потом сколько серий второй: 1:1, 2:1, 1:2


@app.post("/api/arcs/{arc_id}/weave", dependencies=[Depends(auth.need("schedule"))])
def weave_arcs(arc_id: int, w: WeaveIn, request: Request):
    """Перемешать две арки: их серии в очереди чередуются по шаблону на тех же днях, что они уже занимают."""
    try:
        a_n, b_n = (max(1, int(x)) for x in w.pattern.split(":"))
    except ValueError:
        raise HTTPException(400, "Шаблон чередования вида 1:1")
    with db.connect() as c:
        arc_a, arc_b = _arc_or_404(c, arc_id), _arc_or_404(c, w.with_arc)
    if arc_id == w.with_arc:
        raise HTTPException(400, "Выберите другую арку")
    moved = schedule.weave(arc_id, w.with_arc, a_n, b_n)
    if not moved:
        raise HTTPException(400, "Нечего чередовать: у арок нет серий в очереди, которые можно двигать")
    db.log(None, uid(request), f"Арки «{arc_a['title']}» и «{arc_b['title']}» перемешаны ({w.pattern})")
    return {"ok": True, "moved": moved}


class ArcArchiveIn(BaseModel):
    archived: bool


@app.post("/api/arcs/{arc_id}/archive", dependencies=[Depends(auth.need("arcs"))])
def archive_arc(arc_id: int, a: ArcArchiveIn):
    """Закончившаяся арка уходит в архив: с главной «Серий» пропадает, но остаётся доступной, чтобы вспомнить сюжет."""
    with db.connect() as c:
        _arc_or_404(c, arc_id)
        c.execute("UPDATE arcs SET archived=? WHERE id=?", (int(a.archived), arc_id))
    return next(x for x in list_arcs() if x["id"] == arc_id)


class ArcStatusIn(BaseModel):
    status: str


@app.post("/api/arcs/{arc_id}/status", dependencies=[Depends(auth.need("arcs"))])
def set_arc_status(arc_id: int, s: ArcStatusIn, request: Request):
    """Согласование синопсисов с клиентом идёт по арке целиком: статус переносится на её серии,
    которые ещё на этапе синопсиса (серии, ушедшие дальше, не трогаются)."""
    if s.status not in db.ARC_TO_EPISODE:
        raise HTTPException(400, "Неизвестный статус арки")
    with db.connect() as c:
        arc = _arc_or_404(c, arc_id)
        c.execute("UPDATE arcs SET status=? WHERE id=?", (s.status, arc_id))
        stage = tuple(db.SYNOPSIS_STAGE)
        changed = [r[0] for r in c.execute(
            f"SELECT id FROM episodes WHERE arc_id=? AND deleted_at='' AND status IN ({','.join('?' * len(stage))})", (arc_id, *stage))]
        c.execute(f"UPDATE episodes SET status=?, updated_at=? WHERE arc_id=? AND deleted_at='' AND status IN ({','.join('?' * len(stage))})",
                  (db.ARC_TO_EPISODE[s.status], db.now(), arc_id, *stage))
    names = dict(db.ARC_STATUSES)
    for eid in changed:
        db.log(eid, uid(request), f"Арка «{arc['title']}»: {names[s.status].lower()}")
    return {"ok": True}


class CardIn(BaseModel):
    title: str = ""
    synopsis: str = ""
    draft: bool = False


@app.post("/api/arcs/{arc_id}/episodes", dependencies=[Depends(auth.need("schedule"))])
def add_arc_episode(arc_id: str, card: CardIn, request: Request):
    """Новая карточка серии в арке: встаёт в очередь в конец арки (или в черновики)."""
    aid = _arc_id(arc_id)
    if aid is not None:
        with db.connect() as c:
            _arc_or_404(c, aid)
    e = EpisodeIn(title=card.title, synopsis=card.synopsis, arc_id=aid, backlog=card.draft)
    return get_episode(create_episode_row(e, uid(request)))


class OrderIn(BaseModel):
    ids: list[int]


@app.post("/api/arcs/{arc_id}/order", dependencies=[Depends(auth.need("schedule"))])
def order_arc(arc_id: str, o: OrderIn, request: Request):
    schedule.reorder_arc(_arc_id(arc_id), o.ids)
    return {"ok": True}


@app.post("/api/schedule/reorder", dependencies=[Depends(auth.need("schedule"))])
def reorder_schedule(o: OrderIn):
    schedule.reorder_queue(o.ids)
    return {"ok": True}


@app.get("/api/production")
def production(request: Request):
    """Доска «Продакшн»: все серии в очереди. Серии, которые роль ещё не видит (ТЗ не готово), приходят
    с `locked` — только название, дата и арка, без открытия."""
    role = request.state.user["role"]
    eps = [e for e in episode_summaries() if e["number"] is not None]
    for e in eps:
        e["locked"] = not auth.can_see(role, e["status"])
    return eps


class EpisodeArcIn(BaseModel):
    arc_id: int | None = None


@app.post("/api/episodes/{eid}/arc", dependencies=[Depends(auth.need("schedule"))])
def move_to_arc(eid: int, a: EpisodeArcIn, request: Request):
    """Перенести серию в другую арку (в конец её очереди)."""
    ep = load_episode_row(eid)
    with db.connect() as c:
        if a.arc_id is not None:
            _arc_or_404(c, a.arc_id)
        c.execute("UPDATE episodes SET arc_id=? WHERE id=?", (a.arc_id, eid))
    if ep["number"] is not None and not ep["pinned"]:
        schedule.append_to_arc(eid, a.arc_id)
    db.log(eid, uid(request), "Перенесена в другую арку")
    return {"ok": True}


@app.post("/api/episodes/{eid}/queue", dependencies=[Depends(auth.need("schedule"))])
def queue_episode(eid: int, request: Request):
    """Из черновиков обратно в очередь — в конец своей арки."""
    ep = load_episode_row(eid)
    number = schedule.append_to_arc(eid, ep["arc_id"])
    db.log(eid, uid(request), f"Из черновиков в очередь: выход {schedule.date_for(number)}")
    return {"ok": True}


MONTHS_GEN = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября",
              "ноября", "декабря"]


def _ru_date(iso: str | None) -> str:
    if not iso:
        return ""
    d = date.fromisoformat(iso)
    return f"{d.day} {MONTHS_GEN[d.month - 1]}"


def _streaks() -> str:
    """Вертикальные «шторки» тёмного слайда PARI: полосы разной ширины и плотности поверх бирюзового свечения."""
    import random
    rnd, x, out = random.Random(7), 0, []
    while x < 1280:
        w = rnd.randint(14, 46)
        out.append(f'<i style="left:{x}px;width:{w}px;--a:{rnd.uniform(.25, .7):.2f}"></i>')
        x += w - rnd.randint(2, 8)
    return '<div class="streaks">' + "".join(out) + "</div>"


def _synopsis_html(title: str, filename: str, msg: str, cover: str, pages: str) -> str:
    """Страница-презентация синопсисов (16:9, стиль PARI) со «Скачать PDF»: общая для PDF арки и PDF за период."""
    from html import escape as h
    return f"""<!doctype html><html lang="ru"><head><meta charset="utf-8">
<title>{h(title)}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Fira+Sans+Extra+Condensed:wght@800;900&family=Inter:wght@400;500;600&display=swap" rel="stylesheet">
<style>
  @page {{ size: 338.67mm 190.5mm; margin: 0; }}
  :root {{ --teal: #00c7b2; --teal-light: #77f6e8; --card: #0a3833; --ink: #161616; --text: #ffffff; --muted: #b9c9c6; }}
  * {{ box-sizing: border-box; -webkit-print-color-adjust: exact; print-color-adjust: exact; }}
  body {{ margin: 0; background: #2b2b2b; font: 15px/1.45 Inter, system-ui, sans-serif; color: var(--text); }}
  .bar {{ position: sticky; top: 0; z-index: 5; display: flex; gap: 12px; align-items: center; padding: 12px 20px;
         background: #111; color: #ddd; font-size: 14px; }}
  .bar button {{ background: var(--teal); color: #04110e; border: 0; border-radius: 8px; padding: 9px 16px; font-weight: 600; cursor: pointer; }}
  .bar button.ghost {{ background: transparent; color: #ddd; border: 1px solid #555; }}
  .bar button:disabled {{ opacity: .6; cursor: default; }}
  .sheet {{ width: 1280px; height: 720px; margin: 20px auto; padding: 56px 64px; position: relative; overflow: hidden; page-break-after: always; }}
  .head {{ font: 900 108px/.92 "Fira Sans Extra Condensed", Impact, sans-serif; text-transform: uppercase; letter-spacing: -.5px; margin: 0; }}
  .pno {{ position: absolute; right: 40px; bottom: 26px; font-size: 22px; color: var(--teal); }}
  .pno.dark {{ color: #111; }}
  /* обложка: тёмный слайд */
  .sheet.cover {{ background:
      radial-gradient(ellipse 36% 90% at 50% 80%, rgba(0,199,178,.8), rgba(0,199,178,.22) 55%, rgba(0,199,178,0) 78%),
      radial-gradient(ellipse 24% 70% at 80% 10%, rgba(0,199,178,.32), rgba(0,199,178,0) 72%),
      var(--ink); }}
  .streaks {{ position: absolute; inset: 0; pointer-events: none; }}
  .streaks i {{ position: absolute; top: 0; bottom: 0; background: linear-gradient(90deg, rgba(22,22,22,0), rgba(22,22,22,var(--a)), rgba(22,22,22,0)); }}
  .cover > *:not(.streaks):not(.pno) {{ position: relative; }}
  .cover .kicker {{ color: var(--teal); font: 800 20px "Fira Sans Extra Condensed", sans-serif; letter-spacing: .12em; text-transform: uppercase; margin-bottom: 22px; }}
  .cover .cols {{ display: grid; grid-template-columns: 1fr 480px; gap: 48px; margin-top: 34px; }}
  .cover .lead {{ font-size: 17px; white-space: pre-wrap; max-width: 640px; }}
  .cover .meta {{ margin-top: 18px; color: var(--muted); font-size: 15px; }}
  .glass {{ background: rgba(18,24,23,.55); border: 1px solid rgba(255,255,255,.14); border-radius: 10px; padding: 22px 26px; }}
  .glass h4 {{ margin: 0 0 10px; font: 800 18px "Fira Sans Extra Condensed", sans-serif; letter-spacing: .06em; text-transform: uppercase; color: var(--teal); }}
  .glass ul {{ margin: 0; padding: 0; list-style: none; display: flex; flex-direction: column; gap: 7px; }}
  .glass li span {{ color: var(--muted); font-size: 13px; margin-left: 6px; }}
  .chips {{ display: flex; flex-wrap: wrap; gap: 8px; margin-top: 16px; }}
  .chips span {{ border: 1px solid rgba(0,199,178,.6); color: var(--teal-light); border-radius: 99px; padding: 4px 14px; font-size: 14px; }}
  /* листы с сериями: бирюзовый слайд */
  .sheet.teal {{ background: linear-gradient(180deg, var(--teal) 0%, #10d9c3 70%, var(--teal-light) 100%); }}
  .shape {{ position: absolute; background: rgba(255,255,255,.10); }}
  .shape.s1 {{ width: 520px; height: 520px; left: -200px; top: -260px; transform: rotate(32deg); }}
  .shape.s2 {{ width: 640px; height: 640px; right: -260px; bottom: -380px; transform: rotate(-28deg); background: rgba(255,255,255,.14); }}
  /* Каждая серия — две строки общей сетки (заголовок и карточка, subgrid): у серий одного ряда заголовки занимают
     одинаковую высоту и прижаты к карточке, а карточки начинаются на одной линии и одной высоты, сколько бы строк ни было в заголовке */
  .grid {{ position: relative; display: grid; grid-template-columns: repeat(3, 1fr); column-gap: 18px; row-gap: 0; }}
  .ep {{ display: grid; grid-row: span 2; grid-template-rows: subgrid; }}
  .ep:nth-child(n+4) h3 {{ margin-top: 26px; }}
  .ep h3 {{ margin: 0 0 12px; align-self: end; text-align: center; color: #111; font: 900 28px/1 "Fira Sans Extra Condensed", Impact, sans-serif; text-transform: uppercase; }}
  .card {{ background: var(--card); border-radius: 6px; padding: 22px 28px; min-height: 214px; }}
  .card .date {{ color: var(--teal-light); font-size: 13px; margin-bottom: 10px; }}
  .card p {{ margin: 0; white-space: pre-wrap; }}
  /* выгрузка за период: у каждой серии метка её арки цветом арки (арки идут вперемешку) */
  .card.arc {{ border-left: 5px solid var(--a); }}
  .card .arcl {{ display: flex; align-items: center; gap: 7px; font-size: 13px; color: #e8f3f1; margin-bottom: 6px; }}
  .card .arcl i, .dot {{ width: 9px; height: 9px; border-radius: 50%; background: var(--a); flex: none; display: inline-block; }}
  .card .arcl span {{ color: var(--muted); }}
  /* обложка выгрузки за период: арки карточками — цветная полоса сверху, даты, синопсис и полоска дней периода */
  .head.period {{ font-size: 88px; white-space: nowrap; }}
  .head.period small {{ font-size: 48px; color: var(--teal); margin-left: 18px; }}
  .arc-grid {{ display: grid; gap: 16px; }}
  .arc-grid.c1 {{ grid-template-columns: minmax(0, 620px); }}
  .arc-grid.c2 {{ grid-template-columns: repeat(2, 1fr); }}
  .arc-grid.c3 {{ grid-template-columns: repeat(3, 1fr); }}
  .arc-c {{ background: rgba(18,24,23,.62); border: 1px solid rgba(255,255,255,.12); border-top: 4px solid var(--a);
           border-radius: 10px; padding: 16px 20px 18px; display: flex; flex-direction: column; gap: 6px; }}
  .arc-c .arc-n {{ font-size: 13px; color: var(--a); font-weight: 600; }}
  .arc-c h5 {{ margin: 0; font: 900 30px/1 "Fira Sans Extra Condensed", Impact, sans-serif; text-transform: uppercase; color: #fff; }}
  .arc-c p {{ margin: 0; font-size: 14px; line-height: 1.4; color: var(--muted); display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden; }}
  .arc-c .strip {{ display: flex; gap: 2px; margin-top: auto; padding-top: 10px; }}
  .arc-c .strip i {{ flex: 1; height: 8px; border-radius: 2px; background: rgba(255,255,255,.08); }}
  .arc-c .strip i.other {{ background: rgba(255,255,255,.22); }}
  .arc-c .strip i.on {{ background: var(--a); }}
  .arc-grid.dense {{ gap: 10px; }}
  .arc-grid.dense .arc-c {{ padding: 10px 14px 12px; }}
  .arc-grid.dense .arc-c h5 {{ font-size: 22px; }}
  .arc-grid.dense .arc-c p {{ -webkit-line-clamp: 1; }}
  @media print {{ body {{ background: none; }} .bar {{ display: none; }} .sheet {{ margin: 0; zoom: 1; }} }}
</style></head><body>
<div class="bar"><button id="dl" onclick="downloadPdf()">Скачать PDF</button>
  <button class="ghost" onclick="print()">Печать</button>
  <span id="msg">{msg}</span></div>
{cover}
{pages}
<script src="https://cdnjs.cloudflare.com/ajax/libs/html2canvas/1.4.1/html2canvas.min.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/jspdf/2.5.1/jspdf.umd.min.js"></script>
<script>
// «Скачать PDF»: каждый лист (A4, альбомный) снимается картинкой и складывается в файл — без окна печати
async function downloadPdf() {{
  const btn = document.getElementById("dl"), msg = document.getElementById("msg");
  if (!window.html2canvas || !window.jspdf) {{ msg.textContent = "Нет интернета для сборки PDF — нажмите «Печать» и выберите «Сохранить как PDF»."; return; }}
  btn.disabled = true; btn.textContent = "Готовлю PDF…";
  try {{
    await document.fonts.ready;
    const pdf = new window.jspdf.jsPDF({{ orientation: "landscape", unit: "px", format: [1280, 720], hotfixes: ["px_scaling"] }});
    const sheets = [...document.querySelectorAll(".sheet")];
    for (let i = 0; i < sheets.length; i++) {{
      msg.textContent = `Лист ${{i + 1}} из ${{sheets.length}}…`;
      const canvas = await html2canvas(sheets[i], {{ scale: 2, backgroundColor: "#161616", useCORS: true, logging: false }});
      if (i) pdf.addPage([1280, 720], "landscape");
      pdf.addImage(canvas.toDataURL("image/jpeg", 0.92), "JPEG", 0, 0, 1280, 720);
    }}
    pdf.save({json.dumps(filename, ensure_ascii=False)});
    msg.textContent = "Готово: файл в «Загрузках».";
  }} catch (e) {{
    msg.textContent = "Не получилось собрать PDF — нажмите «Печать» и выберите «Сохранить как PDF».";
  }} finally {{ btn.disabled = false; btn.textContent = "Скачать PDF"; }}
}}
</script>
</body></html>"""


ARC_COLORS = ["#8f86e8", "#e08a6a", "#4fb894", "#d77fa6", "#6fa0dc", "#c9a24a", "#8fb85a", "#b58ad8"]  # как arcColor в core.js


def _arc_color(arc_id) -> str:
    return ARC_COLORS[(arc_id - 1) % len(ARC_COLORS)] if arc_id else "#9aa5a3"


def _plural(n: int, one: str, few: str, many: str) -> str:
    return one if n % 10 == 1 and n % 100 != 11 else few if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14 else many


def _synopsis_pages(eps: list[dict], card) -> str:
    per = 6
    return "".join(f"""<section class="sheet teal"><div class="shape s1"></div><div class="shape s2"></div>
  <div class="grid">{"".join(card(i, e) for i, e in enumerate(eps[k:k + per], k + 1))}</div>
  <div class="pno dark">{k // per + 2}</div></section>""" for k in range(0, len(eps), per))


@app.get("/api/arcs/{arc_id}/pdf")
def arc_pdf(arc_id: int):
    """Синопсисы арки для клиента в стиле презентации PARI (16:9): «Скачать PDF» собирает файл в браузере,
    «Печать» — запасной путь.
    В выгрузку идут серии арки, стоящие в очереди (черновики — нет)."""
    from html import escape as h
    arc = next((a for a in list_arcs() if a["id"] == arc_id), None)
    if not arc:
        raise HTTPException(404, "Арка не найдена")
    eps = [e for e in episode_summaries() if e["arc_id"] == arc_id and e["number"] is not None]
    eps.sort(key=lambda e: e["number"])
    series = db.get_settings().get("series_title") or "Сериал"
    assets = {a["id"]: a for a in load_assets()}
    members = []
    for m in arc["members"]:
        a = assets.get(m["asset_id"])
        if a:
            v = next((v for v in a["versions"] if v["id"] == m["version_id"]), None)
            members.append(a["name"] + (f" · {v['label']}" if v else ""))
    span = (f"{_ru_date(arc['start_date'])} — {_ru_date(arc['end_date'])}" if arc["start_date"] else "даты не назначены")
    n = len(eps)

    def card(_, e):
        return f"""<article class="ep"><h3>{e['arc_number']}. «{h((e['title'] or 'Без названия').upper())}»</h3>
          <div class="card"><div class="date">{_ru_date(e['date'])}</div><p>{h(e['synopsis'] or 'Синопсис пока не написан.')}</p></div></article>"""
    toc = "".join(f"<li><b>{e['arc_number']}.</b> «{h(e['title'] or 'Без названия')}» <span>{_ru_date(e['date'])}</span></li>" for e in eps)
    cover = f"""<section class="sheet cover">{_streaks()}
  <div class="kicker">{h(series)} · синопсисы на согласование</div>
  <h1 class="head">{h(arc['title'])}</h1>
  <div class="cols">
    <div><div class="lead">{h(arc['notes']) if arc['notes'] else 'Синопсис арки пока не написан.'}</div>
      <div class="meta">{n} {_plural(n, "серия", "серии", "серий")} · {span}</div>
      {('<div class="chips">' + ''.join(f'<span>{h(m)}</span>' for m in members) + '</div>') if members else ''}</div>
    <div class="glass"><h4>Серии арки</h4><ul>{toc or '<li>Серий пока нет</li>'}</ul></div>
  </div>
  <div class="pno">1</div></section>"""
    msg = f"Файл сохранится в «Загрузки». Серий в выгрузке: {n}{' (черновики не входят)' if arc['drafts'] else ''}."
    return Response(_synopsis_html(f"{arc['title']} — синопсисы", f"{arc['title']} — синопсисы.pdf", msg, cover,
                                   _synopsis_pages(eps, card)), media_type="text/html")


@app.get("/api/synopsis-pdf")
def period_pdf(start: str, end: str):
    """Синопсисы всех серий за период (с «Серий» → «PDF синопсисов»): арки идут вперемешку, поэтому у каждой серии
    метка её арки цветом арки, а на обложке — период, число серий и арки периода."""
    from html import escape as h
    try:
        d0, d1 = date.fromisoformat(start), date.fromisoformat(end)
    except ValueError:
        raise HTTPException(400, "Даты в формате ГГГГ-ММ-ДД")
    if d1 < d0:
        raise HTTPException(400, "Конец периода раньше начала")
    eps = sorted((e for e in episode_summaries() if e["date"] and start <= e["date"] <= end), key=lambda e: e["number"])
    arcs = {a["id"]: a for a in list_arcs()}
    series = db.get_settings().get("series_title") or "Сериал"
    n = len(eps)
    in_period = []
    for e in eps:
        if e["arc_id"] not in in_period:
            in_period.append(e["arc_id"])
    span = f"{_ru_date(start)} — {_ru_date(end)}"
    year = f" {d1.year}" if d1.year != date.today().year else ""

    def arc_label(e):
        return f"{h(e['arc_title'])} <span>· серия {e['arc_number']}</span>" if e["arc_id"] else "Вне арки"

    def card(i, e):
        return f"""<article class="ep"><h3>{i}. «{h((e['title'] or 'Без названия').upper())}»</h3>
          <div class="card arc" style="--a:{_arc_color(e['arc_id'])}"><div class="arcl"><i></i>{arc_label(e)}</div>
            <div class="date">{_ru_date(e['date'])}</div><p>{h(e['synopsis'] or 'Синопсис пока не написан.')}</p></div></article>"""
    # дни периода: на полоске арки закрашены дни её серий — видно, как арки идут вперемешку
    days, d = [], d0
    while d <= d1:
        days.append(d.isoformat())
        d += timedelta(days=1)
    by_day = {e["date"]: e["arc_id"] for e in eps}

    def arc_card(aid):
        own = [e for e in eps if e["arc_id"] == aid]
        c = len(own)
        arc = arcs.get(aid)
        notes = " ".join((arc["notes"] if arc else "Праздники, события, отдельные ролики.").split())
        strip = "".join(f'<i class="{"on" if by_day.get(x, "-") == aid else "other" if x in by_day else ""}"></i>' for x in days)
        when = (_ru_date(own[0]["date"]) if c == 1 else f"{_ru_date(own[0]['date'])} — {_ru_date(own[-1]['date'])}")
        return f"""<div class="arc-c" style="--a:{_arc_color(aid)}">
          <div class="arc-n">{c} {_plural(c, "серия", "серии", "серий")} · {when}</div>
          <h5>{h(arc["title"]) if arc else "Вне арки"}</h5>
          <p>{h(notes[:170])}{"…" if len(notes) > 170 else ""}</p>
          <div class="strip">{strip}</div></div>"""
    cols = 3 if len(in_period) in (3, 5, 6) or len(in_period) > 6 else 2 if len(in_period) in (2, 4) else 1
    cover = f"""<section class="sheet cover">{_streaks()}
  <div class="kicker">{h(series)} · синопсисы на согласование</div>
  <h1 class="head period">{span}{f'<small>{year}</small>' if year else ''}</h1>
  <div class="meta" style="margin:12px 0 22px;font-size:18px;color:#fff">{n} {_plural(n, "серия", "серии", "серий")} · {len(in_period)} {_plural(len(in_period), "арка", "арки", "арок")}{" идут вперемешку" if len(in_period) > 1 else ""}</div>
  <div class="arc-grid c{cols}{' dense' if len(in_period) > 6 else ''}">{"".join(arc_card(a) for a in in_period) or '<p>В этот период серий нет.</p>'}</div>
  <div class="pno">1</div></section>"""
    name = f"Синопсисы {_ru_date(start)} — {_ru_date(end)}"
    msg = f"Файл сохранится в «Загрузки». Серий в выгрузке: {n} (черновики без даты не входят)."
    return Response(_synopsis_html(name, name + ".pdf", msg, cover, _synopsis_pages(eps, card)), media_type="text/html")


@app.get("/api/episodes")
def list_episodes(request: Request):
    return episode_summaries(request.state.user["role"])


@app.get("/api/schedule")
def get_schedule(start: str, end: str, request: Request):
    d0, d1 = date.fromisoformat(start), date.fromisoformat(end)
    eps = [e for e in episode_summaries(request.state.user["role"]) if e["date"] and start <= e["date"] <= end]
    with db.connect() as c:
        custom = db.rows(c.execute("SELECT * FROM calendar_events"))
    events = []
    d = d0
    while d <= d1:
        md = d.strftime("%m-%d")
        if md in HOLIDAYS:
            events.append({"date": d.isoformat(), "title": HOLIDAYS[md], "builtin": True})
        for ev in custom:
            hit = _event_start(ev, d)
            if hit:
                first, span = hit
                events.append({"id": ev["id"], "date": d.isoformat(), "title": ev["title"], "builtin": False,
                               "first": first == d, "last": (d - first).days == span, "occ_start": first.isoformat(),
                               "ev_date": ev["date"], "ev_end": ev["end_date"] or ev["date"], "yearly": bool(ev["yearly"]),
                               "color": ev["color"]})
        d += timedelta(days=1)
    arcs = [a for a in list_arcs() if a["start_date"] and start <= a["start_date"] <= end]
    return {"episodes": eps, "events": events, "arcs": arcs}


def _event_start(ev: dict, d: date) -> tuple[date, int] | None:
    """(первый день, длина в днях) того вхождения своего события, в которое попадает день `d` (None — не попадает).
    Событие может тянуться на несколько дней (`end_date`) и повторяться каждый год."""
    start = date.fromisoformat(ev["date"])
    span = (date.fromisoformat(ev["end_date"]) - start).days if ev["end_date"] else 0
    for year in ((d.year, d.year - 1) if ev["yearly"] else (start.year,)):
        try:
            s = start.replace(year=year)
        except ValueError:  # 29 февраля в невисокосный год
            continue
        if s <= d <= s + timedelta(days=max(span, 0)):
            return s, max(span, 0)
    return None


class CalendarEventIn(BaseModel):
    date: str
    title: str
    yearly: bool = False
    end_date: str = ""
    color: str = ""  # цвет своего события в календаре, «#rrggbb» (пусто — цвет по умолчанию)


def _event_color(v: str) -> str:
    v = (v or "").strip()
    return v if re.fullmatch(r"#[0-9a-fA-F]{6}", v) else ""


def _event_values(e: CalendarEventIn) -> tuple:
    start = date.fromisoformat(e.date)
    end = date.fromisoformat(e.end_date) if e.end_date else start
    if end < start:
        raise HTTPException(400, "Событие не может заканчиваться раньше начала")
    title = e.title.strip()
    if not title:
        raise HTTPException(400, "Введите название")
    return start.isoformat(), title, int(e.yearly), end.isoformat() if end > start else ""


@app.post("/api/calendar-events", dependencies=[Depends(auth.need("schedule"))])
def add_calendar_event(e: CalendarEventIn, request: Request):
    d, title, yearly, end = _event_values(e)
    with db.connect() as c:
        c.execute("INSERT INTO calendar_events(date, title, yearly, end_date, color, created_by) VALUES (?,?,?,?,?,?)",
                  (d, title, yearly, end, _event_color(e.color), uid(request)))
    return {"ok": True}


@app.get("/api/calendar-events")
def list_calendar_events():
    """Все свои события для меню «Свои события» в календаре (ближайшие сверху)."""
    with db.connect() as c:
        return db.rows(c.execute("SELECT id, date, title, yearly, end_date, color FROM calendar_events ORDER BY date, id"))


@app.put("/api/calendar-events/{ev_id}", dependencies=[Depends(auth.need("schedule"))])
def update_calendar_event(ev_id: int, e: CalendarEventIn):
    d, title, yearly, end = _event_values(e)
    with db.connect() as c:
        c.execute("UPDATE calendar_events SET date=?, title=?, yearly=?, end_date=?, color=? WHERE id=?",
                  (d, title, yearly, end, _event_color(e.color), ev_id))
    return {"ok": True}


@app.delete("/api/calendar-events/{ev_id}", dependencies=[Depends(auth.need("schedule"))])
def delete_calendar_event(ev_id: int):
    with db.connect() as c:
        c.execute("DELETE FROM calendar_events WHERE id=?", (ev_id,))
    return {"ok": True}


class AnchorIn(BaseModel):
    number: int
    date: str


@app.put("/api/schedule/anchor", dependencies=[Depends(auth.need("schedule"))])
def set_anchor(a: AnchorIn):
    date.fromisoformat(a.date)
    with db.connect() as c:
        c.execute("UPDATE settings SET value=? WHERE key='anchor_number'", (str(a.number),))
        c.execute("UPDATE settings SET value=? WHERE key='anchor_date'", (a.date,))
        schedule.normalize(c)
    return {"ok": True}


@app.post("/api/schedule/compact", dependencies=[Depends(auth.need("schedule"))])
def compact_schedule():
    schedule.compact()
    return {"ok": True}


class EpisodeIn(BaseModel):
    title: str = ""
    script: str = ""
    synopsis: str = ""
    arc_id: int | None = None
    number: int | None = None
    date: str | None = None
    backlog: bool = False
    urgent: bool = False  # «срочная серия»: наверх в «Продакшне» и значок в меню


def create_episode_row(e: EpisodeIn, user_id) -> int:
    with db.connect() as c:
        status = "dev" if e.script.strip() else "synopsis"
        if e.arc_id and status == "synopsis":  # новая серия согласованной арки сразу идёт со статусом арки
            arc = c.execute("SELECT status FROM arcs WHERE id=?", (e.arc_id,)).fetchone()
            status = db.ARC_TO_EPISODE.get(arc["status"], status) if arc else status
        pos = c.execute("SELECT COALESCE(MAX(position), -1) + 1 FROM episodes WHERE arc_id IS ?", (e.arc_id,)).fetchone()[0]
        eid = c.execute("INSERT INTO episodes(title, script, synopsis, status, arc_id, position, urgent, created_by, "
                        "created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (e.title.strip(), e.script, e.synopsis.strip(), status, e.arc_id, pos, int(e.urgent), user_id,
                         db.now(), db.now())).lastrowid
    if e.date:
        schedule.place(eid, e.date)
    elif e.number:
        schedule.move(eid, e.number)
    elif not e.backlog:
        schedule.append_to_arc(eid, e.arc_id)
    db.log(eid, user_id, "Серия создана" + (" — срочная" if e.urgent else ""))
    if e.script.strip():
        apply_breakdown(eid)
        promote_if_ready(eid, user_id)
    return eid


@app.post("/api/episodes", dependencies=[Depends(auth.need("episodes"))])
def create_episode(e: EpisodeIn, request: Request):
    return get_episode(create_episode_row(e, uid(request)))


def decode_text(raw: bytes) -> str:
    for enc in ("utf-8-sig", "cp1251"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    raise HTTPException(400, "Не удалось прочитать файл: нужен текст в UTF-8 или Windows-1251")


@app.post("/api/episodes/upload", dependencies=[Depends(auth.need("episodes"))])
async def upload_episode(request: Request, file: UploadFile = File(...), title: str = Form(""),
                         date_: str = Form("", alias="date"), backlog: bool = Form(False)):
    text = decode_text(await file.read())
    e = EpisodeIn(title=title or Path(file.filename or "").stem, script=text, date=date_ or None, backlog=backlog)
    return get_episode(create_episode_row(e, uid(request)))


@app.post("/api/read-text", dependencies=[Depends(auth.need("episodes", "script"))])
async def read_text(file: UploadFile = File(...)):
    return {"text": decode_text(await file.read()), "name": Path(file.filename or "").stem}


@app.get("/api/episodes/{eid}")
def api_get_episode(eid: int, request: Request):
    visible_or_404(request, eid)
    return get_episode(eid)


def get_episode(eid: int) -> dict:
    ep = load_episode_row(eid)
    ctx = Ctx()
    with db.connect() as c:
        shots = db.rows(c.execute("SELECT * FROM shots WHERE episode_id=? ORDER BY idx", (eid,)))
        takes = db.rows(c.execute("SELECT t.* FROM takes t JOIN shots s ON s.id=t.shot_id WHERE s.episode_id=? "
                                  "ORDER BY t.take_no", (eid,)))
        open_fixes = db.rows(c.execute("SELECT shot_id, COUNT(*) AS n FROM comments WHERE episode_id=? AND is_fix=1 "
                                       "AND resolved=0 GROUP BY shot_id", (eid,)))
    by_shot: dict[int, list] = {}
    for t in takes:
        by_shot.setdefault(t["shot_id"], []).append(t)
    fixes = {r["shot_id"]: r["n"] for r in open_fixes}
    ep["shots"] = [shot_out(s, ctx, by_shot.get(s["id"], [])) for s in shots]
    for s in ep["shots"]:
        s["open_fixes"] = fixes.get(s["id"], 0)
    ep["total_seconds"] = sum(s["duration"] for s in shots)
    ep["est_seconds"] = round(sum(s["est_seconds"] for s in shots), 1)
    ep["over_limit"] = sum(1 for s in ep["shots"] if s["over_limit"])
    ep["incomplete"] = sum(1 for s in ep["shots"] if s["missing"])
    ep["date"] = schedule.date_for(ep["number"], ctx.settings)
    with db.connect() as c:
        mates = [dict(r) for r in c.execute("SELECT id, number, arc_id FROM episodes WHERE arc_id IS ? AND deleted_at=''", (ep["arc_id"],))]
    ep.update(schedule.arc_numbers(mates, schedule.load_arcs()).get(eid, {"arc_id": None, "arc_title": "", "arc_number": None}))
    ep["status_name"] = db.STATUS_NAMES.get(ep["status"], ep["status"])
    ep["approved_by_name"] = user_names().get(ep["approved_by"], "") if ep.get("approved_by") else ""
    ep["cast"] = episode_cast(eid)
    ep["marked"] = breakdown.has_markers(ep["script"])
    return ep


class EpisodePatch(BaseModel):
    title: str | None = None
    script: str | None = None
    notes: str | None = None
    synopsis: str | None = None
    post_text: str | None = None
    post_same: bool | None = None
    post_text_tiktok: str | None = None
    post_text_reels: str | None = None
    post_text_shorts: str | None = None
    urgent: bool | None = None


POST_FIELDS = {"post_text", "post_same", "post_text_tiktok", "post_text_reels", "post_text_shorts"}
ANY_STAGE_FIELDS = POST_FIELDS | {"urgent"}  # описание поста и «срочно» сценарист меняет на любом этапе


@app.put("/api/episodes/{eid}", dependencies=[Depends(auth.need("episodes", "script"))])
def update_episode(eid: int, p: EpisodePatch, request: Request, reparse: bool = True):
    ep = load_episode_row(eid)
    changes = p.model_dump(exclude_unset=True)
    user = request.state.user
    if "script" in changes and not auth.has(user, "script"):
        raise HTTPException(403, "Нет доступа: сценарий. Доступ настраивает админ в «Команде»")
    if set(changes) - {"script"} and not auth.has(user, "episodes"):
        raise HTTPException(403, "Нет доступа: серии. Доступ настраивает админ в «Команде»")
    if set(changes) - ANY_STAGE_FIELDS:  # описание для поста и «срочно» сценарист правит на любом этапе
        writer_may_edit(request, ep["status"])
    with db.connect() as c:
        for k, v in changes.items():
            if v is not None:
                if k in ("post_same", "urgent"):
                    v = int(v)
                c.execute(f"UPDATE episodes SET {k}=?, updated_at=? WHERE id=?", (v, db.now(), eid))
    if p.urgent is not None and bool(p.urgent) != bool(ep.get("urgent")):
        db.log(eid, uid(request), "Отмечена как срочная" if p.urgent else "Снята отметка «срочно»")
    if p.script is not None and p.script != ep["script"]:
        db.log(eid, uid(request), "Сценарий изменён")
        if reparse:
            apply_breakdown(eid)
            promote_if_ready(eid, uid(request))
    return get_episode(eid)


class ResultIn(BaseModel):
    note: str = ""
    url: str = ""


@app.post("/api/episodes/{eid}/result", dependencies=[Depends(auth.need("episodes", "generation"))])
def set_result(eid: int, r: ResultIn, request: Request):
    """Итог серии: что получилось и где лежит готовый ролик (ссылка, например на Dropbox). Пишет любой из команды."""
    visible_or_404(request, eid)
    url = r.url.strip()
    if url and not re.match(r"^https?://", url, re.I):
        raise HTTPException(400, "Ссылка должна начинаться с http:// или https://")
    with db.connect() as c:
        c.execute("UPDATE episodes SET result_note=?, result_url=?, updated_at=? WHERE id=?", (r.note, url, db.now(), eid))
    db.log(eid, uid(request), "Итог серии обновлён")
    return {"ok": True}


class StatusIn(BaseModel):
    status: str
    force: bool = False  # producer only: approve even though some shots are incomplete


@app.post("/api/episodes/{eid}/status")
def set_status(eid: int, s: StatusIn, request: Request):
    if s.status not in db.STATUS_NAMES:
        raise HTTPException(400, "Неизвестный статус")
    role = request.state.user["role"]
    if not auth.has(request.state.user, "status"):
        raise HTTPException(403, "Нет доступа: менять статус серии. Доступ настраивает админ в «Команде»")
    ep = load_episode_row(eid)
    if s.status != ep["status"] and s.status not in auth.allowed_statuses(role, ep["status"]):
        raise HTTPException(403, f"Роль «{auth.ROLES[role]}» не может перевести серию из статуса "
                                 f"«{db.STATUS_NAMES[ep['status']]}» в «{db.STATUS_NAMES[s.status]}»")
    # Gate: a script is approved only when every shot is fully filled in
    crossing = db.STATUS_ORDER.index(s.status) >= db.STATUS_ORDER.index("approved") > db.STATUS_ORDER.index(ep["status"])
    if crossing:
        problems = incomplete_shots(eid)
        if problems and not (s.force and role == "admin"):
            raise HTTPException(409, "Сценарий нельзя согласовать, пока шоты заполнены не полностью:\n"
                                + "\n".join(problems[:12]) + ("\n…" if len(problems) > 12 else ""))
    with db.connect() as c:
        c.execute("UPDATE episodes SET status=?, updated_at=?, posted_at=CASE WHEN ?='posted' THEN ? ELSE posted_at END "
                  "WHERE id=?", (s.status, db.now(), s.status, date.today().isoformat(), eid))
    if ep["status"] != s.status:
        db.log(eid, uid(request), f"Статус: «{db.STATUS_NAMES[ep['status']]}» → «{db.STATUS_NAMES[s.status]}»")
    return {"ok": True}


class MoveIn(BaseModel):
    number: int | None = None
    date: str | None = None
    pin: bool = False  # поставить на этот день и закрепить (срочная серия): занятый день освобождается сдвигом


@app.post("/api/episodes/{eid}/move", dependencies=[Depends(auth.need("schedule"))])
def move_episode(eid: int, m: MoveIn, request: Request):
    ep = load_episode_row(eid)
    number = schedule.number_for(m.date) if m.date else m.number
    if number is not None and number < 1:
        raise HTTPException(400, "Эта дата раньше старта сериала. Поменяйте точку отсчёта в очереди")
    if m.pin and m.date:
        schedule.place(eid, m.date)
        db.log(eid, uid(request), f"Поставлена на {m.date} и закреплена, остальные сдвинуты")
        return {"ok": True}
    schedule.move(eid, number)
    if ep["number"] != number:
        db.log(eid, uid(request), f"Перенесена: {'в бэклог' if number is None else f'на {schedule.date_for(number)}'}")
    return {"ok": True}


class PinIn(BaseModel):
    pinned: bool


@app.post("/api/episodes/{eid}/pin", dependencies=[Depends(auth.need("schedule"))])
def pin_episode(eid: int, p: PinIn, request: Request):
    schedule.set_pinned(eid, p.pinned)
    db.log(eid, uid(request), "Дата закреплена" if p.pinned else "Дата откреплена")
    return {"ok": True}


# ---------- корзина серий ----------
# Удалённая серия 30 дней лежит в корзине (episodes.deleted_at): из очереди и со всех страниц она пропадает,
# но её можно вернуть — она вернётся в черновики своей арки. Через 30 дней серия удаляется совсем, вместе с файлами.
TRASH_DAYS = 30


class DeleteEpIn(BaseModel):
    shift: bool = False  # сдвинуть следующие серии на день назад, чтобы не было пустого дня


def _trash(c, eid: int, shift: bool) -> None:
    e = c.execute("SELECT number, pinned FROM episodes WHERE id=? AND deleted_at=''", (eid,)).fetchone()
    if not e:
        raise HTTPException(404, "Серия не найдена")
    c.execute("UPDATE episodes SET deleted_at=?, number=NULL, pinned=0, planned_date=NULL, urgent=0 WHERE id=?",
              (db.now(), eid))
    if shift and e["number"] is not None:
        schedule.close_gap(c, e["number"])


@app.delete("/api/episodes/{eid}", dependencies=[Depends(auth.need("episodes"))])
def delete_episode(eid: int, request: Request, p: DeleteEpIn | None = None):
    with db.connect() as c:
        _trash(c, eid, bool(p and p.shift))
    db.log(eid, uid(request), "Серия перенесена в корзину")
    return {"ok": True}


class BulkDeleteIn(BaseModel):
    ids: list[int]


@app.post("/api/episodes/bulk-delete", dependencies=[Depends(auth.need("episodes"))])
def bulk_delete_episodes(p: BulkDeleteIn, request: Request):
    """Несколько серий сразу в корзину (галочки у черновиков). Дни в очереди не сдвигаются."""
    with db.connect() as c:
        for eid in p.ids:
            _trash(c, eid, False)
    for eid in p.ids:
        db.log(eid, uid(request), "Серия перенесена в корзину")
    return {"ok": True, "count": len(p.ids)}


def _purge(c, eids: list[int]) -> None:
    """Удалить серии совсем: строки в базе (шоты, дубли, комментарии уходят каскадом) и их видео и картинки."""
    for eid in eids:
        files = [r[0] for r in c.execute("SELECT t.video_path FROM takes t JOIN shots s ON s.id=t.shot_id "
                                         "WHERE s.episode_id=? AND t.video_path<>''", (eid,))]
        files += [r[0] for r in c.execute("SELECT composition_image FROM shots WHERE episode_id=? AND composition_image<>''", (eid,))]
        c.execute("DELETE FROM episodes WHERE id=?", (eid,))
        root = db.MEDIA_DIR.resolve()
        for rel in files:
            f = (db.MEDIA_DIR / rel).resolve()
            if root in f.parents:
                f.unlink(missing_ok=True)


def purge_old_trash() -> None:
    cutoff = (datetime.now() - timedelta(days=TRASH_DAYS)).isoformat(timespec="seconds")
    with db.connect() as c:
        _purge(c, [r[0] for r in c.execute("SELECT id FROM episodes WHERE deleted_at<>'' AND deleted_at < ?", (cutoff,))])


@app.get("/api/trash", dependencies=[Depends(auth.need("episodes"))])
def list_trash():
    purge_old_trash()
    titles = {a["id"]: a["title"] for a in schedule.load_arcs()}
    with db.connect() as c:
        eps = db.rows(c.execute("SELECT e.id, e.title, e.synopsis, e.status, e.arc_id, e.deleted_at, "
                                "(SELECT COUNT(*) FROM shots s WHERE s.episode_id=e.id) AS shots "
                                "FROM episodes e WHERE e.deleted_at<>'' ORDER BY e.deleted_at DESC"))
    for e in eps:
        e["arc_title"] = titles.get(e["arc_id"], "")
        e["status_name"] = db.STATUS_NAMES.get(e["status"], e["status"])
        e["purge_at"] = (datetime.fromisoformat(e["deleted_at"]) + timedelta(days=TRASH_DAYS)).date().isoformat()
    return {"episodes": eps, "days": TRASH_DAYS}


@app.post("/api/trash/{eid}/restore", dependencies=[Depends(auth.need("episodes"))])
def restore_episode(eid: int, request: Request):
    """Серия возвращается в черновики своей арки (её прежний день мог уже занять кто-то другой)."""
    with db.connect() as c:
        if not c.execute("UPDATE episodes SET deleted_at='' WHERE id=? AND deleted_at<>''", (eid,)).rowcount:
            raise HTTPException(404, "В корзине такой серии нет")
        if c.execute("SELECT arc_id FROM episodes WHERE id=?", (eid,)).fetchone()[0] is not None and \
                not c.execute("SELECT 1 FROM arcs WHERE id=(SELECT arc_id FROM episodes WHERE id=?)", (eid,)).fetchone():
            c.execute("UPDATE episodes SET arc_id=NULL WHERE id=?", (eid,))  # арку за это время удалили
    db.log(eid, uid(request), "Серия возвращена из корзины в черновики")
    return {"ok": True}


@app.delete("/api/trash/{eid}", dependencies=[Depends(auth.need("episodes"))])
def purge_episode(eid: int):
    with db.connect() as c:
        if not c.execute("SELECT 1 FROM episodes WHERE id=? AND deleted_at<>''", (eid,)).fetchone():
            raise HTTPException(404, "В корзине такой серии нет")
        _purge(c, [eid])
    return {"ok": True}


@app.get("/api/episodes/{eid}/events")
def episode_events(eid: int, request: Request):
    visible_or_404(request, eid)
    names = user_names()
    with db.connect() as c:
        evs = db.rows(c.execute("SELECT * FROM events WHERE episode_id=? ORDER BY id DESC LIMIT 200", (eid,)))
    for e in evs:
        e["user_name"] = names.get(e["user_id"], "система")
    return evs


class AnalyzeIn(BaseModel):
    script: str
    episode_id: int | None = None


@app.post("/api/analyze", dependencies=[Depends(auth.need("script"))])
def analyze(a: AnalyzeIn):
    """Live analysis while typing: nothing is saved."""
    ctx = Ctx()
    cast = episode_cast(a.episode_id) if a.episode_id else {}
    shots, notes = breakdown.parse_script(a.script, ctx.assets, ctx.settings, cast)
    for s in shots:
        s["composition_image"] = ""
        refresh_shot(s, ctx)
    used = {}
    for s in shots:
        for ch in s["characters"]:
            used[ch["asset_id"]] = ch["version_id"]
        v = ctx.v_map.get(s["location_version_id"])
        if v:
            used[v["asset_id"]] = v["id"]
    return {
        "marked": breakdown.has_markers(a.script),
        "notes": notes,
        "total_seconds": sum(s["duration"] for s in shots),
        "est_seconds": round(sum(s["est_seconds"] for s in shots), 1),
        "shots": [{k: s[k] for k in ("label", "duration", "est_seconds", "speech_seconds", "words", "scene")}
                  | {"over": s["est_seconds"] > ctx.max_s, "lines": len(s["dialogue"]),
                     "warnings": breakdown.shot_warnings(s, ctx.max_s)} for s in shots],
        "used": [{"asset_id": k, "version_id": v} for k, v in used.items()],
    }


def apply_breakdown(eid: int, only_new: bool = False):
    """Re-parse the script and merge into existing shots by shot label, keeping composition refs,
    takes, comments and manually locked prompts. only_new: уже существующие шоты не трогаем (только их порядок),
    создаём лишь те, которых ещё нет: так добавление шота не сбрасывает персонажей и локации, выбранные в карточках."""
    ep = load_episode_row(eid)
    ctx = Ctx()
    new, notes = breakdown.parse_script(ep["script"], ctx.assets, ctx.settings, episode_cast(eid))
    with db.connect() as c:
        old = db.rows(c.execute("SELECT * FROM shots WHERE episode_id=? ORDER BY idx", (eid,)))
        pool = {}
        for o in old:
            pool.setdefault(o["label"] or str(o["idx"]), []).append(o)
        if only_new:
            left = {}
            for n in new:
                left[n["label"]] = left.get(n["label"], 0) + 1
            for o in old:
                key = o["label"] or str(o["idx"])
                if not left.get(key):
                    raise ValueError(f"шот {key} есть в базе, но пропал из разбора сценария")
                left[key] -= 1
        keep_ids = set()
        for n in new:
            match = (pool.get(n["label"]) or [None]).pop(0) if pool.get(n["label"]) else None
            if match and only_new:
                c.execute("UPDATE shots SET idx=? WHERE id=?", (n["idx"], match["id"]))
                keep_ids.add(match["id"])
                continue
            s = dict(match) if match else {"episode_id": eid, "composition_image": "", "composition_mode": "reference",
                                           "composition_note": "", "prompt": "", "prompt_locked": 0,
                                           "negative_prompt": ctx.settings.get("negative_prompt", ""),
                                           "selected_take_id": None, "needs_redo": 0, "engine": "veo",
                                           "sound_off": 0, "sound_mode": "full", "no_characters": 0, "voiceover": 0,
                                           "location_mode": "library", "location_text": ""}
            s.update({k: n[k] for k in ("idx", "label", "scene", "duration", "camera", "action", "dialogue",
                                        "characters", "location_version_id")})
            refresh_shot(s, ctx)
            save_shot(c, s)
            keep_ids.add(s["id"])
        for o in old:
            if o["id"] not in keep_ids:
                c.execute("DELETE FROM shots WHERE id=?", (o["id"],))
        c.execute("UPDATE episodes SET parse_notes=?, updated_at=? WHERE id=?", (db.dumps(notes), db.now(), eid))


@app.post("/api/episodes/{eid}/breakdown", dependencies=[Depends(auth.need("script"))])
def breakdown_episode(eid: int, request: Request):
    writer_may_edit(request, load_episode_row(eid)["status"])
    apply_breakdown(eid)
    promote_if_ready(eid, uid(request))
    return get_episode(eid)


class ShotInsertIn(BaseModel):
    after: int  # id шота, после которого появится новый
    text: str   # что в кадре, в формате сценария (реплики «МАША: Привет!», камера «КАМЕРА: …»)


@app.post("/api/episodes/{eid}/shots", dependencies=[Depends(auth.need("script", "generation"))])
def insert_shot(eid: int, p: ShotInsertIn, request: Request):
    """Промежуточный шот в готовом сценарии: текст вставляется в сценарий блоком «Шот 2а» после выбранного шота.
    Номера остальных шотов не меняются, поэтому дубли и комментарии остаются у своих шотов, а карточки других шотов
    не перечитываются из сценария. Добавлять может любой, кто пишет сценарий или генерирует (в том числе после согласования
    ТЗ): существующий текст не меняется, шот только добавляется."""
    visible_or_404(request, eid)
    ep = load_episode_row(eid)
    body = p.text.strip()
    if not body:
        raise HTTPException(400, "Опишите, что происходит в новом шоте")
    if not breakdown.has_markers(ep["script"]):
        raise HTTPException(400, "В сценарии нет пометок «Шот 1», «Шот 2»… — номера шотов выдаются по порядку, и вставка сдвинула бы "
                                 "дубли на чужие шоты. Расставьте пометки на вкладке «Сценарий» или допишите шот в сценарий")
    after = load_shot(p.after)
    if after["episode_id"] != eid:
        raise HTTPException(404, "Шот не найден")
    with db.connect() as c:
        same = [r["id"] for r in c.execute("SELECT id FROM shots WHERE episode_id=? AND label=? ORDER BY idx", (eid, after["label"]))]
    try:
        script, label = breakdown.insert_shot_block(ep["script"], after["label"], same.index(after["id"]), body)
    except ValueError as e:
        raise HTTPException(400, str(e))
    with db.connect() as c:
        c.execute("UPDATE episodes SET script=?, updated_at=? WHERE id=?", (script, db.now(), eid))
    try:
        apply_breakdown(eid, only_new=True)
    except ValueError as e:  # шоты разошлись со сценарием: возвращаем прежний текст, ничего не меняя
        with db.connect() as c:
            c.execute("UPDATE episodes SET script=? WHERE id=?", (ep["script"], eid))
        raise HTTPException(409, f"Не получилось добавить шот: {e}. Нажмите «Сохранить и разбить на шоты» на вкладке «Сценарий» и повторите")
    with db.connect() as c:
        new_id = next((r["id"] for r in c.execute("SELECT id FROM shots WHERE episode_id=? AND label=?", (eid, label))), None)
    if new_id and not breakdown.has_scene_heading(body):  # промежуточный шот снимают там же, где предыдущий: локация с его карточки
        s = load_shot(new_id)
        s.update({k: after[k] for k in ("location_version_id", "location_mode", "location_text")})
        refresh_shot(s, Ctx())
        with db.connect() as c:
            save_shot(c, s)
    db.log(eid, uid(request), f"Добавлен шот {label} после шота {after['label']}")
    promote_if_ready(eid, uid(request))
    out = get_episode(eid)
    out["inserted_shot_id"] = new_id
    return out


class CastIn(BaseModel):
    cast: list[dict]  # [{asset_id, version_id|null}]


@app.put("/api/episodes/{eid}/cast", dependencies=[Depends(auth.need("script"))])
def set_cast(eid: int, body: CastIn, request: Request):
    writer_may_edit(request, load_episode_row(eid)["status"])
    with db.connect() as c:
        for item in body.cast:
            if item.get("version_id"):
                c.execute("INSERT OR REPLACE INTO episode_cast(episode_id, asset_id, version_id) VALUES (?,?,?)",
                          (eid, item["asset_id"], item["version_id"]))
            else:
                c.execute("DELETE FROM episode_cast WHERE episode_id=? AND asset_id=?", (eid, item["asset_id"]))
    db.log(eid, uid(request), "Изменены версии персонажей/локаций для серии")
    apply_breakdown(eid)
    promote_if_ready(eid, uid(request))
    return get_episode(eid)


class GenerateIn(BaseModel):
    mode: str = "missing"  # missing | redo | all
    force: bool = False    # только продюсер: отправить и незаполненные шоты «под свою ответственность»


@app.post("/api/episodes/{eid}/generate", dependencies=[Depends(auth.need("generation"))])
def generate_episode(eid: int, g: GenerateIn, request: Request):
    ep = get_episode(eid)
    if not ep["shots"]:
        raise HTTPException(400, "В серии нет шотов")
    settings = db.get_settings()
    force = g.force and request.state.user["role"] == "admin"
    started = skipped = uploaded = 0
    unready = []
    farm_items = []  # шоты рендерфермы уходят одним запросом
    ctx = Ctx() if any(models.route(s["engine"], settings) == "renderfarm" for s in ep["shots"]) else None
    for s in ep["shots"]:
        busy = s["gen_status"] in ("queued", "running")
        if busy:
            continue
        if (s.get("selected_take") or {}).get("source") == "upload":  # готовое видео загружено вручную: на генерацию не отправляем
            uploaded += 1
            continue
        if not models.has_api(s["engine"]):  # этот шот монтажёр делает вручную в другой нейросети и пришлёт файлом
            if not s["selected_take_id"] or s["needs_redo"]:
                skipped += 1
            continue
        if g.mode == "missing" and s["selected_take_id"] and not s["needs_redo"]:
            continue
        if g.mode == "redo" and not (s["needs_redo"] or s["open_fixes"]):
            continue
        if not s["ready"] and not force:  # защита от пустых шотов: незаполненное в нейросеть не уходит
            unready.append({"id": s["id"], "label": s["label"], "items": unready_items(s)})
            continue
        why = "отправлено продюсером с незаполненными пунктами" if not s["ready"] else ""
        if models.route(s["engine"], settings) == "renderfarm":  # в режиме «Заглушка» ферме ничего не уходит
            farm_items.append((s, ctx.v_map, ctx.a_map))
            continue
        veo.start(s, s["references"], settings, uid(request), why)
        started += 1
    started += len(farm_items)
    if started:
        with db.connect() as c:
            c.execute("UPDATE episodes SET status='generating', updated_at=? WHERE id=?", (db.now(), eid))
        db.log(eid, uid(request), f"Отправлено в генерацию шотов: {started}")
    if farm_items:  # статус серии уже «Генерация»: если шот не удастся подготовить, серия сама перейдёт дальше
        farm.start_many(farm_items, settings, uid(request), "отправлено продюсером с незаполненными пунктами" if any(not x[0]["ready"] for x in farm_items) else "")
    return {"started": started, "skipped": skipped, "uploaded": uploaded, "unready": unready}


@app.get("/api/episodes/{eid}/export")
def export_episode(eid: int, request: Request):
    visible_or_404(request, eid)
    ep = get_episode(eid)
    name = re.sub(r"[^\w\-]+", "_", f"episode_{ep['number'] or 'backlog'}_{ep['title']}")[:60]
    return JSONResponse(ep, headers={"Content-Disposition": f'attachment; filename="{name}.json"'})


# ---------- shots ----------

class ShotPatch(BaseModel):
    scene: str | None = None
    duration: int | None = None
    camera: str | None = None
    action: str | None = None
    dialogue: list[dict] | None = None
    characters: list[dict] | None = None
    location_version_id: int | None = None
    clear_location: bool = False
    location_mode: str | None = None
    location_text: str | None = None
    composition_mode: str | None = None
    composition_note: str | None = None
    prompt: str | None = None
    negative_prompt: str | None = None
    prompt_locked: bool | None = None
    needs_redo: bool | None = None
    engine: str | None = None
    sound_off: bool | None = None
    sound_mode: str | None = None
    no_characters: bool | None = None
    voiceover: bool | None = None
    farm_action: str | None = None     # «Ферма»: своё описание действия по-английски (не обязательно; пусто: уходит «Действие» как есть, русский переведёт ферма)
    farm_voice: str | None = None      # голос по-английски: normal calm voice, whisper…
    farm_camera: str | None = None     # камера по-английски: slow push-in…
    farm_shot_size: str | None = None  # medium shot | close-up | wide shot
    farm_quality: str | None = None    # final | draft
    farm_len: str | None = None        # "" — длину считает ферма по реплике, "shot" — берём «Длину клипа» шота


def load_shot(sid: int) -> dict:
    with db.connect() as c:
        s = db.row(c.execute("SELECT * FROM shots WHERE id=?", (sid,)).fetchone())
    if not s:
        raise HTTPException(404, "Шот не найден")
    return s


def shot_response(sid: int) -> dict:
    s = load_shot(sid)
    with db.connect() as c:
        takes = db.rows(c.execute("SELECT * FROM takes WHERE shot_id=? ORDER BY take_no", (sid,)))
        s["open_fixes"] = c.execute("SELECT COUNT(*) FROM comments WHERE shot_id=? AND is_fix=1 AND resolved=0",
                                    (sid,)).fetchone()[0]
    return shot_out(s, Ctx(), takes)


# Content belongs to the writer, generation settings to the editor (the producer may touch both).
WRITER_SHOT_FIELDS = {"scene", "camera", "action", "dialogue", "characters", "location_version_id", "clear_location",
                       "location_mode", "location_text", "sound_off", "sound_mode", "no_characters", "voiceover"}
EDITOR_SHOT_FIELDS = {"sound_off", "sound_mode", "voiceover", "duration", "composition_mode", "composition_note", "prompt", "negative_prompt",
                      "prompt_locked", "needs_redo", "engine", "farm_action", "farm_voice", "farm_camera", "farm_shot_size", "farm_quality", "farm_len"}


@app.put("/api/shots/{sid}")
def update_shot(sid: int, p: ShotPatch, request: Request):
    s = load_shot(sid)
    before = dict(s)
    data = p.model_dump(exclude_unset=True)
    user = request.state.user
    if user["role"] != "admin":
        own = (WRITER_SHOT_FIELDS if auth.has(user, "script") else set()) | (EDITOR_SHOT_FIELDS if auth.has(user, "generation") else set())
        foreign = sorted(set(data) - own)
        if foreign:
            raise HTTPException(403, f"Нет доступа к полям шота: {', '.join(foreign)}. Доступ настраивает админ в «Команде»")
        if set(data) & (WRITER_SHOT_FIELDS - EDITOR_SHOT_FIELDS):
            writer_may_edit(request, load_episode_row(s["episode_id"])["status"])
    if data.pop("clear_location", False):
        s["location_version_id"] = None
    if data.get("engine") not in (None, *ENGINE_NAMES):
        raise HTTPException(400, "Неизвестная нейросеть")
    engine = data.get("engine") or s.get("engine")
    if "duration" in data and data["duration"] not in breakdown.engine_durations(engine):
        raise HTTPException(400, f"«{ENGINE_NAMES.get(engine, engine)}» поддерживает длительность: "
                                 + ", ".join(str(d) for d in breakdown.engine_durations(engine)) + " с")
    if data.get("sound_mode") not in (None, *SOUND_MODES):
        raise HTTPException(400, "Неизвестный режим звука")
    if "sound_mode" in data:
        data["sound_off"] = data["sound_mode"] == "off"
    elif data.get("sound_off") is not None:  # старая галочка «звук не нужен»
        data["sound_mode"] = "off" if data["sound_off"] else "full"
    if data.get("characters"):
        data["no_characters"] = False  # выбрали персонажа — «без персонажей» снимается само
    if data.get("location_mode") not in (None, "library", "text"):
        raise HTTPException(400, "Неизвестный способ задать локацию")
    if data.get("composition_mode") not in (None, "reference", "first_frame"):
        raise HTTPException(400, "Неизвестный режим композиции")
    if data.get("farm_shot_size") not in (None, "", *farm.SHOT_SIZES) or data.get("farm_quality") not in (None, "", "draft", "final") \
            or data.get("farm_len") not in (None, "", "shot"):
        raise HTTPException(400, "Неизвестное значение настройки фермы")
    for k in ("farm_action", "farm_voice", "farm_camera"):
        if data.get(k) and farm.CYR.search(data[k]):
            raise HTTPException(400, "Это поле для своего английского текста. Русский пишите в обычных полях шота («Действие», «Камера»): "
                                     "ферма переведёт его сама. Если поле не нужно, оставьте его пустым")
    for k, v in data.items():
        if v is not None or k == "location_version_id":
            s[k] = v
    if "prompt" in data:
        s["prompt_locked"] = True if p.prompt_locked is None else p.prompt_locked
    ctx = Ctx()
    if {"action", "scene", "camera", "dialogue"} & set(data):
        add_named_characters(s, before, ctx)
    refresh_shot(s, ctx)
    with db.connect() as c:
        save_shot(c, s)
    promote_if_ready(s["episode_id"], uid(request))
    return {**shot_response(sid), "episode_status": load_episode_row(s["episode_id"])["status"]}


def shot_texts(s: dict) -> str:
    return " \n".join([s.get("scene") or "", s.get("action") or "", s.get("camera") or "",
                      *[d.get("text") or "" for d in s.get("dialogue") or []]])


def add_named_characters(s: dict, before: dict, ctx: Ctx):
    """Герой, которого автор только что вписал в текст шота (по имени или через @), попадает в «Персонажи в кадре».
    Убранных вручную не возвращаем: добавляем только тех, кого в прошлом тексте не было."""
    idx = breakdown.AssetIndex(ctx.assets, episode_cast(s["episode_id"]))
    was = {a["id"] for a in idx.find_all(shot_texts(before), "character")}
    have = {c["asset_id"] for c in s["characters"]}
    for a in idx.find_all(shot_texts(s), "character"):
        if a["id"] not in was and a["id"] not in have:
            s["characters"] = [*s["characters"], {"asset_id": a["id"], "name": a["name"],
                                                  "version_id": idx.version(a, None)["id"] if a["versions"] else None}]
    have = {c["asset_id"] for c in s["characters"]}
    was_speakers = {d.get("asset_id") for d in before.get("dialogue") or []}
    for d in s["dialogue"]:  # выбранный говорящий тоже появляется в кадре
        aid = d.get("asset_id")
        if aid and aid not in have and aid not in was_speakers and aid in ctx.a_map:
            a = ctx.a_map[aid]
            s["characters"] = [*s["characters"], {"asset_id": aid, "name": a["name"],
                                                  "version_id": idx.version(a, None)["id"] if a["versions"] else None}]
            have.add(aid)


@app.post("/api/shots/{sid}/composition", dependencies=[Depends(auth.need("generation"))])
def upload_composition(sid: int, image: UploadFile = File(...)):
    s = load_shot(sid)
    s["composition_image"] = save_upload(image, f"compositions/{s['episode_id']}")
    refresh_shot(s, Ctx())
    with db.connect() as c:
        save_shot(c, s)
    return shot_response(sid)


@app.delete("/api/shots/{sid}/composition", dependencies=[Depends(auth.need("generation"))])
def delete_composition(sid: int):
    s = load_shot(sid)
    s["composition_image"] = ""
    refresh_shot(s, Ctx())
    with db.connect() as c:
        save_shot(c, s)
    return shot_response(sid)


@app.post("/api/shots/{sid}/rebuild-prompt", dependencies=[Depends(auth.need("generation"))])
def rebuild_prompt(sid: int):
    s = load_shot(sid)
    s["prompt_locked"] = 0
    refresh_shot(s, Ctx())
    with db.connect() as c:
        save_shot(c, s)
    return shot_response(sid)


@app.get("/api/shots/{sid}/request")
def shot_request(sid: int):
    s = shot_response(sid)
    if s["engine"] == "renderfarm":
        ctx = Ctx()
        return farm.preview(s, ctx.v_map, ctx.a_map, ctx.settings)
    return veo.preview(s, s["references"], db.get_settings())


class RegenerateIn(BaseModel):
    prompt: str | None = None   # improved prompt for this attempt; saved to the shot
    reason: str = ""            # what was wrong with the previous take
    duration: int | None = None  # длина клипа для нового дубля (вводится вручную, сохраняется в шот)
    force: bool = False          # только продюсер: отправить незаполненный шот


@app.post("/api/shots/{sid}/generate", dependencies=[Depends(auth.need("generation"))])
def generate_shot(sid: int, request: Request, g: RegenerateIn | None = None):
    s = shot_response(sid)
    if s["gen_status"] in ("queued", "running"):
        raise HTTPException(400, "Этот шот уже генерируется")
    if not models.has_api(s["engine"]):
        raise HTTPException(400, f"Этот шот делается в «{ENGINE_NAMES.get(s['engine'], s['engine'])}»: через API он не уходит. "
                                 "Сделайте видео там и загрузите готовый файл.")
    g = g or RegenerateIn()
    if g.duration is not None and g.duration != s["duration"]:
        allowed = breakdown.engine_durations(s["engine"])
        if g.duration not in allowed:
            raise HTTPException(400, f"«{ENGINE_NAMES.get(s['engine'], s['engine'])}» делает клипы длиной "
                                     + _durations_text(allowed) + ", а введено " + str(g.duration) + " с")
        if s["forced_8"] and g.duration != 8:
            raise HTTPException(400, "С референсами Veo 3.1 делает только 8 с")
        raw = load_shot(sid)
        raw["duration"] = g.duration
        refresh_shot(raw, Ctx())
        with db.connect() as c:
            save_shot(c, raw)
        s = shot_response(sid)
    if g.prompt is not None and g.prompt.strip() and g.prompt.strip() != s["prompt"].strip():
        raw = load_shot(sid)
        raw["prompt"], raw["prompt_locked"] = g.prompt.strip(), 1
        with db.connect() as c:
            save_shot(c, raw)
        s = shot_response(sid)
    if not s["ready"] and not (g.force and request.state.user["role"] == "admin"):
        raise HTTPException(400, f"Шот {s['label']} не заполнен: " + "; ".join(unready_items(s))
                                 + ". Если пункт не нужен, закройте его явно («-», «без персонажей»).")
    with db.connect() as c:
        c.execute("UPDATE episodes SET status='generating', updated_at=? WHERE id=? AND status NOT IN ('posted')",
                  (db.now(), s["episode_id"]))
    if models.route(s["engine"], db.get_settings()) == "renderfarm":
        ctx = Ctx()
        farm.start_many([(s, ctx.v_map, ctx.a_map)], ctx.settings, uid(request), g.reason)
    else:
        veo.start(s, s["references"], db.get_settings(), uid(request), g.reason)
    note = f" — причина: {g.reason.strip()}" if g.reason.strip() else ""
    db.log(s["episode_id"], uid(request), f"Шот {s['label']}: новый дубль (№{len(s['takes']) + 1}) отправлен в {ENGINE_NAMES.get(s['engine'], s['engine'])}{note}")
    return shot_response(sid)


def _durations_text(ds) -> str:
    ds = sorted(ds)
    if len(ds) > 3 and ds == list(range(ds[0], ds[-1] + 1)):
        return f"от {ds[0]} до {ds[-1]} с"
    return ", ".join(str(d) for d in ds) + " с"


@app.post("/api/takes/{tid}/cancel", dependencies=[Depends(auth.need("generation"))])
def cancel_take(tid: int, request: Request):
    with db.connect() as c:
        t = c.execute("SELECT t.shot_id, t.take_no, s.episode_id, s.label FROM takes t JOIN shots s ON s.id=t.shot_id "
                      "WHERE t.id=?", (tid,)).fetchone()
    if not t:
        raise HTTPException(404, "Дубль не найден")
    if not veo.cancel(tid):
        raise HTTPException(400, "Этот дубль уже не генерируется")
    db.log(t["episode_id"], uid(request), f"Шот {t['label']}: генерация дубля {t['take_no']} отменена")
    return shot_response(t["shot_id"])


class SelectTakeIn(BaseModel):
    take_id: int


@app.post("/api/shots/{sid}/select-take", dependencies=[Depends(auth.need("generation"))])
def select_take(sid: int, t: SelectTakeIn, request: Request):
    with db.connect() as c:
        take = c.execute("SELECT * FROM takes WHERE id=? AND shot_id=?", (t.take_id, sid)).fetchone()
        if not take:
            raise HTTPException(404, "Дубль не найден")
        c.execute("UPDATE shots SET selected_take_id=? WHERE id=?", (t.take_id, sid))
        ep_id = c.execute("SELECT episode_id FROM shots WHERE id=?", (sid,)).fetchone()[0]
    db.log(ep_id, uid(request), f"Выбран дубль {take['take_no']} для шота")
    return shot_response(sid)


@app.post("/api/shots/{sid}/upload-take", dependencies=[Depends(auth.need("generation"))])
def upload_take(sid: int, request: Request, video: UploadFile = File(...)):
    s = load_shot(sid)
    rel = save_upload(video, f"renders/manual/{s['episode_id']}", VIDEO_EXT)
    with db.connect() as c:
        n = c.execute("SELECT COALESCE(MAX(take_no), 0) + 1 FROM takes WHERE shot_id=?", (sid,)).fetchone()[0]
        tid = c.execute("INSERT INTO takes(shot_id, take_no, status, video_path, prompt, duration, source, created_by,"
                        " created_at, finished_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (sid, n, "done", rel, s["prompt"], s["duration"], "upload", uid(request), db.now(), db.now())).lastrowid
        c.execute("UPDATE shots SET selected_take_id=?, needs_redo=0 WHERE id=?", (tid, sid))
    db.log(s["episode_id"], uid(request), f"Шот {s['label']}: загружено видео вручную (дубль {n})")
    return shot_response(sid)


# ---------- download approved episode ----------

_TR = dict(zip("абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
               ["a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "y", "k", "l", "m", "n", "o", "p", "r", "s", "t", "u",
                "f", "kh", "ts", "ch", "sh", "shch", "", "y", "", "e", "yu", "ya"]))


def latin_slug(title: str, fallback: str = "episode") -> str:
    s = "".join(_TR.get(ch, ch) for ch in (title or "").lower())
    s = re.sub(r"[^a-z0-9]+", "_", s).strip("_")
    return s or fallback


def version_picks(eid: int, version: int) -> list[dict]:
    """Для версии серии vN: у каждого шота берётся последний дубль с видео, номер которого не больше N
    (если такого нет, самый ранний). Так же версию показывает вкладка «Просмотр и правки»."""
    with db.connect() as c:
        shots = db.rows(c.execute("SELECT id, idx, label FROM shots WHERE episode_id=? ORDER BY idx", (eid,)))
        takes = db.rows(c.execute(
            "SELECT t.id, t.shot_id, t.take_no, t.video_path FROM takes t JOIN shots s ON s.id=t.shot_id "
            "WHERE s.episode_id=? AND t.video_path<>'' ORDER BY t.take_no", (eid,)))
    out = []
    for s in shots:
        mine = [t for t in takes if t["shot_id"] == s["id"]]
        upto = [t for t in mine if t["take_no"] <= version]
        pick = upto[-1] if upto else (mine[0] if mine else None)
        out.append({"shot_id": s["id"], "idx": s["idx"], "label": s["label"], "take_id": pick["id"] if pick else None,
                    "video_path": pick["video_path"] if pick else ""})
    return out


class ApproveVersionIn(BaseModel):
    version: int


@app.post("/api/episodes/{eid}/approve-version", dependencies=[Depends(auth.need("generation"))])
def approve_version(eid: int, a: ApproveVersionIn, request: Request):
    ep = load_episode_row(eid)
    if ep["export_status"] == "building":
        raise HTTPException(409, "Архив предыдущей версии ещё собирается, подождите минуту")
    picks = version_picks(eid, a.version)
    with db.connect() as c:
        top = c.execute("SELECT COALESCE(MAX(t.take_no), 0) FROM takes t JOIN shots s ON s.id=t.shot_id "
                        "WHERE s.episode_id=? AND t.video_path<>''", (eid,)).fetchone()[0]
    if a.version < 1 or a.version > top or not any(p["video_path"] for p in picks):
        raise HTTPException(400, f"В версии v{a.version} нет видео")
    empty = [p["label"] or str(p["idx"]) for p in picks if not p["video_path"]]
    if empty:
        raise HTTPException(409, f"Версию v{a.version} нельзя согласовать: нет видео у шотов {', '.join(empty)}")
    slug = latin_slug(ep["title"], f"episode_{ep['number'] or eid}")
    folder = f"{ep['number']:02d}" if ep["number"] is not None else f"id{eid}"
    with db.connect() as c:
        c.execute("UPDATE episodes SET approved_version=?, approved_takes=?, approved_by=?, approved_at=?, "
                  "export_status='building', export_error='', updated_at=?, "
                  "status=CASE WHEN status IN ('generating','fixes','client_review') THEN 'ready' ELSE status END WHERE id=?",
                  (a.version, json.dumps({str(p["shot_id"]): p["take_id"] for p in picks}), uid(request), db.now(),
                   db.now(), eid))
    db.log(eid, uid(request), f"Согласована версия v{a.version}")
    exporter.start(eid, a.version, folder, slug, [(p["idx"], p["video_path"]) for p in picks])
    return get_episode(eid)


@app.get("/api/episodes/{eid}/download")
def download_episode(eid: int):
    ep = load_episode_row(eid)
    v = ep["approved_version"]
    if not v:
        raise HTTPException(400, "Сначала согласуйте версию на вкладке «Просмотр и правки»")
    if ep["export_status"] == "building":
        raise HTTPException(409, "Архив ещё собирается, повторите через минуту")
    f = exporter.export_path(eid, v)
    if ep["export_status"] == "error" or not f.is_file():
        raise HTTPException(500, ep["export_error"] or "Архив не собран")
    slug = latin_slug(ep["title"], f"episode_{ep['number'] or eid}")
    return FileResponse(f, media_type="application/zip", filename=f"{slug}_v{v}.zip")


# ---------- comments / fixes ----------

class CommentIn(BaseModel):
    text: str
    shot_id: int | None = None
    take_id: int | None = None
    timecode: float | None = None
    is_fix: bool = False


def comments_for(eid: int) -> list[dict]:
    names = user_names()
    with db.connect() as c:
        cs = db.rows(c.execute(
            "SELECT cm.*, s.label AS shot_label, t.take_no FROM comments cm LEFT JOIN shots s ON s.id=cm.shot_id "
            "LEFT JOIN takes t ON t.id=cm.take_id WHERE cm.episode_id=? ORDER BY cm.id", (eid,)))
    for cm in cs:
        cm["user_name"] = names.get(cm["user_id"], "—")
        cm["resolved_by_name"] = names.get(cm["resolved_by"], "") if cm["resolved_by"] else ""
    return cs


@app.get("/api/episodes/{eid}/comments")
def list_comments(eid: int, request: Request):
    visible_or_404(request, eid)
    return comments_for(eid)


@app.post("/api/episodes/{eid}/comments")
def add_comment(eid: int, cm: CommentIn, request: Request):
    visible_or_404(request, eid)
    if not cm.text.strip():
        raise HTTPException(400, "Пустой комментарий")
    # Anyone can comment, but only the editor (or producer) decides what goes to re-generation
    cm.is_fix = cm.is_fix and auth.has(request.state.user, "generation")
    with db.connect() as c:
        c.execute("INSERT INTO comments(episode_id, shot_id, take_id, user_id, text, timecode, is_fix, created_at)"
                  " VALUES (?,?,?,?,?,?,?,?)",
                  (eid, cm.shot_id, cm.take_id, uid(request), cm.text.strip(), cm.timecode, int(cm.is_fix), db.now()))
        if cm.is_fix and cm.shot_id:
            c.execute("UPDATE shots SET needs_redo=1 WHERE id=?", (cm.shot_id,))
        if cm.is_fix:
            c.execute("UPDATE episodes SET status='fixes', updated_at=? WHERE id=? AND status IN ('ready')",
                      (db.now(), eid))
    return comments_for(eid)


class ResolveIn(BaseModel):
    resolved: bool = True


@app.post("/api/comments/{cid}/resolve", dependencies=[Depends(auth.need("generation"))])
def resolve_comment(cid: int, r: ResolveIn, request: Request):
    with db.connect() as c:
        cm = c.execute("SELECT * FROM comments WHERE id=?", (cid,)).fetchone()
        if not cm:
            raise HTTPException(404, "Комментарий не найден")
        c.execute("UPDATE comments SET resolved=?, resolved_by=? WHERE id=?",
                  (int(r.resolved), uid(request) if r.resolved else None, cid))
    return comments_for(cm["episode_id"])


@app.delete("/api/comments/{cid}")
def delete_comment(cid: int, request: Request):
    me = request.state.user
    with db.connect() as c:
        cm = c.execute("SELECT * FROM comments WHERE id=?", (cid,)).fetchone()
        if not cm:
            raise HTTPException(404, "Комментарий не найден")
        if cm["user_id"] != me["id"] and me["role"] != "admin":
            raise HTTPException(403, "Удалить можно только свой комментарий")
        c.execute("DELETE FROM comments WHERE id=?", (cid,))
    return comments_for(cm["episode_id"])


# ---------- «Правка»: notes about the studio itself, stored as GitHub issues ----------

def _gh(fn, *args):
    try:
        return fn(*args)
    except GitHubError as e:
        raise HTTPException(502 if e.code >= 500 else 400, str(e))


# ---------- «Сообщить о проблеме»: заявки команды, видит их админ ----------

REPORT_KINDS = {"bug": "Что-то сломалось", "ux": "Неудобно или криво", "idea": "Есть идея"}
REPORT_STATUSES = {"new": "Новая", "work": "В работе", "done": "Сделано", "rejected": "Отклонена"}


def _report_out(r: dict, names: dict) -> dict:
    return {**r, "author": names.get(r["created_by"], "—"), "kind_name": REPORT_KINDS.get(r["kind"], r["kind"]),
            "has_shot": bool(r["shot_path"]), "shot_path": None}


@app.post("/api/reports")
async def report_create(request: Request, kind: str = Form("bug"), text: str = Form(""), page: str = Form(""),
                        viewport: str = Form(""), image: UploadFile | None = File(default=None)):
    if kind not in REPORT_KINDS:
        raise HTTPException(400, "Неизвестный тип заявки")
    if not text.strip():
        raise HTTPException(400, "Напишите, что случилось")
    data = await image.read() if image else b""
    ext = ""
    if data:
        ext = Path(image.filename or "").suffix.lower()
        if ext not in IMAGE_EXT:
            ext = "." + (image.content_type or "image/png").split("/")[-1].replace("jpeg", "jpg")
        if ext not in IMAGE_EXT:
            raise HTTPException(400, "Снимок должен быть картинкой PNG, JPG или WEBP")
        if len(data) > 15 * 1024 * 1024:
            raise HTTPException(400, "Снимок больше 15 МБ")
    with db.connect() as c:
        rid = c.execute("INSERT INTO reports(kind, text, page, version, viewport, created_by, created_at, updated_at) "
                        "VALUES (?,?,?,?,?,?,?,?)", (kind, text.strip(), page[:300], SITE_VERSION, viewport[:40],
                                                     uid(request), db.now(), db.now())).lastrowid
        if data:
            REPORTS_DIR.mkdir(parents=True, exist_ok=True)
            name = f"report{rid}{ext}"
            (REPORTS_DIR / name).write_bytes(data)
            c.execute("UPDATE reports SET shot_path=? WHERE id=?", (name, rid))
    return {"ok": True, "id": rid}


def _sync_reports(rows: list[dict]) -> None:
    """Заявки, отправленные в правки: закрытая на GitHub задача = «Сделано»."""
    linked = [r for r in rows if r["issue_number"] and r["status"] == "work"]
    if not linked:
        return
    try:
        done = {n["number"] for n in feedback.list_notes() if n["status"] in ("done", "closed")}
    except GitHubError:
        return
    with db.connect() as c:
        for r in linked:
            if r["issue_number"] in done:
                r["status"] = "done"
                c.execute("UPDATE reports SET status='done', updated_at=? WHERE id=?", (db.now(), r["id"]))


@app.get("/api/reports", dependencies=[Depends(auth.admin)])
def report_list():
    with db.connect() as c:
        rows = db.rows(c.execute("SELECT * FROM reports ORDER BY id DESC LIMIT 300"))
        names = {r["id"]: r["name"] for r in c.execute("SELECT id, name FROM users")}
    _sync_reports(rows)
    return {"reports": [_report_out(r, names) for r in rows], "kinds": REPORT_KINDS, "statuses": REPORT_STATUSES}


@app.get("/api/reports/{rid}/shot", dependencies=[Depends(auth.admin)])
def report_shot(rid: int):
    with db.connect() as c:
        r = c.execute("SELECT shot_path FROM reports WHERE id=?", (rid,)).fetchone()
    if not r or not r["shot_path"] or not (REPORTS_DIR / r["shot_path"]).is_file():
        raise HTTPException(404, "Снимка нет")
    return FileResponse(REPORTS_DIR / r["shot_path"])


class ReportPatch(BaseModel):
    status: str


@app.put("/api/reports/{rid}", dependencies=[Depends(auth.admin)])
def report_update(rid: int, p: ReportPatch):
    if p.status not in REPORT_STATUSES:
        raise HTTPException(400, "Неизвестный статус")
    with db.connect() as c:
        c.execute("UPDATE reports SET status=?, updated_at=? WHERE id=?", (p.status, db.now(), rid))
    return {"ok": True}


class ReportForward(BaseModel):
    with_shot: bool = False


@app.post("/api/reports/{rid}/forward", dependencies=[Depends(auth.admin)])
def report_forward(rid: int, f: ReportForward, request: Request):
    """Админ отправляет заявку в правки: она становится задачей на GitHub с меткой «правка», её подхватывает Claude.
    Репозиторий публичный, поэтому снимок уходит только по галочке."""
    with db.connect() as c:
        r = db.row(c.execute("SELECT * FROM reports WHERE id=?", (rid,)).fetchone())
        if not r:
            raise HTTPException(404, "Заявка не найдена")
        author = (c.execute("SELECT name FROM users WHERE id=?", (r["created_by"],)).fetchone() or {"name": "—"})["name"]
    if r["issue_number"]:
        raise HTTPException(400, f"Заявка уже в правках: задача #{r['issue_number']}")
    shots = []
    if f.with_shot and r["shot_path"] and (REPORTS_DIR / r["shot_path"]).is_file():
        shots.append(((REPORTS_DIR / r["shot_path"]).read_bytes(), r["shot_path"].rsplit(".", 1)[-1]))
    text = f"[{REPORT_KINDS.get(r['kind'], r['kind'])}] {r['text']}\n\n(Заявка #{rid} из «Сообщить о проблеме», версия сайта {r['version'] or '—'})"
    note = _gh(feedback.create, text, r["page"], r["viewport"], author, shots)
    with db.connect() as c:
        c.execute("UPDATE reports SET issue_number=?, status='work', updated_at=? WHERE id=?", (note["number"], db.now(), rid))
    return {"ok": True, "number": note["number"], "url": note.get("url", "")}


# ---------- значки в меню: срочные серии, уведомления, новые заявки ----------

@app.get("/api/badges")
def badges(request: Request):
    user = request.state.user
    me = user["id"]
    with db.connect() as c:
        urgent = c.execute("SELECT COUNT(*) FROM episodes WHERE urgent=1 AND status!='posted' AND number IS NOT NULL").fetchone()[0]
        notes = []
        if user["role"] != "admin" and auth.has(user, "generation"):  # «серия сгенерировалась» — нейронщику
            for n in db.rows(c.execute("SELECT n.*, e.status AS ep_status FROM notifications n LEFT JOIN episodes e "
                                       "ON e.id=n.episode_id WHERE n.role=? AND COALESCE(e.deleted_at,'')='' AND n.created_at >= ? ORDER BY n.id DESC LIMIT 50",
                                       ("editor", (date.today() - timedelta(days=14)).isoformat()))):
                if me not in (n["seen_by"] or []):
                    notes.append({k: n[k] for k in ("id", "kind", "episode_id", "text", "created_at", "ep_status")})
        new_reports = c.execute("SELECT COUNT(*) FROM reports WHERE status='new'").fetchone()[0] if user["role"] == "admin" else 0
    return {"urgent": urgent, "notifications": notes, "reports": new_reports}


class SeenIn(BaseModel):
    ids: list[int] = []
    episode_id: int | None = None


@app.post("/api/notifications/seen")
def notifications_seen(p: SeenIn, request: Request):
    me = uid(request)
    with db.connect() as c:
        q, args = ("SELECT id, seen_by FROM notifications WHERE episode_id=?", (p.episode_id,)) if p.episode_id \
            else (f"SELECT id, seen_by FROM notifications WHERE id IN ({','.join('?' * len(p.ids)) or 'NULL'})", tuple(p.ids))
        for n in db.rows(c.execute(q, args)):
            seen = n["seen_by"] or []
            if me not in seen:
                c.execute("UPDATE notifications SET seen_by=? WHERE id=?", (db.dumps([*seen, me]), n["id"]))
    return {"ok": True}


# ---------- «Тех. требования»: шаблоны ТТ ----------

class TemplateIn(BaseModel):
    name: str
    kind: str = "video"
    model: str = ""
    specs: str = ""
    prompt: str = ""
    notes: str = ""
    in_prompt: bool = False


def _template_values(t: TemplateIn) -> tuple:
    if not t.name.strip():
        raise HTTPException(400, "Назовите шаблон")
    if t.kind not in db.TEMPLATE_KINDS:
        raise HTTPException(400, "Неизвестный вид шаблона")
    return t.name.strip(), t.kind, t.model.strip(), t.specs.strip(), t.prompt.strip(), t.notes.strip(), int(t.in_prompt)


@app.get("/api/templates")
def list_templates():
    with db.connect() as c:
        return {"kinds": db.TEMPLATE_KINDS,
                "templates": db.rows(c.execute("SELECT * FROM templates ORDER BY position, id"))}


@app.post("/api/templates", dependencies=[Depends(auth.need("tech"))])
def create_template(t: TemplateIn):
    with db.connect() as c:
        pos = c.execute("SELECT COALESCE(MAX(position), -1) + 1 FROM templates").fetchone()[0]
        c.execute("INSERT INTO templates(name, kind, model, specs, prompt, notes, in_prompt, position, created_at) "
                  "VALUES (?,?,?,?,?,?,?,?,?)", (*_template_values(t), pos, db.now()))
    refresh_all_prompts()
    return list_templates()


@app.put("/api/templates/{tid}", dependencies=[Depends(auth.need("tech"))])
def update_template(tid: int, t: TemplateIn):
    with db.connect() as c:
        c.execute("UPDATE templates SET name=?, kind=?, model=?, specs=?, prompt=?, notes=?, in_prompt=? WHERE id=?",
                  (*_template_values(t), tid))
    refresh_all_prompts()
    return list_templates()


@app.delete("/api/templates/{tid}", dependencies=[Depends(auth.need("tech"))])
def delete_template(tid: int):
    with db.connect() as c:
        c.execute("DELETE FROM templates WHERE id=?", (tid,))
    refresh_all_prompts()
    return list_templates()


# ---------- settings ----------

# farmhub_api_key, farmhub_webhook_secret и anthropic_api_key: прежние схемы связи с фермой и перевода; значения, если их успели вписать, остаются в базе и не отдаются
SECRET_SETTINGS = ("veo_api_key", "github_token", "magnific_api_key", "farmhub_secret", "farmhub_api_key", "farmhub_webhook_secret", "anthropic_api_key")
PROMPT_SETTINGS = {"style", "consistency_character", "consistency_location", "aspect_ratio", "veo_model", "veo_route", "dialogue_language", "words_per_second", "max_shot_seconds"}


@app.get("/api/settings")
def read_settings():
    s = db.get_settings()
    s.pop("media_secret", None)
    for k in SECRET_SETTINGS:
        s[k] = ("••••" + s[k][-4:]) if s.get(k) else ""
    s["farmhub_env"] = {"secret": bool(os.environ.get("FARMHUB_SECRET"))}  # что задано переменными сервера
    s["farmhub_seen"] = farm.seen_ago()  # секунд назад Пульт в последний раз обращался к студии (пусто: с запуска не обращался)
    return s


@app.put("/api/settings", dependencies=[Depends(auth.admin)])
def write_settings(values: dict):
    with db.connect() as c:
        for k, v in values.items():
            if k not in db.DEFAULT_SETTINGS or k.startswith("anchor_"):
                continue
            if k in SECRET_SETTINGS and str(v).startswith("••••"):
                continue
            c.execute("INSERT OR REPLACE INTO settings(key, value) VALUES (?,?)", (k, str(v)))
    if PROMPT_SETTINGS & values.keys():
        refresh_all_prompts()
    return read_settings()


# ---------- рендерферма: Пульт сам ходит на сайт (секрет проверяет session_guard) ----------

@app.get("/api/farmhub/jobs/pending")
def farmhub_pending(request: Request):
    """Шоты, которые ждут ферму (не больше 50, что раньше — тем раньше). Картинки отдаются ссылками на этот же сайт."""
    return {"shots": farm.pending(farm.site_base(db.get_settings(), request.headers, request.url.scheme))}


@app.post("/api/farmhub/jobs/{ref}/status")
def farmhub_status(ref: str, body: dict = Body(...)):
    """Событие Пульта по шоту: принят, отклонён, старт, проценты, этап, конец. {ref} — наш id шота (take-N)."""
    try:
        return {"ok": True, **farm.apply_status(ref, body)}
    except LookupError as e:
        raise HTTPException(404, str(e)) from None
    except ValueError as e:
        raise HTTPException(400, str(e)) from None


@app.post("/api/farmhub/jobs/{ref}/files")
def farmhub_files(ref: str, kind: str = Form(...), file: UploadFile = File(...), name: str = Form(""), job_id: str = Form("")):
    """Черновик (kind=draft) или готовый ролик (kind=final) от Пульта; повторная загрузка заменяет файл."""
    try:
        return {"ok": True, **farm.save_file(ref, kind, file.file)}
    except LookupError as e:
        raise HTTPException(404, str(e)) from None
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    except OverflowError as e:
        raise HTTPException(413, str(e)) from None
    except OSError as e:
        raise HTTPException(507, str(e)) from None


# ---------- API keys of neural networks ----------

class IntegrationIn(BaseModel):
    name: str | None = None
    kind: str | None = None
    api_key: str | None = None


def integration_out(r: dict) -> dict:
    k = r.get("api_key") or ""
    return {"id": r["id"], "name": r["name"], "kind": r["kind"], "has_key": bool(k), "key_mask": ("••••" + k[-4:]) if k else ""}


@app.get("/api/integrations", dependencies=[Depends(auth.admin)])
def list_integrations():
    with db.connect() as c:
        return [integration_out(r) for r in db.rows(c.execute("SELECT * FROM integrations ORDER BY id"))]


@app.post("/api/integrations", dependencies=[Depends(auth.admin)])
def add_integration(p: IntegrationIn):
    name = (p.name or "").strip()
    if not name:
        raise HTTPException(400, "Дайте нейросети имя")
    kind = p.kind if p.kind in (*ENGINE_NAMES, "elevenlabs") else "other"
    with db.connect() as c:
        c.execute("INSERT INTO integrations(name, kind, api_key, created_at) VALUES (?,?,?,?)",
                  (name, kind, (p.api_key or "").strip(), db.now()))
    return list_integrations()


@app.put("/api/integrations/{iid}", dependencies=[Depends(auth.admin)])
def edit_integration(iid: int, p: IntegrationIn):
    with db.connect() as c:
        r = db.row(c.execute("SELECT * FROM integrations WHERE id=?", (iid,)).fetchone())
        if not r:
            raise HTTPException(404, "Не найдено")
        name = (p.name if p.name is not None else r["name"]).strip() or r["name"]
        kind = p.kind if p.kind in (*ENGINE_NAMES, "elevenlabs") else r["kind"]
        key = r["api_key"] if p.api_key is None or p.api_key.startswith("••••") else p.api_key.strip()
        c.execute("UPDATE integrations SET name=?, kind=?, api_key=? WHERE id=?", (name, kind, key, iid))
    return list_integrations()


@app.delete("/api/integrations/{iid}", dependencies=[Depends(auth.admin)])
def delete_integration(iid: int):
    with db.connect() as c:
        c.execute("DELETE FROM integrations WHERE id=?", (iid,))
    return list_integrations()


# Промпты шотов пересобираются при запуске: подхватывают новые правила сборки и изменения в библиотеке (закреплённые вручную не трогаем)
try:
    refresh_all_prompts()
except Exception as e:  # noqa: BLE001 - запуск студии важнее
    print("Не удалось пересобрать промпты:", e)


# ---------- static ----------

@app.get("/pub/{sig}/{rel:path}")
def public_media(sig: str, rel: str):
    """Картинка по подписанной ссылке: так её забирает Magnific у моделей, которые принимают картинки только ссылкой.
    Подпись привязана к пути файла, поэтому по ней нельзя открыть другие файлы студии."""
    f = (db.MEDIA_DIR / rel).resolve()
    if not hmac.compare_digest(sig, models.sign(rel)) or db.MEDIA_DIR.resolve() not in f.parents or not f.is_file() \
            or f.suffix.lower() not in IMAGE_EXT:
        raise HTTPException(404, "Не найдено")
    return FileResponse(f)


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return FileResponse(STATIC_DIR / "favicon.svg", media_type="image/svg+xml")


app.mount("/media", StaticFiles(directory=db.MEDIA_DIR), name="media")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"})
