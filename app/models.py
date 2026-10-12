"""Нейросети для видео через Magnific API (бывший Freepik API) и правила, по которым для каждой собирается запрос.

У каждой модели свой адрес, свои названия полей и свой способ сослаться в промпте на картинку-референс:
  * `@Image1`, `@Image2` … : Kling 3 Omni, Seedance 2.0, Hailuo 3 (номер = порядок картинки в запросе);
  * `Image 1`, `Image 2` … : WAN 2.7 (каждая картинка = один персонаж);
  * «reference image 1» обычными словами: Veo 3.1 (специального синтаксиса нет);
  * без референсов (только первый кадр): Runway Gen-4.5.
Данные сверены с docs.magnific.com в октябре 2026. Если модель обновится, поправьте её описание здесь.

Как картинки уходят в Magnific: где API принимает base64, картинка вкладывается прямо в запрос (работает и с ноутбука).
Где принимается только ссылка (Kling 3 Omni, Veo 3.1 по референсам, WAN 2.7), нужен «Публичный адрес студии» в настройках:
тогда картинка отдаётся по подписанной ссылке /pub/<подпись>/<файл>, которую нельзя подобрать к другим файлам.
"""
import base64
import hashlib
import hmac
import json
import secrets
import time
import urllib.error
import urllib.request

from . import db

API_BASE = "https://api.magnific.com"

# key: ключ в базе (shots.engine). Старые ключи veo / kling / seedance / runway / other сохранены, чтобы шоты не потерялись.
#   ref_token: как ссылаться на картинку в промпте (at | label | natural | None)
#   max_refs: сколько картинок-референсов принимает; ref_kinds: какие картинки годятся в референсы
#   first_frame: умеет ли начинать видео с картинки; audio: делает ли звук сам
#   url_only: картинки принимаются только ссылкой (иначе можно base64)
MODELS = {
    "veo": {
        "name": "Veo 3.1", "durations": (4, 6, 8), "ref_token": "natural", "max_refs": 3,
        "ref_kinds": ("composition", "character", "location"), "first_frame": True, "audio": True,
        "url_only": True, "prompt_max": 20000,
        "duration_note": "Veo 3.1: 4, 6 или 8 с. С референсами персонажей и локаций только 8 с.",
        "refs_note": "До 3 картинок. В промпте они называются словами: «reference image 1, 2, 3».",
        "tips": ["Пишите по-английски, как сцену фильма: кто, где, что делает, как движется камера.",
                 "Реплики в кавычках: Veo сам озвучит их и синхронизирует губы.",
                 "С референсами клип всегда 8 с. Для 4 или 6 с уберите фото или дайте кадр как «первый кадр»."],
    },
    "kling": {
        "name": "Kling 3 Omni", "durations": tuple(range(3, 16)), "ref_token": "at", "max_refs": 4,
        "ref_kinds": ("composition", "character", "location"), "first_frame": True, "audio": True,
        "url_only": True, "prompt_max": 2500,
        "duration_note": "Kling 3 Omni: от 3 до 15 с.",
        "refs_note": "До 4 картинок. В промпте на них ссылаются как @Image1, @Image2 … по порядку.",
        "tips": ["Промпт не длиннее 2500 знаков: длинный стиль и правила лучше сократить.",
                 "Называйте героя через @Image1 там, где он что-то делает: «@Image1 opens the door».",
                 "Картинки только JPG или PNG, от 300×300 и до 10 МБ."],
    },
    "seedance": {
        "name": "Seedance 2.0", "durations": tuple(range(4, 16)), "ref_token": "at", "max_refs": 9,
        "ref_kinds": ("composition", "character", "location"), "first_frame": True, "audio": True,
        "url_only": False, "prompt_max": 40000,
        "duration_note": "Seedance 2.0: от 4 до 15 с.",
        "refs_note": "До 9 картинок. В промпте @Image1 … @Image9 по порядку. Первый кадр и референсы вместе нельзя.",
        "tips": ["Ссылайтесь на каждого героя и локацию через @ImageN: модель держит их внешность по картинке.",
                 "Опишите действие по шагам и движение камеры, звук и шумы модель делает сама.",
                 "Картинки можно отправлять прямо из студии, ссылки не нужны."],
    },
    "hailuo": {
        "name": "Hailuo 3", "durations": tuple(range(4, 16)), "ref_token": "at", "max_refs": 9,
        "ref_kinds": ("composition", "character", "location"), "first_frame": True, "audio": False,
        "url_only": False, "prompt_max": 7000,
        "duration_note": "MiniMax Hailuo 3: от 4 до 15 с.",
        "refs_note": "До 9 картинок. В промпте @Image1 … @Image9 по порядку. Первый кадр и референсы вместе нельзя.",
        "tips": ["Ссылайтесь на героев и локацию через @ImageN, например «@Image1 walks past @Image2».",
                 "Звук Hailuo не делает: реплики озвучиваются в ElevenLabs."],
    },
    "wan": {
        "name": "WAN 2.7", "durations": tuple(range(2, 11)), "ref_token": "label", "max_refs": 5,
        "ref_kinds": ("character",), "first_frame": False, "audio": False,
        "url_only": True, "prompt_max": 5000,
        "duration_note": "WAN 2.7 по референсам: от 2 до 10 с.",
        "refs_note": "До 5 картинок, каждая = один персонаж. В промпте «Image 1», «Image 2». Локация описывается словами.",
        "tips": ["Каждая картинка должна изображать одного персонажа, локация идёт только текстом.",
                 "Пишите «Image 1 walks left while Image 2 sits down».",
                 "Нужен хотя бы один персонаж с фото. Звук WAN не делает: реплики озвучиваются в ElevenLabs."],
    },
    "runway": {
        "name": "Runway Gen-4.5", "durations": (5, 8, 10), "ref_token": None, "max_refs": 0,
        "ref_kinds": (), "first_frame": True, "audio": False,
        "url_only": False, "prompt_max": 2000,
        "duration_note": "Runway Gen-4.5: 5, 8 или 10 с.",
        "refs_note": "Референсы персонажей не принимает. Чтобы сохранить героев, дайте готовый кадр как «первый кадр видео».",
        "tips": ["Промпт не длиннее 2000 знаков.",
                 "Для узнаваемых героев загрузите кадр-композицию и выберите «как первый кадр видео».",
                 "Звук Runway не делает: реплики озвучиваются в ElevenLabs."],
    },
    "other": {
        "name": "Другая (вручную)", "durations": tuple(range(3, 16)), "ref_token": "natural", "max_refs": 9,
        "ref_kinds": ("composition", "character", "location"), "first_frame": True, "audio": False,
        "url_only": False, "prompt_max": 0, "manual": True,
        "duration_note": "Другая нейросеть: выберите длину по её документации.",
        "refs_note": "Через API не уходит: скопируйте промпт, приложите картинки в этом порядке и загрузите готовое видео.",
        "tips": [],
    },
}
ENGINES = [(k, m["name"]) for k, m in MODELS.items()]
RETIRED = {"sora": "other"}  # Sora закрыта OpenAI (24.09.2026), в Magnific её нет


def spec(engine: str | None) -> dict:
    return MODELS.get(engine or "veo", MODELS["other"])


def has_api(engine: str | None) -> bool:
    return not spec(engine).get("manual")


def token(engine: str | None, n: int) -> str:
    style = spec(engine)["ref_token"]
    return {"at": f"@Image{n}", "label": f"Image {n}"}.get(style, f"reference image {n}")


def veo_route(settings: dict) -> str:
    return "google" if settings.get("veo_route") == "google" else "magnific"


def route(engine: str | None, settings: dict) -> str:
    """Куда уйдёт шот: stub (заглушка), magnific, google (Veo через Gemini API) или manual (делается вручную)."""
    if not has_api(engine):
        return "manual"
    if settings.get("gen_mode") != "live":
        return "stub"
    return veo_route(settings) if (engine or "veo") == "veo" else "magnific"


def accepts_refs(engine: str | None, settings: dict) -> bool:
    """Veo через Google принимает референсы только в модели 3.1; в Magnific Veo всегда 3.1."""
    if (engine or "veo") == "veo" and veo_route(settings) == "google":
        return "3.1" in settings.get("veo_model", "")
    return spec(engine)["max_refs"] > 0


# ---------- картинки ----------

def _secret() -> str:
    s = db.get_settings().get("media_secret")
    if not s:
        s = secrets.token_hex(16)
        with db.connect() as c:
            c.execute("INSERT OR REPLACE INTO settings(key, value) VALUES ('media_secret', ?)", (s,))
    return s


def sign(rel: str) -> str:
    return hmac.new(_secret().encode(), rel.encode(), hashlib.sha256).hexdigest()[:32]


def public_url(rel: str, settings: dict) -> str:
    base = (settings.get("public_base_url") or "").strip().rstrip("/")
    return f"{base}/pub/{sign(rel)}/{rel}" if base else ""


class NeedPublicUrl(RuntimeError):
    pass


def image_value(rel: str, settings: dict, engine: str, inline: bool, url_only: bool) -> str:
    """Публичная ссылка, если задан адрес студии; иначе base64 (если модель его принимает)."""
    url = public_url(rel, settings)
    if url:
        return url
    if url_only:
        if inline:
            raise NeedPublicUrl(f"{spec(engine)['name']} принимает картинки только ссылкой. Укажите «Публичный адрес студии» "
                                "в настройках (студия должна быть доступна из интернета) или выберите Seedance 2.0 / Hailuo 3.")
        return f"<ссылка на {rel}: нужен публичный адрес студии>"
    if not inline:
        return f"<base64 {rel}>"
    return base64.b64encode((db.MEDIA_DIR / rel).read_bytes()).decode()


def link_warning(shot: dict, refs: list[dict], settings: dict) -> str:
    """Предупреждение, если картинки нужно отдать ссылкой, а публичного адреса студии нет."""
    engine = shot.get("engine") or "veo"
    m = spec(engine)
    first = bool(shot.get("composition_image")) and shot.get("composition_mode") == "first_frame"
    if not refs or not m["url_only"] or m.get("manual") or (settings.get("public_base_url") or "").strip():
        return ""
    if engine == "veo" and (first or veo_route(settings) == "google"):
        return ""  # первый кадр Veo и Veo через Google принимают картинку файлом
    return (f"{m['name']} принимает картинки только ссылкой: без «Публичного адреса студии» в настройках отправка не пройдёт. "
            "С ноутбука подойдут Seedance 2.0 или Hailuo 3")


def image_warnings(engine: str | None, refs: list[dict]) -> list[str]:
    """Требования моделей к картинкам: JPG/PNG у Kling и Veo по ссылке, до 10 МБ у всех."""
    out = []
    for r in refs:
        p = db.MEDIA_DIR / r["path"]
        who = r.get("name") or "композиция"
        if p.exists() and p.stat().st_size > 10 * 1024 * 1024:
            out.append(f"Картинка «{who}» больше 10 МБ: {spec(engine)['name']} её не примет")
        if engine == "kling" and p.suffix.lower() not in (".jpg", ".jpeg", ".png"):
            out.append(f"Картинка «{who}»: Kling принимает только JPG или PNG")
    return out


# ---------- запрос ----------

def _ratio(settings: dict) -> str:
    return settings.get("aspect_ratio") or "9:16"


SOCIAL = {"9:16": "social_story_9_16", "16:9": "widescreen_16_9", "1:1": "square_1_1", "3:4": "traditional_3_4",
          "4:3": "classic_4_3"}
RUNWAY_RATIO = {"9:16": "720:1280", "16:9": "1280:720", "1:1": "960:960", "3:4": "832:1104", "4:3": "1104:832"}


def build(shot: dict, refs: list[dict], settings: dict, inline: bool) -> dict:
    """Запрос в Magnific: {"path", "status_path", "body"}. inline=False даёт читаемый вариант для просмотра (без base64)."""
    engine = shot.get("engine") or "veo"
    m = spec(engine)
    first = bool(shot.get("composition_image")) and shot.get("composition_mode") == "first_frame" and m["first_frame"]
    img = lambda rel, url_only=m["url_only"]: image_value(rel, settings, engine, inline, url_only)  # noqa: E731
    paths = [r["path"] for r in refs]
    prompt = shot["prompt"]
    neg = shot.get("negative_prompt") or settings.get("negative_prompt") or ""
    dur = int(shot["duration"])
    ratio = _ratio(settings)
    res = settings.get("magnific_resolution") or "720p"
    body: dict = {"prompt": prompt}

    if engine == "veo":
        common = {"negative_prompt": neg, "aspect_ratio": ratio, "resolution": res, "generate_audio": True}
        if first:
            path = "/v1/ai/image-to-video/veo-3-1"
            body.update(image=img(paths[0], False), duration=dur, **common)
        elif paths:
            path = "/v1/ai/reference-to-video/veo-3-1"  # длина фиксирована: 8 с
            body.update(image_urls=[img(p) for p in paths], **common)
        else:
            path = "/v1/ai/text-to-video/veo-3-1"
            body.update(duration=dur, **common)
        status = path
    elif engine == "kling":
        path, status = "/v1/ai/video/kling-v3-omni-pro", "/v1/ai/video/kling-v3-omni"
        body.update(aspect_ratio=ratio, duration=str(dur), generate_audio=True)
        if first:
            body["start_image_url"] = img(paths[0])
        elif paths:
            body["image_urls"] = [img(p) for p in paths]
    elif engine == "seedance":
        res = res if res in ("480p", "720p", "1080p") else "720p"
        path, status = f"/v1/ai/video/seedance-2-pro-{res}", "/v1/ai/video/seedance-2-pro"
        body.update(duration=dur, aspect_ratio=SOCIAL.get(ratio, "social_story_9_16"), sound_effects=True, no_music=True)
        if first:
            body["image"] = img(paths[0])
        elif paths:
            body["reference_images"] = [img(p) for p in paths]
    elif engine == "hailuo":
        path = status = "/v1/ai/video/minimax-h3-768p"
        body.update(duration=dur, aspect_ratio=SOCIAL.get(ratio, "social_story_9_16"))
        if first:
            body["image"] = img(paths[0])
        elif paths:
            body["reference_images"] = [img(p) for p in paths]
    elif engine == "wan":
        path = status = "/v1/ai/reference-to-video/wan-2-7"
        if not paths and inline:
            raise RuntimeError("WAN 2.7 работает по фото персонажей: добавьте в шот персонажа с картинкой или выберите другую модель")
        body.update(image_urls=[img(p) for p in paths], duration=dur, aspect_ratio=ratio,
                    resolution="1080P" if res in ("1080p", "4k") else "720P")
        if neg:
            body["negative_prompt"] = neg[:500]
    elif engine == "runway":
        kind = "image-to-video" if first else "text-to-video"
        path = status = f"/v1/ai/{kind}/runway-4-5"
        body.update(duration=dur, ratio=RUNWAY_RATIO.get(ratio, "720:1280"))
        if first:
            body["image"] = img(paths[0])
    else:
        raise RuntimeError(f"«{m['name']}» не подключена к API: сделайте видео вручную и загрузите файл")
    if m["prompt_max"] and len(prompt) > m["prompt_max"]:
        raise RuntimeError(f"Промпт длиннее {m['prompt_max']} знаков, {m['name']} его не примет: сократите стиль или правила")
    return {"path": path, "status_path": status, "body": body}


# ---------- отправка ----------

def _http(method: str, url: str, key: str | None, body: dict | None = None, raw=False):
    headers = {"Content-Type": "application/json"}
    if key:
        headers["x-magnific-api-key"] = key
    req = urllib.request.Request(url, method=method, headers=headers,
                                 data=json.dumps(body).encode() if body is not None else None)
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            data = r.read()
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Magnific: HTTP {e.code}: {e.read().decode(errors='replace')[:800]}") from None
    return data if raw else json.loads(data)


def generate(shot: dict, refs: list[dict], settings: dict) -> bytes:
    """Создаёт задачу, опрашивает её каждые 10 с (до 30 минут) и сразу скачивает mp4: ссылка живёт 30 минут."""
    key = (settings.get("magnific_api_key") or "").strip()
    if not key:
        raise RuntimeError("Не указан API-ключ Magnific в настройках")
    req = build(shot, refs, settings, inline=True)
    task = _http("POST", API_BASE + req["path"], key, req["body"])["data"]
    tid = task["task_id"]
    deadline = time.time() + 30 * 60
    while task.get("status") not in ("COMPLETED", "FAILED"):
        if time.time() > deadline:
            raise RuntimeError(f"Magnific не закончил за 30 минут (задача {tid})")
        time.sleep(10)
        task = _http("GET", f"{API_BASE}{req['status_path']}/{tid}", key)["data"]
    if task["status"] == "FAILED" or not task.get("generated"):
        raise RuntimeError("Magnific не сделал видео: " + json.dumps(task, ensure_ascii=False)[:500])
    return _http("GET", task["generated"][0], None, raw=True)
