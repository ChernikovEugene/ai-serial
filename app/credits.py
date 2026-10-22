"""Остаток кредитов на аккаунте Magnific, по ключу которого работает студия.

В открытой документации API Magnific (docs.magnific.com) нет адреса «сколько у меня кредитов»: есть только отчёт о расходе для
командных тарифов. Поэтому студия пробует несколько вероятных адресов, запоминает тот, что ответил, и ищет в ответе число
остатка. Нужный адрес можно вписать в «Настройки» (`magnific_credits_path`). Если ни один не подошёл, показывается пометка
«Magnific не отдаёт остаток по ключу», а если Magnific сам присылает остаток в заголовках ответов на генерацию, берётся он."""
import time
from datetime import datetime

from . import models

CANDIDATES = ["/v1/credits", "/v1/ai/credits", "/v1/account/credits", "/v1/user/credits", "/v1/account", "/v1/me"]
CACHE_SECONDS = 120          # не чаще раза в две минуты ходим в Magnific
NEGATIVE_SECONDS = 1800      # если адреса не подошли, повторяем пробу раз в полчаса
AVAILABLE_KEYS = ("available", "remaining", "balance", "available_credits", "credits_available", "remaining_credits",
                  "credits_remaining", "credit_balance", "credits_balance", "credits_left", "left")
TOTAL_KEYS = ("total", "total_credits", "credits_total", "limit", "plan_credits", "total_plan", "totalplan")
SPENT_KEYS = ("spent", "used", "used_credits", "credits_used", "consumed")

_cache: dict = {"at": 0.0, "value": None, "path": ""}


def _num(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return v
    if isinstance(v, str):
        try:
            return float(v.replace(" ", "").replace(",", "."))
        except ValueError:
            return None
    return None


def _find(obj, keys, depth=0):
    """Ищет число по одному из ключей на любой глубине (до 4 уровней); ключ `credits` может быть и числом, и словарём."""
    if depth > 4:
        return None
    if isinstance(obj, dict):
        low = {str(k).lower(): v for k, v in obj.items()}
        for k in keys:
            if k in low and _num(low[k]) is not None:
                return _num(low[k])
        for v in low.values():
            if isinstance(v, (dict, list)):
                if (n := _find(v, keys, depth + 1)) is not None:
                    return n
        if (n := _num(low.get("credits"))) is not None and keys is AVAILABLE_KEYS:
            return n
    elif isinstance(obj, list):
        for v in obj[:5]:
            if (n := _find(v, keys, depth + 1)) is not None:
                return n
    return None


def parse(payload) -> dict | None:
    """{"available", "total", "spent"} из ответа Magnific или None, если остатка в нём нет."""
    available = _find(payload, AVAILABLE_KEYS)
    if available is None:
        return None
    return {"available": available, "total": _find(payload, TOTAL_KEYS), "spent": _find(payload, SPENT_KEYS)}


def _from_headers() -> dict | None:
    h = models.CREDIT_HEADERS
    if not h or time.time() - h.get("_at", 0) > 6 * 3600:
        return None
    for k, v in h.items():
        if k != "_at" and any(w in k for w in ("remaining", "balance", "available")) and _num(v) is not None:
            return {"available": _num(v), "total": None, "spent": None, "path": f"заголовок {k}"}
    return None


def get(settings: dict, force: bool = False) -> dict:
    """{"ok": True, "available", "total", "spent", "path", "at"} или {"ok": False, "reason", "message"}."""
    key = (settings.get("magnific_api_key") or "").strip()
    if not key:
        return {"ok": False, "reason": "no_key", "message": "Не указан API-ключ Magnific в настройках"}
    now = time.time()
    c = _cache
    if not force and c["value"] is not None:
        ttl = CACHE_SECONDS if c["value"].get("ok") else NEGATIVE_SECONDS
        if now - c["at"] < ttl and c.get("key") == key:
            return c["value"]
    custom = (settings.get("magnific_credits_path") or "").strip()
    paths = [custom if custom.startswith("/") else "/" + custom] if custom else ([c["path"]] if c["path"] else []) + CANDIDATES
    value = None
    denied = ""
    for p in dict.fromkeys(paths):
        try:
            payload = models._http("GET", models.API_BASE + p, key)
        except RuntimeError as e:
            msg = str(e)
            if "HTTP 401" in msg or "HTTP 403" in msg:
                denied = msg
            continue
        got = parse(payload)
        if got:
            c["path"] = p
            value = {"ok": True, **got, "path": p}
            break
    if value is None and (h := _from_headers()):
        value = {"ok": True, **h}
    if value is None:
        value = {"ok": False, "reason": "denied" if denied else "unavailable",
                 "message": "Magnific отклонил ключ: проверьте его в «Настройки»" if denied
                 else "Magnific не отдаёт остаток кредитов по API-ключу. Посмотрите его в кабинете Magnific; "
                      "если знаете адрес, впишите его в «Настройки» → «Адрес остатка кредитов»"}
    value["at"] = datetime.now().isoformat(timespec="seconds")
    c.update(at=now, value=value, key=key)
    return value
