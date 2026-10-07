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


def load_arcs(c=None) -> list[dict]:
    """Арки с вычисленными полями: start_number/end_number — первый и последний слот выхода её серий (None, если
    все серии в черновиках), episodes/drafts — сколько серий в очереди и в черновиках."""
    if c is None:
        with db.connect() as conn:
            return load_arcs(conn)
    arcs = db.rows(c.execute("SELECT * FROM arcs ORDER BY id"))
    eps = [dict(r) for r in c.execute("SELECT id, number, arc_id FROM episodes WHERE arc_id IS NOT NULL")]
    for a in arcs:
        nums = sorted(e["number"] for e in eps if e["arc_id"] == a["id"] and e["number"] is not None)
        a["start_number"] = nums[0] if nums else None
        a["end_number"] = nums[-1] if nums else None
        a["episodes"] = len(nums)
        a["drafts"] = sum(1 for e in eps if e["arc_id"] == a["id"] and e["number"] is None)
    # Сначала арки с сериями в очереди (по первому дню), потом арки только с черновиками (по созданию)
    arcs.sort(key=lambda a: (a["start_number"] is None, a["start_number"] or 0, a["id"]))
    return arcs


def arc_numbers(eps: list[dict], arcs: list[dict]) -> dict[int, dict]:
    """{episode_id: {arc_id, arc_title, arc_number}}. Номер внутри арки — место серии среди серий этой арки,
    стоящих в очереди (по дню выхода). У черновиков номера нет."""
    titles = {a["id"]: a["title"] for a in arcs}
    out: dict[int, dict] = {}
    by_arc: dict[int, list] = {}
    for e in eps:
        if e.get("arc_id") in titles:
            by_arc.setdefault(e["arc_id"], []).append(e)
            out[e["id"]] = {"arc_id": e["arc_id"], "arc_title": titles[e["arc_id"]], "arc_number": None}
        else:
            out[e["id"]] = {"arc_id": None, "arc_title": "", "arc_number": None}
    for items in by_arc.values():
        for k, e in enumerate(sorted((x for x in items if x["number"] is not None), key=lambda x: x["number"]), 1):
            out[e["id"]]["arc_number"] = k
    return out


def append_to_arc(eid: int, arc_id: int | None) -> int:
    """Поставить серию в очередь в конец её арки (следующий день после последней серии арки); серию без арки —
    в конец всей очереди. Если день занят, следующие серии сдвигаются вперёд (кроме закреплённых)."""
    with db.connect() as c:
        if arc_id is None:
            last = c.execute("SELECT MAX(number) FROM episodes WHERE number IS NOT NULL AND id<>?", (eid,)).fetchone()[0]
        else:
            last = c.execute("SELECT MAX(number) FROM episodes WHERE arc_id=? AND number IS NOT NULL AND id<>?",
                             (arc_id, eid)).fetchone()[0]
    number = last + 1 if last else next_free()
    move(eid, number)
    return number


def reorder_queue(ids: list[int]) -> None:
    """Новый порядок в общей очереди выкладки (перетаскивание в «Сериях»): серии обмениваются своими днями.
    Выложенные и закреплённые за датой серии не двигаются."""
    with db.connect() as c:
        eps = {r["id"]: dict(r) for r in c.execute("SELECT id, number, pinned, status FROM episodes WHERE number IS NOT NULL")}
        movable = [eps[i] for i in ids if i in eps and not eps[i]["pinned"] and eps[i]["status"] != "posted"]
        for e, n in zip(movable, sorted(e["number"] for e in movable)):
            c.execute("UPDATE episodes SET number=? WHERE id=?", (n, e["id"]))


def reorder_arc(arc_id: int | None, ids: list[int]) -> None:
    """Новый порядок серий арки (перетаскивание). Серии в очереди обмениваются своими днями: набор дней остаётся
    тем же, меняется только кто в какой день выходит. Выложенные и закреплённые за датой серии не двигаются."""
    with db.connect() as c:
        eps = {r["id"]: dict(r) for r in c.execute(
            "SELECT id, number, pinned, status FROM episodes WHERE arc_id IS ?", (arc_id,))}
        movable = [eps[i] for i in ids if i in eps and eps[i]["number"] is not None
                   and not eps[i]["pinned"] and eps[i]["status"] != "posted"]
        slots = sorted(e["number"] for e in movable)
        for e, n in zip(movable, slots):
            c.execute("UPDATE episodes SET number=? WHERE id=?", (n, e["id"]))
        for pos, i in enumerate(ids):
            c.execute("UPDATE episodes SET position=? WHERE id=?", (pos, i))
