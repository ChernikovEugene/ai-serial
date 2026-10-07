import json
import re
import uuid
from datetime import date, timedelta
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import auth, breakdown, db, feedback, schedule, veo
from .github import GitHubError

STATIC_DIR = Path(__file__).resolve().parent / "static"
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp"}
VIDEO_EXT = {".mp4", ".mov", ".webm"}

HOLIDAYS = {
    "01-01": "Новый год", "01-07": "Рождество", "01-25": "Татьянин день", "02-14": "День святого Валентина",
    "02-23": "23 февраля", "03-08": "8 марта", "04-01": "День смеха", "04-12": "День космонавтики",
    "05-01": "Праздник весны и труда", "05-09": "День Победы", "06-01": "День защиты детей",
    "06-12": "День России", "07-08": "День семьи, любви и верности", "09-01": "День знаний",
    "10-31": "Хэллоуин", "11-04": "День народного единства", "12-31": "Новогодняя ночь",
}

db.init()
veo.recover()
app = FastAPI(title="Студия сериала")


# ---------- auth middleware ----------

OPEN_PATHS = ("/api/auth/",)
VIEWER_WRITE_OK = re.compile(r"^/api/(episodes/\d+/comments|comments/\d+|auth/.*)$")


@app.middleware("http")
async def session_guard(request: Request, call_next):
    path = request.url.path
    request.state.user = None
    if path.startswith("/api/") or path.startswith("/media/"):
        user = auth.user_for_token(request.cookies.get(auth.COOKIE))
        request.state.user = user
        if not path.startswith(OPEN_PATHS):
            if not user:
                return JSONResponse({"detail": "Нужно войти"}, status_code=401)
            if user["role"] == "viewer" and request.method != "GET" and not VIEWER_WRITE_OK.match(path):
                return JSONResponse({"detail": "Недостаточно прав: роль «Зритель»"}, status_code=403)
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


@app.get("/api/users")
def list_users():
    with db.connect() as c:
        return db.rows(c.execute("SELECT id, login, name, role, created_at FROM users ORDER BY id"))


@app.post("/api/users", dependencies=[Depends(auth.admin)])
def create_user(u: UserIn):
    if not u.login or not u.password or len(u.password) < 4:
        raise HTTPException(400, "Нужны логин и пароль (от 4 символов)")
    if u.role and u.role not in auth.ROLES:
        raise HTTPException(400, "Неизвестная роль")
    with db.connect() as c:
        if c.execute("SELECT 1 FROM users WHERE login=?", (u.login.strip().lower(),)).fetchone():
            raise HTTPException(400, "Такой логин уже есть")
        c.execute("INSERT INTO users(login, name, pass_hash, role, created_at) VALUES (?,?,?,?,?)",
                  (u.login.strip().lower(), (u.name or u.login).strip(), auth.hash_password(u.password),
                   u.role or "writer", db.now()))
    return list_users()


@app.put("/api/users/{user_id}")
def update_user(user_id: int, u: UserIn, request: Request):
    me = request.state.user
    if me["role"] != "admin" and me["id"] != user_id:
        raise HTTPException(403, "Недостаточно прав")
    with db.connect() as c:
        if u.name is not None:
            c.execute("UPDATE users SET name=? WHERE id=?", (u.name.strip(), user_id))
        if u.password:
            if len(u.password) < 4:
                raise HTTPException(400, "Пароль от 4 символов")
            c.execute("UPDATE users SET pass_hash=? WHERE id=?", (auth.hash_password(u.password), user_id))
        if u.role is not None and me["role"] == "admin":
            if u.role not in auth.ROLES:
                raise HTTPException(400, "Неизвестная роль")
            admins = c.execute("SELECT COUNT(*) FROM users WHERE role='admin' AND id<>?", (user_id,)).fetchone()[0]
            if u.role != "admin" and not admins:
                raise HTTPException(400, "Должен остаться хотя бы один администратор")
            c.execute("UPDATE users SET role=? WHERE id=?", (u.role, user_id))
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
    return {
        "user": request.state.user,
        "statuses": [{"key": k, "name": n} for k, n in db.STATUSES],
        "roles": auth.ROLES,
        "status_flow": status_flow(request.state.user["role"]),
        "veo_provider": s["veo_provider"], "veo_model": s["veo_model"],
        "max_shot_seconds": int(s["max_shot_seconds"]), "words_per_second": float(s["words_per_second"]),
        "anchor_number": int(s["anchor_number"]), "anchor_date": s["anchor_date"],
        "today": date.today().isoformat(),
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
    rel = f"{folder}/{uuid.uuid4().hex[:12]}{ext}"
    dest = db.MEDIA_DIR / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("wb") as out:
        while chunk := f.file.read(1 << 20):
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
    prompt, refs, _ = breakdown.build_prompt(s, ctx.v_map, ctx.a_map, ctx.settings)
    forced = breakdown.forced_duration(refs, s, ctx.settings)
    if forced and s["duration"] != forced:
        s["duration"] = forced
        prompt, refs, _ = breakdown.build_prompt(s, ctx.v_map, ctx.a_map, ctx.settings)
    if not s.get("prompt_locked"):
        s["prompt"] = prompt
    return s


def shot_out(s: dict, ctx: Ctx, takes: list[dict]) -> dict:
    _, refs, warns = breakdown.build_prompt(s, ctx.v_map, ctx.a_map, ctx.settings)
    w = breakdown.shot_warnings(s, ctx.max_s)
    if not s.get("location_version_id") and s.get("scene"):
        w.append(f"Локация «{s['scene']}» не найдена в библиотеке")
    s["warnings"] = w + warns
    s["missing"] = breakdown.shot_missing(s, ctx.max_s)
    s["revisions"] = max(0, len(takes) - 1)  # how many times this shot was re-generated
    s["over_limit"] = s["est_seconds"] > ctx.max_s
    s["forced_8"] = bool(breakdown.forced_duration(refs, s, ctx.settings))
    s["references"] = refs
    s["takes"] = takes
    sel = next((t for t in takes if t["id"] == s.get("selected_take_id")), None)
    s["selected_take"] = sel
    last = takes[-1] if takes else None
    s["gen_status"] = last["status"] if last else "idle"
    return s


SHOT_COLS = ["idx", "label", "scene", "duration", "est_seconds", "speech_seconds", "words", "camera", "action",
             "dialogue", "characters", "location_version_id", "composition_image", "composition_mode",
             "composition_note", "prompt", "negative_prompt", "prompt_locked", "selected_take_id", "needs_redo"]


def save_shot(c, s: dict):
    vals = [db.dumps(s[k]) if k in ("dialogue", "characters") else s.get(k) for k in SHOT_COLS]
    vals = [int(v) if isinstance(v, bool) else v for v in vals]
    if s.get("id"):
        c.execute(f"UPDATE shots SET {', '.join(f'{k}=?' for k in SHOT_COLS)}, updated_at=? WHERE id=?",
                  (*vals, db.now(), s["id"]))
    else:
        s["id"] = c.execute(
            f"INSERT INTO shots(episode_id, {', '.join(SHOT_COLS)}, updated_at) VALUES (?, {', '.join('?' * len(SHOT_COLS))}, ?)",
            (s["episode_id"], *vals, db.now())).lastrowid


def episode_cast(eid: int) -> dict:
    with db.connect() as c:
        return {r["asset_id"]: r["version_id"] for r in c.execute("SELECT * FROM episode_cast WHERE episode_id=?", (eid,))}


def load_episode_row(eid: int) -> dict:
    with db.connect() as c:
        ep = db.row(c.execute("SELECT * FROM episodes WHERE id=?", (eid,)).fetchone())
    if not ep:
        raise HTTPException(404, "Серия не найдена")
    return ep


def writer_may_edit(request: Request, status: str) -> None:
    """Once the script is approved only the producer can change it, so the editor works on a stable text."""
    if request.state.user["role"] == "writer" and status not in ("dev", "review"):
        raise HTTPException(403, "Сценарий уже согласован — править его может только продюсер. "
                                 "Верните серию в «В разработке» или попросите продюсера")


def incomplete_shots(eid: int) -> list[str]:
    ep = get_episode(eid)
    if not ep["shots"]:
        return ["В серии нет ни одного шота"]
    return [f"Шот {s['label']}: {'; '.join(s['missing'])}" for s in ep["shots"] if s["missing"]]


def user_names() -> dict:
    with db.connect() as c:
        return {r["id"]: r["name"] or r["login"] for r in c.execute("SELECT id, name, login FROM users")}


# ---------- assets: characters & locations ----------

@app.get("/api/assets")
def list_assets(kind: str | None = None):
    return load_assets(kind)


@app.post("/api/assets", dependencies=[Depends(auth.writer)])
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
        asset_id = c.execute("INSERT INTO assets(kind, name, aliases, created_by, created_at) VALUES (?,?,?,?,?)",
                             (kind, name, db.dumps(alias_list), uid(request), db.now())).lastrowid
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


@app.put("/api/assets/{asset_id}", dependencies=[Depends(auth.writer)])
def update_asset(asset_id: int, p: AssetPatch):
    with db.connect() as c:
        if p.name is not None and p.name.strip():
            c.execute("UPDATE assets SET name=? WHERE id=?", (p.name.strip(), asset_id))
        if p.aliases is not None:
            c.execute("UPDATE assets SET aliases=? WHERE id=?", (db.dumps([a.strip() for a in p.aliases if a.strip()]), asset_id))
    return get_asset(asset_id)


@app.delete("/api/assets/{asset_id}", dependencies=[Depends(auth.admin)])
def delete_asset(asset_id: int):
    with db.connect() as c:
        c.execute("DELETE FROM assets WHERE id=?", (asset_id,))
    return {"ok": True}


@app.post("/api/assets/{asset_id}/versions", dependencies=[Depends(auth.writer)])
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


@app.post("/api/assets/{asset_id}/versions/{version_id}/activate", dependencies=[Depends(auth.writer)])
def activate_version(asset_id: int, version_id: int):
    with db.connect() as c:
        if not c.execute("SELECT 1 FROM asset_versions WHERE id=? AND asset_id=?", (version_id, asset_id)).fetchone():
            raise HTTPException(404, "Версия не найдена")
        c.execute("UPDATE assets SET active_version_id=? WHERE id=?", (version_id, asset_id))
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
        eps = db.rows(c.execute("SELECT id, number, pinned, planned_date, title, status, posted_at, updated_at "
                                "FROM episodes ORDER BY number IS NULL, number, id"))
        stats = {r["episode_id"]: dict(r) for r in c.execute(
            "SELECT episode_id, COUNT(*) AS shots, COALESCE(SUM(duration),0) AS seconds, "
            "COALESCE(SUM(est_seconds),0) AS est, SUM(est_seconds > ?) AS over, "
            "SUM(selected_take_id IS NOT NULL) AS with_take, SUM(needs_redo) AS redo FROM shots GROUP BY episode_id",
            (max_s,))}
        fixes = {r[0]: r[1] for r in c.execute(
            "SELECT episode_id, COUNT(*) FROM comments WHERE is_fix=1 AND resolved=0 GROUP BY episode_id")}
    for e in eps:
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
        out.append(t)
    text = " / ".join(out)
    return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0] + "…"


@app.get("/api/month-status")
def month_status(month: str, request: Request):
    """Страница «Статус месяца»: серии, выходящие в месяце YYYY-MM, + короткое описание из сценария.
    Серии, которые роль не видит (монтажёр до согласования сценария), показываются только этапом: без названия,
    описания и ссылки (`locked`), чтобы картина месяца оставалась полной."""
    try:
        y, m = (int(x) for x in month.split("-"))
        first = date(y, m, 1)
    except (ValueError, TypeError):
        raise HTTPException(400, "Нужен месяц в формате ГГГГ-ММ")
    last = date(y + (m == 12), m % 12 + 1, 1) - timedelta(days=1)
    role = request.state.user["role"]
    all_eps = episode_summaries()
    eps = [e for e in all_eps if e["date"] and first.isoformat() <= e["date"] <= last.isoformat()]
    with db.connect() as c:
        scripts = {r["id"]: r["script"] or "" for r in c.execute("SELECT id, script FROM episodes")}
    for e in eps:
        e["has_script"] = bool(scripts.get(e["id"], "").strip())
        e["locked"] = not auth.can_see(role, e["status"])
        e["blurb"] = "" if e["locked"] else _blurb(scripts.get(e["id"], ""))
        if e["locked"]:
            e["title"] = "Сценарий ещё не согласован"
    return {"month": month, "first": first.isoformat(), "days": last.day,
            "first_number": schedule.number_for(first.isoformat()),
            "backlog": sum(1 for e in all_eps if not e["date"] and auth.can_see(role, e["status"])), "episodes": eps}


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
            if ev["date"] == d.isoformat() or (ev["yearly"] and ev["date"][5:] == md):
                events.append({"id": ev["id"], "date": d.isoformat(), "title": ev["title"], "builtin": False})
        d += timedelta(days=1)
    return {"episodes": eps, "events": events}


class CalendarEventIn(BaseModel):
    date: str
    title: str
    yearly: bool = False


@app.post("/api/calendar-events", dependencies=[Depends(auth.writer)])
def add_calendar_event(e: CalendarEventIn, request: Request):
    date.fromisoformat(e.date)
    with db.connect() as c:
        c.execute("INSERT INTO calendar_events(date, title, yearly, created_by) VALUES (?,?,?,?)",
                  (e.date, e.title.strip(), int(e.yearly), uid(request)))
    return {"ok": True}


@app.delete("/api/calendar-events/{ev_id}", dependencies=[Depends(auth.writer)])
def delete_calendar_event(ev_id: int):
    with db.connect() as c:
        c.execute("DELETE FROM calendar_events WHERE id=?", (ev_id,))
    return {"ok": True}


class AnchorIn(BaseModel):
    number: int
    date: str


@app.put("/api/schedule/anchor", dependencies=[Depends(auth.writer)])
def set_anchor(a: AnchorIn):
    date.fromisoformat(a.date)
    with db.connect() as c:
        c.execute("UPDATE settings SET value=? WHERE key='anchor_number'", (str(a.number),))
        c.execute("UPDATE settings SET value=? WHERE key='anchor_date'", (a.date,))
        schedule.normalize(c)
    return {"ok": True}


@app.post("/api/schedule/compact", dependencies=[Depends(auth.writer)])
def compact_schedule():
    schedule.compact()
    return {"ok": True}


class EpisodeIn(BaseModel):
    title: str = ""
    script: str = ""
    number: int | None = None
    date: str | None = None
    backlog: bool = False


def create_episode_row(e: EpisodeIn, user_id) -> int:
    with db.connect() as c:
        eid = c.execute("INSERT INTO episodes(title, script, status, created_by, created_at, updated_at) VALUES (?,?,?,?,?,?)",
                        (e.title.strip(), e.script, "dev", user_id, db.now(), db.now())).lastrowid
    if e.date:
        schedule.move(eid, schedule.number_for(e.date))
        schedule.set_pinned(eid, True)
    elif not e.backlog:
        schedule.move(eid, e.number or schedule.next_free())
    db.log(eid, user_id, "Серия создана")
    if e.script.strip():
        apply_breakdown(eid)
    return eid


@app.post("/api/episodes", dependencies=[Depends(auth.writer)])
def create_episode(e: EpisodeIn, request: Request):
    return get_episode(create_episode_row(e, uid(request)))


def decode_text(raw: bytes) -> str:
    for enc in ("utf-8-sig", "cp1251"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    raise HTTPException(400, "Не удалось прочитать файл: нужен текст в UTF-8 или Windows-1251")


@app.post("/api/episodes/upload", dependencies=[Depends(auth.writer)])
async def upload_episode(request: Request, file: UploadFile = File(...), title: str = Form(""),
                         date_: str = Form("", alias="date"), backlog: bool = Form(False)):
    text = decode_text(await file.read())
    e = EpisodeIn(title=title or Path(file.filename or "").stem, script=text, date=date_ or None, backlog=backlog)
    return get_episode(create_episode_row(e, uid(request)))


@app.post("/api/read-text", dependencies=[Depends(auth.writer)])
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
    ep["status_name"] = db.STATUS_NAMES.get(ep["status"], ep["status"])
    ep["cast"] = episode_cast(eid)
    ep["marked"] = breakdown.has_markers(ep["script"])
    return ep


class EpisodePatch(BaseModel):
    title: str | None = None
    script: str | None = None
    notes: str | None = None


@app.put("/api/episodes/{eid}", dependencies=[Depends(auth.writer)])
def update_episode(eid: int, p: EpisodePatch, request: Request, reparse: bool = True):
    ep = load_episode_row(eid)
    writer_may_edit(request, ep["status"])
    with db.connect() as c:
        for k, v in p.model_dump(exclude_unset=True).items():
            if v is not None:
                c.execute(f"UPDATE episodes SET {k}=?, updated_at=? WHERE id=?", (v, db.now(), eid))
    if p.script is not None and p.script != ep["script"]:
        db.log(eid, uid(request), "Сценарий изменён")
        if reparse:
            apply_breakdown(eid)
    return get_episode(eid)


class StatusIn(BaseModel):
    status: str
    force: bool = False  # producer only: approve even though some shots are incomplete


@app.post("/api/episodes/{eid}/status")
def set_status(eid: int, s: StatusIn, request: Request):
    if s.status not in db.STATUS_NAMES:
        raise HTTPException(400, "Неизвестный статус")
    role = request.state.user["role"]
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


@app.post("/api/episodes/{eid}/move", dependencies=[Depends(auth.writer)])
def move_episode(eid: int, m: MoveIn, request: Request):
    ep = load_episode_row(eid)
    number = schedule.number_for(m.date) if m.date else m.number
    if number is not None and number < 1:
        raise HTTPException(400, "Эта дата раньше старта сериала. Поменяйте точку отсчёта в очереди")
    schedule.move(eid, number)
    if ep["number"] != number:
        db.log(eid, uid(request), f"Перенесена: {'в бэклог' if number is None else f'серия №{number}, {schedule.date_for(number)}'}")
    return {"ok": True}


class PinIn(BaseModel):
    pinned: bool


@app.post("/api/episodes/{eid}/pin", dependencies=[Depends(auth.writer)])
def pin_episode(eid: int, p: PinIn, request: Request):
    schedule.set_pinned(eid, p.pinned)
    db.log(eid, uid(request), "Дата закреплена" if p.pinned else "Дата откреплена")
    return {"ok": True}


@app.delete("/api/episodes/{eid}", dependencies=[Depends(auth.admin)])
def delete_episode(eid: int):
    with db.connect() as c:
        c.execute("DELETE FROM episodes WHERE id=?", (eid,))
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


@app.post("/api/analyze", dependencies=[Depends(auth.writer)])
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


def apply_breakdown(eid: int):
    """Re-parse the script and merge into existing shots by shot label, keeping composition refs,
    takes, comments and manually locked prompts."""
    ep = load_episode_row(eid)
    ctx = Ctx()
    new, notes = breakdown.parse_script(ep["script"], ctx.assets, ctx.settings, episode_cast(eid))
    with db.connect() as c:
        old = db.rows(c.execute("SELECT * FROM shots WHERE episode_id=? ORDER BY idx", (eid,)))
        pool = {}
        for o in old:
            pool.setdefault(o["label"] or str(o["idx"]), []).append(o)
        keep_ids = set()
        for n in new:
            match = (pool.get(n["label"]) or [None]).pop(0) if pool.get(n["label"]) else None
            s = dict(match) if match else {"episode_id": eid, "composition_image": "", "composition_mode": "reference",
                                           "composition_note": "", "prompt": "", "prompt_locked": 0,
                                           "negative_prompt": ctx.settings.get("negative_prompt", ""),
                                           "selected_take_id": None, "needs_redo": 0}
            s.update({k: n[k] for k in ("idx", "label", "scene", "duration", "camera", "action", "dialogue",
                                        "characters", "location_version_id")})
            refresh_shot(s, ctx)
            save_shot(c, s)
            keep_ids.add(s["id"])
        for o in old:
            if o["id"] not in keep_ids:
                c.execute("DELETE FROM shots WHERE id=?", (o["id"],))
        c.execute("UPDATE episodes SET parse_notes=?, updated_at=? WHERE id=?", (db.dumps(notes), db.now(), eid))


@app.post("/api/episodes/{eid}/breakdown", dependencies=[Depends(auth.writer)])
def breakdown_episode(eid: int, request: Request):
    writer_may_edit(request, load_episode_row(eid)["status"])
    apply_breakdown(eid)
    return get_episode(eid)


class CastIn(BaseModel):
    cast: list[dict]  # [{asset_id, version_id|null}]


@app.put("/api/episodes/{eid}/cast", dependencies=[Depends(auth.writer)])
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
    return get_episode(eid)


class GenerateIn(BaseModel):
    mode: str = "missing"  # missing | redo | all


@app.post("/api/episodes/{eid}/generate", dependencies=[Depends(auth.editor)])
def generate_episode(eid: int, g: GenerateIn, request: Request):
    ep = get_episode(eid)
    if not ep["shots"]:
        raise HTTPException(400, "В серии нет шотов")
    settings = db.get_settings()
    started = 0
    for s in ep["shots"]:
        busy = s["gen_status"] in ("queued", "running")
        if busy:
            continue
        if g.mode == "missing" and s["selected_take_id"] and not s["needs_redo"]:
            continue
        if g.mode == "redo" and not (s["needs_redo"] or s["open_fixes"]):
            continue
        veo.start(s, s["references"], settings, uid(request))
        started += 1
    if started:
        with db.connect() as c:
            c.execute("UPDATE episodes SET status='generating', updated_at=? WHERE id=?", (db.now(), eid))
        db.log(eid, uid(request), f"Отправлено в Veo шотов: {started}")
    return {"started": started}


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
    composition_mode: str | None = None
    composition_note: str | None = None
    prompt: str | None = None
    negative_prompt: str | None = None
    prompt_locked: bool | None = None
    needs_redo: bool | None = None


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
WRITER_SHOT_FIELDS = {"scene", "camera", "action", "dialogue", "characters", "location_version_id", "clear_location"}
EDITOR_SHOT_FIELDS = {"duration", "composition_mode", "composition_note", "prompt", "negative_prompt",
                      "prompt_locked", "needs_redo"}


@app.put("/api/shots/{sid}")
def update_shot(sid: int, p: ShotPatch, request: Request):
    s = load_shot(sid)
    data = p.model_dump(exclude_unset=True)
    role = request.state.user["role"]
    if role != "admin":
        own = WRITER_SHOT_FIELDS if role == "writer" else EDITOR_SHOT_FIELDS if role == "editor" else set()
        foreign = sorted(set(data) - own)
        if foreign:
            who = "сценарист" if role == "editor" else "монтажёр"
            raise HTTPException(403, f"Эти поля меняет {who}: {', '.join(foreign)}")
        if role == "writer":
            writer_may_edit(request, load_episode_row(s["episode_id"])["status"])
    if data.pop("clear_location", False):
        s["location_version_id"] = None
    if "duration" in data and data["duration"] not in breakdown.ALLOWED_DURATIONS:
        raise HTTPException(400, "Veo поддерживает длительность 4, 6 или 8 секунд")
    if data.get("composition_mode") not in (None, "reference", "first_frame"):
        raise HTTPException(400, "Неизвестный режим композиции")
    for k, v in data.items():
        if v is not None or k == "location_version_id":
            s[k] = v
    if "prompt" in data:
        s["prompt_locked"] = True if p.prompt_locked is None else p.prompt_locked
    ctx = Ctx()
    refresh_shot(s, ctx)
    with db.connect() as c:
        save_shot(c, s)
    return shot_response(sid)


@app.post("/api/shots/{sid}/composition", dependencies=[Depends(auth.editor)])
def upload_composition(sid: int, image: UploadFile = File(...)):
    s = load_shot(sid)
    s["composition_image"] = save_upload(image, f"compositions/{s['episode_id']}")
    refresh_shot(s, Ctx())
    with db.connect() as c:
        save_shot(c, s)
    return shot_response(sid)


@app.delete("/api/shots/{sid}/composition", dependencies=[Depends(auth.editor)])
def delete_composition(sid: int):
    s = load_shot(sid)
    s["composition_image"] = ""
    refresh_shot(s, Ctx())
    with db.connect() as c:
        save_shot(c, s)
    return shot_response(sid)


@app.post("/api/shots/{sid}/rebuild-prompt", dependencies=[Depends(auth.editor)])
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
    return veo.preview(s, s["references"], db.get_settings())


class RegenerateIn(BaseModel):
    prompt: str | None = None   # improved prompt for this attempt; saved to the shot
    reason: str = ""            # what was wrong with the previous take


@app.post("/api/shots/{sid}/generate", dependencies=[Depends(auth.editor)])
def generate_shot(sid: int, request: Request, g: RegenerateIn | None = None):
    s = shot_response(sid)
    if s["gen_status"] in ("queued", "running"):
        raise HTTPException(400, "Этот шот уже генерируется")
    g = g or RegenerateIn()
    if g.prompt is not None and g.prompt.strip() and g.prompt.strip() != s["prompt"].strip():
        raw = load_shot(sid)
        raw["prompt"], raw["prompt_locked"] = g.prompt.strip(), 1
        with db.connect() as c:
            save_shot(c, raw)
        s = shot_response(sid)
    veo.start(s, s["references"], db.get_settings(), uid(request), g.reason)
    with db.connect() as c:
        c.execute("UPDATE episodes SET status='generating', updated_at=? WHERE id=? AND status NOT IN ('posted')",
                  (db.now(), s["episode_id"]))
    note = f" — причина: {g.reason.strip()}" if g.reason.strip() else ""
    db.log(s["episode_id"], uid(request), f"Шот {s['label']}: новый дубль (№{len(s['takes']) + 1}) отправлен в Veo{note}")
    return shot_response(sid)


class SelectTakeIn(BaseModel):
    take_id: int


@app.post("/api/shots/{sid}/select-take", dependencies=[Depends(auth.editor)])
def select_take(sid: int, t: SelectTakeIn, request: Request):
    with db.connect() as c:
        take = c.execute("SELECT * FROM takes WHERE id=? AND shot_id=?", (t.take_id, sid)).fetchone()
        if not take:
            raise HTTPException(404, "Дубль не найден")
        c.execute("UPDATE shots SET selected_take_id=? WHERE id=?", (t.take_id, sid))
        ep_id = c.execute("SELECT episode_id FROM shots WHERE id=?", (sid,)).fetchone()[0]
    db.log(ep_id, uid(request), f"Выбран дубль {take['take_no']} для шота")
    return shot_response(sid)


@app.post("/api/shots/{sid}/upload-take", dependencies=[Depends(auth.editor)])
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
    cm.is_fix = cm.is_fix and request.state.user["role"] in auth.GEN_ROLES
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


@app.post("/api/comments/{cid}/resolve", dependencies=[Depends(auth.editor)])
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

class NoteComment(BaseModel):
    text: str


def _gh(fn, *args):
    try:
        return fn(*args)
    except GitHubError as e:
        raise HTTPException(502 if e.code >= 500 else 400, str(e))


@app.get("/api/feedback")
def feedback_list():
    return _gh(feedback.list_notes)


@app.post("/api/feedback", dependencies=[Depends(auth.writer_or_editor)])
async def feedback_create(request: Request, text: str = Form(""), page: str = Form(""), viewport: str = Form(""),
                          images: list[UploadFile] = File(default=[])):
    shots = []
    for f in images:
        ext = Path(f.filename or "").suffix.lower()
        if ext not in IMAGE_EXT:
            ext = "." + (f.content_type or "image/png").split("/")[-1].replace("jpeg", "jpg")
        if ext not in IMAGE_EXT:
            continue
        data = await f.read()
        if data:
            shots.append((data, ext[1:]))
    if not text.strip() and not shots:
        raise HTTPException(400, "Напишите, что не так, или вставьте скриншот")
    return _gh(feedback.create, text, page, viewport, request.state.user["name"], shots)


@app.get("/api/feedback/{number}/comments")
def feedback_comments(number: int):
    return _gh(feedback.comments, number)


@app.post("/api/feedback/{number}/comments", dependencies=[Depends(auth.writer_or_editor)])
def feedback_comment(number: int, body: NoteComment, request: Request):
    if not body.text.strip():
        raise HTTPException(400, "Пустой ответ")
    _gh(feedback.add_comment, number, body.text.strip(), request.state.user["name"])
    return {"ok": True}


@app.post("/api/feedback/{number}/withdraw", dependencies=[Depends(auth.writer_or_editor)])
def feedback_withdraw(number: int):
    _gh(feedback.withdraw, number)
    return {"ok": True}


@app.get("/api/feedback-shot")
def feedback_shot(path: str):
    data = _gh(feedback.shot, path)
    ext = path.rsplit(".", 1)[-1]
    return Response(data, media_type=f"image/{'jpeg' if ext == 'jpg' else ext}",
                    headers={"Cache-Control": "max-age=86400"})


# ---------- settings ----------

SECRET_SETTINGS = ("veo_api_key", "github_token")


@app.get("/api/settings")
def read_settings():
    s = db.get_settings()
    for k in SECRET_SETTINGS:
        s[k] = ("••••" + s[k][-4:]) if s.get(k) else ""
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
    return read_settings()


# ---------- static ----------

app.mount("/media", StaticFiles(directory=db.MEDIA_DIR), name="media")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"})
