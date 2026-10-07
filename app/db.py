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
        created_at TEXT NOT NULL DEFAULT ''""",
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
        status TEXT NOT NULL DEFAULT 'dev',
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
        created_by INTEGER""",
    "arcs": """
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        start_number INTEGER NOT NULL,
        notes TEXT NOT NULL DEFAULT '',
        members TEXT NOT NULL DEFAULT '[]',
        created_by INTEGER,
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
    "github_token": "",              # for «Правка»; not needed when GitHub CLI (gh) is logged in
}

STATUSES = [
    ("dev", "В разработке"),
    ("review", "Сценарий на согласовании"),
    ("approved", "Сценарий согласован"),
    ("generating", "В процессе генерации"),
    ("fixes", "На правках визуала"),
    ("ready", "Готов к постингу"),
    ("posted", "Опубликовано"),
]
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
    with connect() as c:
        return {r["key"]: r["value"] for r in c.execute("SELECT key, value FROM settings")}


def log(episode_id, user_id, text):
    with connect() as c:
        c.execute("INSERT INTO events(episode_id, user_id, text, created_at) VALUES (?,?,?,?)",
                  (episode_id, user_id, text, now()))
