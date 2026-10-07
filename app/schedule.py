"""Release queue. Episode number == release slot, one episode per day:
    date(N) = anchor_date + (N - anchor_number) days
Unpinned episodes own a number (date follows). Pinned episodes own a date (number follows),
so a holiday episode keeps its day when the queue is reordered. Number NULL = backlog.

Story arcs start at a release slot (start_number). Inside an arc episodes are counted again from 1:
arc_number = number - arc.start_number + 1. The release slot itself (and so the date) does not change."""
from datetime import date, timedelta

from . import db


def _anchor(settings=None):
    s = settings or db.get_settings()
    try:
        d = date.fromisoformat(s.get("anchor_date") or "")
    except ValueError:
        d = date.today()
    return int(s.get("anchor_number") or 1), d


def date_for(number: int | None, settings=None) -> str | None:
    if number is None:
        return None
    n0, d0 = _anchor(settings)
    return (d0 + timedelta(days=number - n0)).isoformat()


def number_for(day: str, settings=None) -> int:
    n0, d0 = _anchor(settings)
    return n0 + (date.fromisoformat(day) - d0).days


def normalize(c, first: int | None = None):
    """Recompute pinned numbers and resolve collisions by pushing unpinned episodes forward.
    `first` wins a collision (the episode the user just moved)."""
    settings = {r["key"]: r["value"] for r in c.execute("SELECT key, value FROM settings")}
    eps = [dict(r) for r in c.execute("SELECT id, number, pinned, planned_date FROM episodes")]
    taken: set[int] = set()
    pinned = sorted((e for e in eps if e["pinned"] and e["planned_date"]), key=lambda e: (e["id"] != first, e["id"]))
    updates = {}
    for e in pinned:
        n = number_for(e["planned_date"], settings)
        if n < 1 or n in taken:  # date before the series start or two pinned on one day -> unpin
            e["pinned"] = 0
            e["number"] = max(n, 1)
            continue
        taken.add(n)
        updates[e["id"]] = (n, 1, e["planned_date"])
    loose = [e for e in eps if e["id"] not in updates and e["number"] is not None]
    loose.sort(key=lambda e: (e["number"], e["id"] != first, e["id"]))
    for e in loose:
        n = max(1, e["number"])
        while n in taken:
            n += 1
        taken.add(n)
        updates[e["id"]] = (n, 0, None)
    for eid, (n, p, d) in updates.items():
        c.execute("UPDATE episodes SET number=?, pinned=?, planned_date=? WHERE id=?", (n, p, d, eid))


def move(eid: int, number: int | None):
    with db.connect() as c:
        e = c.execute("SELECT pinned FROM episodes WHERE id=?", (eid,)).fetchone()
        if number is None:
            c.execute("UPDATE episodes SET number=NULL, pinned=0, planned_date=NULL WHERE id=?", (eid,))
        elif e and e["pinned"]:
            c.execute("UPDATE episodes SET number=?, planned_date=? WHERE id=?", (number, date_for(number), eid))
        else:
            c.execute("UPDATE episodes SET number=? WHERE id=?", (number, eid))
        normalize(c, first=eid)


def set_pinned(eid: int, pinned: bool):
    with db.connect() as c:
        e = c.execute("SELECT number FROM episodes WHERE id=?", (eid,)).fetchone()
        if pinned and e["number"] is not None:
            c.execute("UPDATE episodes SET pinned=1, planned_date=? WHERE id=?", (date_for(e["number"]), eid))
        else:
            c.execute("UPDATE episodes SET pinned=0, planned_date=NULL WHERE id=?", (eid,))
        normalize(c, first=eid)


def compact():
    """Close gaps between unpinned episodes, keeping order; pinned stay on their dates."""
    with db.connect() as c:
        eps = [dict(r) for r in c.execute("SELECT id, number, pinned FROM episodes WHERE number IS NOT NULL ORDER BY number")]
        pinned = {e["number"] for e in eps if e["pinned"]}
        start = min((e["number"] for e in eps), default=1)
        n = start
        for e in eps:
            if e["pinned"]:
                continue
            while n in pinned:
                n += 1
            c.execute("UPDATE episodes SET number=? WHERE id=?", (n, e["id"]))
            n += 1
        normalize(c)


def next_free(settings=None) -> int:
    with db.connect() as c:
        taken = {r[0] for r in c.execute("SELECT number FROM episodes WHERE number IS NOT NULL")}
    n0, d0 = _anchor(settings)
    today_n = n0 + (date.today() - d0).days
    n = max(1, today_n)
    while n in taken:
        n += 1
    return n


def load_arcs() -> list[dict]:
    with db.connect() as c:
        return db.rows(c.execute("SELECT * FROM arcs ORDER BY start_number, id"))


def arc_for(number: int | None, arcs: list[dict]) -> dict | None:
    """The arc a release slot belongs to: the last arc that starts at or before it."""
    if number is None:
        return None
    found = None
    for a in arcs:
        if a["start_number"] <= number:
            found = a
    return found


def arc_info(number: int | None, arcs: list[dict]) -> dict:
    a = arc_for(number, arcs)
    if not a:
        return {"arc_id": None, "arc_title": "", "arc_number": None}
    return {"arc_id": a["id"], "arc_title": a["title"], "arc_number": number - a["start_number"] + 1}
