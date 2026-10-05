"""Turns an episode script into Veo-sized shots, estimates timing and builds per-shot prompts.

Two modes:
  * Marked: the script contains "Шот 1", "ШОТ 2:", "Shot 3 —" ... Each marked block is exactly one shot.
    Nothing is split automatically; a shot that does not fit Veo's limit gets a warning with the numbers.
  * Auto (no markers): every action paragraph starts a shot, long lines are split to fit 8 s.

Recognised inside the text (all optional):
  ИНТ. КАФЕ — ДЕНЬ / НАТ. ... / СЦЕНА 2: Парк / ЛОКАЦИЯ: Кафе   -> scene + location
  МАША: Привет!  /  Маша (шёпотом): Привет!  /  @Маша:Пижама: Привет!
  МАША  (caps line, dialogue on next lines)  /  — Привет, — сказала Маша.
  КАМЕРА: крупный план  /  [медленный наезд]                       -> camera
  @Маша, @Маша:Пижама, @Кафе, @Кафе:Ночь                           -> character / location (+ version)
"""
import re

ALLOWED_DURATIONS = (4, 6, 8)
MAX_REFERENCE_IMAGES = 3
MAX_DIALOGUE_LINES_PER_SHOT = 2

SHOT_MARK_RE = re.compile(
    r"^[#*_\s]*(?:шот|shot|кадр)\s*№?\s*(\d+[a-zа-я]?)\b[*_\s]*[.:)\-–—]?[*_\s]*(.*)$", re.I)
SCENE_RE = re.compile(
    r"^(?:(?:ИНТ|НАТ|INT|EXT)\.?(?:\s*/\s*(?:ИНТ|НАТ|INT|EXT)\.?)?\s+|(?:СЦЕНА|SCENE)\s*\d*\s*[.:\-–—]?\s*|(?:ЛОКАЦИЯ|LOCATION)\s*[:\-–—]\s*)(.*)$",
    re.I)
CAMERA_RE = re.compile(r"^(?:КАМЕРА|CAMERA|РАКУРС|ПЛАН)\s*[:\-–—]\s*(.+)$", re.I)
BRACKET_RE = re.compile(r"^\[(.+)\]$")
BREAK_RE = re.compile(r"^(?:-{3,}|\*{3,})$")
VERSIONED_DIALOGUE_RE = re.compile(r"^@([\wЁё\-]+):([\wЁё\-]+)\s*(?:\(([^)]{1,80})\))?\s*:\s+(.+)$")
INLINE_DIALOGUE_RE = re.compile(r"^@?([^\s:()\[\]—–][^:()\[\]]{0,40}?)\s*(?:\(([^)]{1,80})\))?\s*:\s+(.+)$")
CAPS_SPEAKER_RE = re.compile(r"^@?([A-ZА-ЯЁ][A-ZА-ЯЁ0-9 .\-]{0,30})\s*(?:\(([^)]{1,80})\))?$")
PAREN_RE = re.compile(r"^\(([^)]{1,80})\)$")
MENTION_RE = re.compile(r"@([\wЁё\-]+)(?::([\wЁё\-]+))?")
TITLE_RE = re.compile(r"^(?:серия|эпизод|название|episode)\b", re.I)
SENTENCE_SPLIT_RE =re.compile(r"(?<=[.!?…])\s+")


def _stem(word: str) -> str:
    w = word.lower()
    return w[:-1] if len(w) >= 4 and w[-1] in "аяоеиыуюьй" else w


def _norm(name: str) -> str:
    return name.replace("_", " ").strip().lower()


class AssetIndex:
    """Lookup of characters/locations by name, alias, declined forms and @mentions."""

    def __init__(self, assets: list[dict], cast: dict | None = None):
        self.assets = assets
        self.cast = cast or {}
        self.by_id = {a["id"]: a for a in assets}
        self.patterns = []
        for a in assets:
            for name in [a["name"], *a.get("aliases", [])]:
                name = name.strip()
                if not name:
                    continue
                stems = r"\s+".join(re.escape(_stem(t)) + r"[а-яёa-z]{0,4}" for t in name.split())
                self.patterns.append((re.compile(r"(?<![\wЁё@])" + stems + r"(?![\wЁё])", re.I), a))

    def exact(self, name: str, kind: str | None = None):
        n = _norm(name)
        for a in self.assets:
            if (not kind or a["kind"] == kind) and (n == a["name"].lower() or n in [x.lower() for x in a.get("aliases", [])]):
                return a
        for rx, a in self.patterns:
            if (not kind or a["kind"] == kind) and rx.fullmatch(name.replace("_", " ").strip()):
                return a
        return None

    def find_all(self, text: str, kind: str | None = None) -> list[dict]:
        found = []
        for rx, a in self.patterns:
            if kind and a["kind"] != kind:
                continue
            m = rx.search(text)
            if m and a not in [f for _, f in found]:
                found.append((m.start(), a))
        for m in MENTION_RE.finditer(text):
            a = self.exact(m.group(1), kind)
            if a and a not in [f for _, f in found]:
                found.append((m.start(), a))
        return [a for _, a in sorted(found, key=lambda x: x[0])]

    def version(self, asset: dict, label: str | None):
        """explicit @label > version pinned for this episode > active version."""
        if label:
            for v in asset["versions"]:
                if _norm(v["label"]) == _norm(label) or str(v["version_no"]) == label.lstrip("vв"):
                    return v
        pinned = self.cast.get(asset["id"])
        for vid in (pinned, asset["active_version_id"]):
            v = next((v for v in asset["versions"] if v["id"] == vid), None)
            if v:
                return v
        return asset["versions"][-1] if asset["versions"] else None


def words(text: str) -> int:
    return len(re.findall(r"[\wЁё']+", text))


def speech_seconds(text: str, wps: float) -> float:
    return words(text) / wps + 0.4


def _action_seconds(text: str) -> float:
    return min(8.0, max(2.5, words(text) * 0.3))


def fit_duration(seconds: float, max_s: int) -> int:
    for d in ALLOWED_DURATIONS:
        if d <= max_s and seconds <= d:
            return d
    return max(d for d in ALLOWED_DURATIONS if d <= max_s)


def estimate(action: str, dialogue: list[dict], wps: float) -> tuple[float, float]:
    """(estimated shot seconds, speech seconds)."""
    speech = sum(speech_seconds(d["text"], wps) for d in dialogue)
    act = _action_seconds(action) if action.strip() else 0.0
    est = max(act, speech + 0.6) if speech else act
    return round(est, 1), round(speech, 1)


def _split_to_fit(text: str, max_seconds: float, cost) -> list[str]:
    text = text.strip()
    if cost(text) <= max_seconds:
        return [text]
    ws = text.split()
    n = 2
    while n <= len(ws):
        target = len(ws) / n
        chunks, part = [], []
        for i, w in enumerate(ws):
            part.append(w)
            if len(chunks) == n - 1 or i == len(ws) - 1:
                continue
            if (w[-1] in ".!?…" and len(part) >= target * 0.6) or (w[-1] in ",;:—" and len(part) >= target * 0.85) \
                    or len(part) >= target * 1.2:
                chunks.append(" ".join(part))
                part = []
        if part:
            chunks.append(" ".join(part))
        if all(cost(c) <= max_seconds for c in chunks):
            return chunks
        n += 1
    return ws


def has_markers(text: str) -> bool:
    return any(SHOT_MARK_RE.match(l.strip()) for l in text.splitlines())


def parse_script(text: str, assets: list[dict], settings: dict, cast: dict | None = None) -> tuple[list[dict], list[str]]:
    """Returns (shots, episode-level notes)."""
    idx = AssetIndex(assets, cast)
    wps = float(settings.get("words_per_second") or 2.5)
    max_s = int(settings.get("max_shot_seconds") or 8)
    speech = lambda t: speech_seconds(t, wps)  # noqa: E731
    marked = has_markers(text)
    notes: list[str] = []
    outside: list[str] = []

    shots: list[dict] = []
    st = {"scene": "", "location": None, "loc_label": None, "camera": None, "shot": None,
          "speaker": None, "paren": None, "in_para": False, "scene_shots": 0}

    def new_shot(label=None):
        s = {"label": label or "", "scene": st["scene"], "location": st["location"], "loc_label": st["loc_label"],
             "camera": st["camera"] or "", "action": "", "dialogue": [], "mentions": {},
             "first_in_scene": st["scene_shots"] == 0}
        st["camera"] = None
        st["shot"] = s
        st["scene_shots"] += 1
        shots.append(s)
        return s

    def mentions(shot, text):
        for m in MENTION_RE.finditer(text):
            a = idx.exact(m.group(1))
            if not a:
                continue
            if a["kind"] == "location":
                shot["location"], shot["loc_label"] = a, m.group(2)
            else:
                shot["mentions"][a["id"]] = m.group(2) or shot["mentions"].get(a["id"])

    def current_or_outside(text) -> dict | None:
        if st["shot"] is None:
            if marked:
                if not TITLE_RE.match(text):
                    outside.append(text)
                return None
            return new_shot()
        return st["shot"]

    def add_action(text):
        if marked:
            cur = current_or_outside(text)
            if cur is not None:
                cur["action"] = f"{cur['action']} {text}".strip()
                mentions(cur, text)
            return
        cur = st["shot"]
        joined = f"{cur['action']} {text}".strip() if cur else text
        if st["in_para"] and cur and not cur["dialogue"] and words(joined) <= 26:
            cur["action"] = joined
            mentions(cur, text)
            return
        for chunk in _split_to_fit(text, 26 * 0.3, _action_seconds):
            s = new_shot()
            s["action"] = chunk
            mentions(s, chunk)
        st["in_para"] = True

    def add_dialogue(speaker_name, text, paren=None, label=None):
        st["in_para"] = False
        speaker = idx.exact(speaker_name, "character") if speaker_name else None
        name = speaker["name"] if speaker else (speaker_name or "").strip().title()

        def line(chunk):
            return {"speaker": name, "asset_id": speaker["id"] if speaker else None, "text": chunk,
                    "parenthetical": paren or ""}

        if marked:
            cur = current_or_outside(text)
            if cur is None:
                return
            cur["dialogue"].append(line(text))
            if speaker:
                cur["mentions"].setdefault(speaker["id"], label)
                if label:
                    cur["mentions"][speaker["id"]] = label
            mentions(cur, text)
            return
        chunks = _split_to_fit(text, max_s - 0.6, speech)
        for chunk in chunks:
            cur = st["shot"]
            used = sum(speech(d["text"]) for d in cur["dialogue"]) if cur else 0
            fits = (len(chunks) == 1 and cur is not None and len(cur["dialogue"]) < MAX_DIALOGUE_LINES_PER_SHOT
                    and max(_action_seconds(cur["action"]) if cur["action"] else 0, used + speech(chunk) + 0.6) <= max_s)
            if not fits:
                cur = new_shot()
            cur["dialogue"].append(line(chunk))
            if speaker:
                cur["mentions"].setdefault(speaker["id"], label)
            mentions(cur, chunk)

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            st["speaker"] = st["paren"] = None
            st["in_para"] = False
            continue

        m = SHOT_MARK_RE.match(line)
        if m:
            st["speaker"] = None
            st["in_para"] = False
            new_shot(m.group(1))
            rest = m.group(2).strip(" *_")
            if rest:
                if not _handle_inline(rest, idx, add_dialogue):
                    add_action(rest)
            continue

        if st["speaker"]:
            pm = PAREN_RE.match(line)
            if pm:
                st["paren"] = pm.group(1)
                continue
            add_dialogue(st["speaker"], line, st["paren"])
            st["paren"] = None
            continue

        if BREAK_RE.match(line):
            if not marked:
                st["shot"] = None
            st["in_para"] = False
            continue

        m = SCENE_RE.match(line)
        if m and not line[:1].islower():
            raw_heading = m.group(1).strip(" .—–-")
            heading = MENTION_RE.sub(lambda x: x.group(1).replace("_", " "), raw_heading)
            loc_part = re.split(r"\s+[—–-]\s+", heading)[0]
            st["scene"] = heading
            mm = MENTION_RE.search(raw_heading)
            loc = idx.exact(mm.group(1), "location") if mm else None
            st["loc_label"] = mm.group(2) if (mm and loc) else None
            st["location"] = loc or idx.exact(loc_part, "location") or next(iter(idx.find_all(heading, "location")), None)
            st["scene_shots"] = 0
            st["in_para"] = False
            if marked and st["shot"] is not None and not st["shot"]["action"] and not st["shot"]["dialogue"]:
                st["shot"].update(scene=heading, location=st["location"], loc_label=st["loc_label"])
            elif not marked:
                st["shot"] = None
            continue

        m = CAMERA_RE.match(line) or BRACKET_RE.match(line)
        if m:
            cam = m.group(1).strip()
            cur = st["shot"]
            if cur is not None and (marked or (not cur["action"] and not cur["dialogue"])):
                cur["camera"] = f"{cur['camera']}, {cam}".strip(", ") if marked and cur["camera"] else cam
            else:
                st["camera"] = cam
                if not marked:
                    st["shot"] = None
            st["in_para"] = False
            continue

        if _handle_inline(line, idx, add_dialogue):
            continue

        m = CAPS_SPEAKER_RE.match(line)
        if m and len(m.group(1).split()) <= 4 and (idx.exact(m.group(1).title(), "character") or line.isupper()):
            st["speaker"] = m.group(1).strip()
            st["paren"] = m.group(2)
            continue

        if line[0] in "—–":
            body = line[1:].strip()
            parts = re.split(r"\s*,?\s*[—–]\s+", body)
            spoken = [parts[0]] + parts[2::2]
            author = " ".join(parts[1::2])
            chars = idx.find_all(author, "character") if author else []
            add_dialogue(chars[0]["name"] if chars else "", " ".join(p.strip(" ,") for p in spoken if p.strip()))
            if author and st["shot"] is not None:
                for a in chars:
                    st["shot"]["mentions"].setdefault(a["id"], None)
            continue

        add_action(line)

    if marked and outside:
        notes.append("Текст до первой пометки «Шот 1» не попал в разбивку: "
                     + " / ".join(outside)[:200])
    if marked:
        labels = [s["label"] for s in shots]
        dups = sorted({l for l in labels if labels.count(l) > 1})
        if dups:
            notes.append("Повторяются номера шотов: " + ", ".join(dups))
    result = [_finalize(s, i, idx, max_s, wps, marked) for i, s in enumerate(shots)]
    if marked:
        empty = [r["label"] for r in result if not r["action"] and not r["dialogue"]]
        if empty:
            notes.append("Пустые шоты: " + ", ".join(empty))
    else:
        result = [r for r in result if r["action"] or r["dialogue"]]
        for i, r in enumerate(result):
            r["idx"] = i + 1
            r["label"] = str(i + 1)
    return result, notes


def _handle_inline(line: str, idx: AssetIndex, add_dialogue) -> bool:
    m = VERSIONED_DIALOGUE_RE.match(line)
    if m and idx.exact(m.group(1), "character"):
        add_dialogue(m.group(1), m.group(4).strip(), m.group(3), label=m.group(2))
        return True
    m = INLINE_DIALOGUE_RE.match(line)
    if m:
        spk = m.group(1).strip().lstrip("@")
        if idx.exact(spk, "character") or (spk.isupper() and len(spk.split()) <= 3):
            add_dialogue(spk, m.group(3).strip(), m.group(2))
            return True
    return False


def shot_warnings(shot: dict, max_s: int) -> list[str]:
    w = []
    if shot["est_seconds"] > max_s:
        w.append(f"Не влезает в Veo: ~{shot['est_seconds']:.1f} с при лимите {max_s} с "
                 f"(речь {shot['speech_seconds']:.1f} с, {shot['words']} слов). Сократите реплики или разбейте шот.")
    elif shot["est_seconds"] > max_s - 0.8:
        w.append(f"Впритык: ~{shot['est_seconds']:.1f} с из {max_s} с — актёр может не успеть договорить")
    if len(shot["dialogue"]) > MAX_DIALOGUE_LINES_PER_SHOT:
        w.append(f"{len(shot['dialogue'])} реплики в одном шоте — Veo лучше справляется с 1–2")
    if len(shot["characters"]) > 3:
        w.append("Больше 3 персонажей в кадре — Veo может путать внешность")
    for d in shot["dialogue"]:
        if not d.get("asset_id"):
            w.append(f"Персонаж «{d['speaker'] or '?'}» не найден в библиотеке")
    return w


def _finalize(s: dict, i: int, idx: AssetIndex, max_s: int, wps: float, marked: bool) -> dict:
    order = []
    for d in s["dialogue"]:
        if d["asset_id"] and d["asset_id"] not in order:
            order.append(d["asset_id"])
    for a in idx.find_all(s["action"], "character"):
        if a["id"] not in order:
            order.append(a["id"])
    for aid in s["mentions"]:
        if aid not in order:
            order.append(aid)
    chars = []
    for aid in order:
        a = idx.by_id[aid]
        v = idx.version(a, s["mentions"].get(aid))
        chars.append({"asset_id": aid, "name": a["name"], "version_id": v["id"] if v else None})

    loc = s["location"]
    loc_v = idx.version(loc, s.get("loc_label")) if loc else None

    camera = s["camera"]
    if not camera:
        speakers = list(dict.fromkeys(d["speaker"] for d in s["dialogue"]))
        if len(speakers) == 1:
            camera = f"Medium close-up on {speakers[0]}, eye level, slight handheld"
        elif len(speakers) >= 2:
            camera = "Two-shot, medium, over-the-shoulder"
        elif s["first_in_scene"]:
            camera = "Establishing wide shot, slow push-in"
        else:
            camera = "Medium shot, steady"

    est, sp = estimate(s["action"], s["dialogue"], wps)
    shot = {
        "idx": i + 1,
        "label": s["label"] or str(i + 1),
        "scene": s["scene"],
        "duration": fit_duration(est, max_s),
        "est_seconds": est,
        "speech_seconds": sp,
        "words": sum(words(d["text"]) for d in s["dialogue"]),
        "camera": camera,
        "action": s["action"],
        "dialogue": s["dialogue"],
        "characters": chars,
        "location_version_id": loc_v["id"] if loc_v else None,
    }
    shot["warnings"] = shot_warnings(shot, max_s)
    if not loc and s["scene"]:
        shot["warnings"].append(f"Локация «{s['scene']}» не найдена в библиотеке")
    return shot


def _sent(text: str) -> str:
    text = text.strip()
    return text if text.endswith((".", "!", "?", "…")) else text + "."


def build_prompt(shot: dict, versions: dict, assets: dict, settings: dict) -> tuple[str, list[dict], list[str]]:
    """Returns (prompt, references, warnings). references: [{"path", "kind": composition|character|location}]."""
    lang = settings.get("dialogue_language") or "Russian"
    model = settings.get("veo_model", "")
    parts = []
    if settings.get("style"):
        parts.append(_sent(settings["style"]))
    parts.append(f"Vertical {settings.get('aspect_ratio', '9:16')} video, {shot['duration']} seconds, one continuous shot.")

    comp = shot.get("composition_image")
    mode = shot.get("composition_mode") or "reference"
    if comp:
        if mode == "first_frame":
            parts.append("The video starts exactly from the provided first-frame image; keep its composition, framing and camera angle.")
        else:
            parts.append("Composition: match the framing, camera angle, blocking and layout of the composition reference image.")
    if shot.get("composition_note"):
        parts.append(_sent("Composition notes: " + shot["composition_note"]))

    loc_v = versions.get(shot.get("location_version_id"))
    if loc_v:
        desc = loc_v["description"].strip()
        parts.append(_sent(f"Location: {assets[loc_v['asset_id']]['name']}" + (f" — {desc}" if desc else "")))
    elif shot.get("scene"):
        parts.append(_sent(f"Location: {shot['scene']}"))

    char_vs = []
    for c in shot.get("characters", []):
        v = versions.get(c.get("version_id"))
        char_vs.append((c, v))
        desc = v["description"].strip() if v else ""
        outfit = f" (look: {v['label']})" if v and v["label"] else ""
        parts.append(_sent(f"Character {c['name']}{outfit}" + (f": {desc}" if desc else "")))

    if shot.get("action"):
        parts.append(_sent("Action: " + MENTION_RE.sub(lambda m: m.group(1).replace("_", " "), shot["action"])))
    if shot.get("camera"):
        parts.append(_sent(f"Camera: {shot['camera']}"))

    if shot.get("dialogue"):
        for d in shot["dialogue"]:
            v = next((v for c, v in char_vs if c["asset_id"] == d.get("asset_id")), None)
            how = f", {d['parenthetical']}" if d.get("parenthetical") else ""
            voice = " " + _sent(f"Voice: {v['voice']}") if v and v.get("voice") else ""
            parts.append(f'{d["speaker"]} says in {lang}{how}: "{d["text"].strip()}"{voice}')
        parts.append("Lip-sync the spoken lines. No background music.")
    else:
        parts.append("No dialogue, natural ambient sound only.")

    refs, warns = [], []
    if comp:
        refs.append({"path": comp, "kind": "composition"})
    for c, v in char_vs:
        if v and v["images"]:
            refs.append({"path": v["images"][0], "kind": "character", "name": c["name"]})
        elif v:
            warns.append(f"У персонажа «{c['name']}» нет фото — Veo нарисует его по описанию")
    if loc_v and loc_v["images"]:
        refs.append({"path": loc_v["images"][0], "kind": "location", "name": assets[loc_v["asset_id"]]["name"]})

    if comp and mode == "first_frame":
        if len(refs) > 1:
            warns.append("В режиме «первый кадр» фото персонажей и локации не отправляются — Veo берёт всё из кадра")
        refs = refs[:1]
    elif refs and "3.1" not in model:
        warns.append("Референс-изображения поддерживает только Veo 3.1 — сейчас они не отправятся")
    if len(refs) > MAX_REFERENCE_IMAGES:
        dropped = ", ".join(r.get("name") or "композиция" for r in refs[MAX_REFERENCE_IMAGES:])
        warns.append(f"Veo принимает до {MAX_REFERENCE_IMAGES} референсов — не отправятся: {dropped}")
        refs = refs[:MAX_REFERENCE_IMAGES]
    return " ".join(parts), refs, warns


def forced_duration(refs: list[dict], shot: dict, settings: dict) -> int | None:
    """Veo 3.1 only renders 8 s clips when reference images are attached."""
    if refs and (shot.get("composition_mode") != "first_frame" or not shot.get("composition_image")) \
            and "3.1" in settings.get("veo_model", ""):
        return 8
    return None
