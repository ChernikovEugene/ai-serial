"""SQLite storage. Plain sqlite3, JSON stored in TEXT columns, additive migrations on startup."""
import json
import re
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
MEDIA_DIR = DATA_DIR / "media"
DB_PATH = DATA_DIR / "studio.db"

_lock = threading.RLock()

TABLES = {
    "users": """
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        login TEXT NOT NULL UNIQUE,
        name TEXT NOT NULL DEFAULT '',
        pass_hash TEXT NOT NULL,
        role TEXT NOT NULL DEFAULT 'editor',
        created_at TEXT NOT NULL DEFAULT '',
        tg TEXT NOT NULL DEFAULT '',
        tasks TEXT NOT NULL DEFAULT ''""",
    "sessions": """
        token TEXT PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        expires_at TEXT NOT NULL""",
    "assets": """
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        kind TEXT NOT NULL,
        name TEXT NOT NULL,
        aliases TEXT NOT NULL DEFAULT '[]',
        active_version_id INTEGER,
        created_by INTEGER,
        created_at TEXT NOT NULL DEFAULT ''""",
    "asset_versions": """
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        asset_id INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
        version_no INTEGER NOT NULL,
        label TEXT NOT NULL DEFAULT '',
        description TEXT NOT NULL DEFAULT '',
        voice TEXT NOT NULL DEFAULT '',
        notes TEXT NOT NULL DEFAULT '',
        images TEXT NOT NULL DEFAULT '[]',
        parent_version_id INTEGER,
        created_by INTEGER,
        created_at TEXT NOT NULL DEFAULT ''""",
    "episodes": """
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        number INTEGER,
        pinned INTEGER NOT NULL DEFAULT 0,
        planned_date TEXT,
        title TEXT NOT NULL DEFAULT '',
        script TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'synopsis',
        arc_id INTEGER,
        position INTEGER NOT NULL DEFAULT 0,
        synopsis TEXT NOT NULL DEFAULT '',
        post_text TEXT NOT NULL DEFAULT '',
        result_note TEXT NOT NULL DEFAULT '',
        result_url TEXT NOT NULL DEFAULT '',
        notes TEXT NOT NULL DEFAULT '',
        parse_notes TEXT NOT NULL DEFAULT '[]',
        posted_at TEXT,
        created_by INTEGER,
        created_at TEXT NOT NULL DEFAULT '',
        updated_at TEXT NOT NULL DEFAULT ''""",
    "episode_cast": """
        episode_id INTEGER NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
        asset_id INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
        version_id INTEGER NOT NULL,
        PRIMARY KEY (episode_id, asset_id)""",
    "shots": """
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        episode_id INTEGER NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
        idx INTEGER NOT NULL,
        label TEXT NOT NULL DEFAULT '',
        scene TEXT NOT NULL DEFAULT '',
        duration INTEGER NOT NULL DEFAULT 8,
        est_seconds REAL NOT NULL DEFAULT 0,
        speech_seconds REAL NOT NULL DEFAULT 0,
        words INTEGER NOT NULL DEFAULT 0,
        camera TEXT NOT NULL DEFAULT '',
        action TEXT NOT NULL DEFAULT '',
        dialogue TEXT NOT NULL DEFAULT '[]',
        characters TEXT NOT NULL DEFAULT '[]',
        location_version_id INTEGER,
        composition_image TEXT NOT NULL DEFAULT '',
        composition_mode TEXT NOT NULL DEFAULT 'reference',
        composition_note TEXT NOT NULL DEFAULT '',
        prompt TEXT NOT NULL DEFAULT '',
        negative_prompt TEXT NOT NULL DEFAULT '',
        prompt_locked INTEGER NOT NULL DEFAULT 0,
        warnings TEXT NOT NULL DEFAULT '[]',
        selected_take_id INTEGER,
        needs_redo INTEGER NOT NULL DEFAULT 0,
        updated_at TEXT NOT NULL DEFAULT ''""",
    "takes": """
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        shot_id INTEGER NOT NULL REFERENCES shots(id) ON DELETE CASCADE,
        take_no INTEGER NOT NULL,
        status TEXT NOT NULL DEFAULT 'queued',
        error TEXT NOT NULL DEFAULT '',
        video_path TEXT NOT NULL DEFAULT '',
        request_path TEXT NOT NULL DEFAULT '',
        prompt TEXT NOT NULL DEFAULT '',
        duration INTEGER NOT NULL DEFAULT 8,
        source TEXT NOT NULL DEFAULT 'veo',
        provider TEXT NOT NULL DEFAULT '',
        reason TEXT NOT NULL DEFAULT '',
        created_by INTEGER,
        created_at TEXT NOT NULL DEFAULT '',
        finished_at TEXT""",
    "comments": """
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        episode_id INTEGER NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
        shot_id INTEGER REFERENCES shots(id) ON DELETE SET NULL,
        take_id INTEGER REFERENCES takes(id) ON DELETE SET NULL,
        user_id INTEGER,
        text TEXT NOT NULL,
        timecode REAL,
        is_fix INTEGER NOT NULL DEFAULT 0,
        resolved INTEGER NOT NULL DEFAULT 0,
        resolved_by INTEGER,
        created_at TEXT NOT NULL DEFAULT ''""",
    "events": """
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        episode_id INTEGER REFERENCES episodes(id) ON DELETE CASCADE,
        user_id INTEGER,
        text TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT ''""",
    "calendar_events": """
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        date TEXT NOT NULL,
        title TEXT NOT NULL,
        yearly INTEGER NOT NULL DEFAULT 0,
        end_date TEXT NOT NULL DEFAULT '',
        created_by INTEGER""",
    "arcs": """
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        start_number INTEGER NOT NULL DEFAULT 0,
        notes TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'writing',
        members TEXT NOT NULL DEFAULT '[]',
        archived INTEGER NOT NULL DEFAULT 0,
        created_by INTEGER,
        created_at TEXT NOT NULL DEFAULT ''""",
    "templates": """
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        kind TEXT NOT NULL DEFAULT 'video',
        model TEXT NOT NULL DEFAULT '',
        specs TEXT NOT NULL DEFAULT '',
        prompt TEXT NOT NULL DEFAULT '',
        notes TEXT NOT NULL DEFAULT '',
        in_prompt INTEGER NOT NULL DEFAULT 0,
        position INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL DEFAULT ''""",
    "settings": """
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL""",
}

JSON_FIELDS = {"aliases", "images", "dialogue", "characters", "warnings", "parse_notes", "members"}

DEFAULT_SETTINGS = {
    "veo_provider": "stub",          # stub | gemini
    "veo_api_key": "",
    "veo_model": "veo-3.1-generate-preview",
    "veo_parallel": "2",
    "aspect_ratio": "9:16",
    "resolution": "720p",
    "style": "Cinematic vertical TikTok series, realistic, natural lighting, shallow depth of field",
    "negative_prompt": "subtitles, captions, text overlay, watermark, logo, distorted faces, extra fingers",
    "dialogue_language": "Russian",
    "words_per_second": "2.5",
    "max_shot_seconds": "8",
    "anchor_number": "1",
    "anchor_date": "",               # empty -> today on first start
    "series_title": "Сериал",       # название сериала (в PDF для клиента)
    "github_token": "",              # for «Правка»; not needed when GitHub CLI (gh) is logged in
}

# Порядок важен: по нему проверяется «переход через согласование ТЗ» (main.set_status).
# Клиент согласует дважды: синопсисы арки (synopsis_review → synopsis_ok) и готовый ролик (client_review → ready).
STATUSES = [
    ("synopsis", "Синопсис"),
    ("synopsis_review", "Синопсис у клиента"),
    ("synopsis_ok", "Синопсис согласован"),
    ("dev", "Пишется ТЗ"),
    ("review", "ТЗ на проверке"),
    ("approved", "ТЗ готово"),
    ("generating", "Генерация"),
    ("fixes", "Монтаж"),
    ("client_review", "Ролик у клиента"),
    ("ready", "Готов к постингу"),
    ("posted", "Опубликовано"),
]
# Статус арки = этап согласования синопсисов с клиентом; переносится на серии, которые ещё на этапе синопсиса.
ARC_STATUSES = [("writing", "Синопсисы пишутся"), ("client", "Синопсисы у клиента"), ("approved", "Синопсисы согласованы")]
ARC_TO_EPISODE = {"writing": "synopsis", "client": "synopsis_review", "approved": "synopsis_ok"}
SYNOPSIS_STAGE = {"synopsis", "synopsis_review", "synopsis_ok"}
STATUS_NAMES = dict(STATUSES)
STATUS_ORDER = [k for k, _ in STATUSES]


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _columns(spec: str) -> list[tuple[str, str]]:
    cols = []
    for line in spec.strip().splitlines():
        line = line.strip().rstrip(",")
        m = re.match(r"^(\w+)\s+(.*)$", line)
        if m and m.group(1).upper() not in ("PRIMARY", "UNIQUE", "FOREIGN"):
            cols.append((m.group(1), m.group(2)))
    return cols


def init():
    DATA_DIR.mkdir(exist_ok=True)
    MEDIA_DIR.mkdir(exist_ok=True)
    with connect() as c:
        # v1 had episodes.number NOT NULL; v2 needs it nullable (backlog) -> rebuild keeping data
        info = {r["name"]: r for r in c.execute("PRAGMA table_info(episodes)")}
        if "number" in info and info["number"]["notnull"]:
            c.execute("PRAGMA foreign_keys = OFF")
            c.execute(f"CREATE TABLE episodes_v2 ({TABLES['episodes']})")
            common = [k for k in info if k in dict(_columns(TABLES["episodes"]))]
            cols = ",".join(common)
            c.execute(f"INSERT INTO episodes_v2 ({cols}) SELECT {cols} FROM episodes")
            c.execute("DROP TABLE episodes")
            c.execute("ALTER TABLE episodes_v2 RENAME TO episodes")
            c.execute("PRAGMA foreign_keys = ON")
        for table, spec in TABLES.items():
            c.execute(f"CREATE TABLE IF NOT EXISTS {table} ({spec})")
            existing = {r["name"] for r in c.execute(f"PRAGMA table_info({table})")}
            for col, decl in _columns(spec):
                if col not in existing:
                    decl = re.sub(r"\s*REFERENCES.*$", "", decl).replace("NOT NULL", "")
                    c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
        c.execute("UPDATE episodes SET status='dev' WHERE status NOT IN (%s)" % ",".join("?" * len(STATUSES)),
                  [k for k, _ in STATUSES])
        for k, v in DEFAULT_SETTINGS.items():
            c.execute("INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)", (k, v))
        c.execute("UPDATE settings SET value=? WHERE key='anchor_date' AND value=''",
                  (datetime.now().date().isoformat(),))
        _migrate_arcs_v2(c)
        _seed_templates(c)


# Шаблоны технических требований: примеры при первом запуске (в промпт не подставляются, пока не включат)
TEMPLATE_KINDS = {"video": "Видео", "sound": "Звук", "edit": "Монтаж", "other": "Другое"}
_TEMPLATE_EXAMPLES = [
    ("Видео: Veo 3.1", "video", "Veo 3.1 (Google)",
     "Формат 9:16, 1080×1920\nШот до 8 с, 24 fps\nОдин непрерывный план на шот",
     "Realistic smartphone-style vertical footage, natural light, no on-screen text, no logos except approved branding.",
     "Референсы персонажей и локаций — только у Veo 3.1."),
    ("Звук", "sound", "Veo 3.1 (звук в генерации)",
     "Реплики — на русском, чисто, без музыки\nМузыка и закадровый голос — на монтаже\nГромкость финала: −14 LUFS",
     "Clean dialogue audio, no background music.",
     "Закадровую озвучку записываем отдельно."),
    ("Монтаж и выкладка", "edit", "",
     "1080×1920, H.264, 30 fps\nХронометраж 30–60 с\nСубтитры: белые, внизу, без обводки",
     "", "Обложка — первый кадр с героиней, без текста."),
]


def _seed_templates(c):
    if c.execute("SELECT 1 FROM settings WHERE key='templates_seeded'").fetchone():
        return
    if not c.execute("SELECT 1 FROM templates").fetchone():
        for pos, (name, kind, model, specs, prompt, notes) in enumerate(_TEMPLATE_EXAMPLES):
            c.execute("INSERT INTO templates(name, kind, model, specs, prompt, notes, in_prompt, position, created_at) "
                      "VALUES (?,?,?,?,?,?,0,?,?)", (name, kind, model, specs, prompt, notes, pos, now()))
    c.execute("INSERT INTO settings(key, value) VALUES ('templates_seeded', '1')")


def _migrate_arcs_v2(c):
    """v1: арка = «с какого дня начинается» (arcs.start_number). v2: серия принадлежит арке (episodes.arc_id).
    Один раз раскладываем существующие серии по аркам по старому правилу."""
    if c.execute("SELECT 1 FROM settings WHERE key='arcs_v2'").fetchone():
        return
    arcs = [dict(r) for r in c.execute("SELECT id, start_number FROM arcs WHERE start_number > 0 ORDER BY start_number")]
    for e in c.execute("SELECT id, number FROM episodes WHERE arc_id IS NULL AND number IS NOT NULL").fetchall():
        owner = None
        for a in arcs:
            if a["start_number"] <= e["number"]:
                owner = a["id"]
        if owner:
            c.execute("UPDATE episodes SET arc_id=? WHERE id=?", (owner, e["id"]))
    # Старые арки уже в производстве: их синопсисы считаем согласованными
    c.execute("UPDATE arcs SET status='approved' WHERE id IN (SELECT DISTINCT arc_id FROM episodes WHERE arc_id IS NOT NULL)")
    c.execute("INSERT INTO settings(key, value) VALUES ('arcs_v2', '1')")


@contextmanager
def connect():
    with _lock:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()


def row(r):
    if r is None:
        return None
    d = dict(r)
    for k in JSON_FIELDS & d.keys():
        d[k] = json.loads(d[k] or "[]")
    return d


def rows(rs):
    return [row(r) for r in rs]


def dumps(v) -> str:
    return json.dumps(v, ensure_ascii=False)


def get_settings() -> dict:
    """Настройки + `tt_prompt`: текст шаблонов ТТ с галочкой «Подставлять в промпт» (идёт в промпт каждого шота)."""
    with connect() as c:
        s = {r["key"]: r["value"] for r in c.execute("SELECT key, value FROM settings")}
        s["tt_prompt"] = " ".join(r[0].strip() for r in c.execute(
            "SELECT prompt FROM templates WHERE in_prompt=1 AND prompt<>'' ORDER BY position, id"))
        return s


def log(episode_id, user_id, text):
    with connect() as c:
        c.execute("INSERT INTO events(episode_id, user_id, text, created_at) VALUES (?,?,?,?)",
                  (episode_id, user_id, text, now()))
