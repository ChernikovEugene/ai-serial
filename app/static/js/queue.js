// Release queue: numbered slots (1 episode per day), drag & drop, backlog, status filter.
import { $, $$, api, addDays, can, esc, fmtDate, fmtDur, state, statusPill, toast, view, STATUS_COLORS } from "./core.js";
import { newEpisodeDialog } from "./episode.js";

let filter = null;
let expanded = false;

export async function renderQueue() {
  const [eps] = await Promise.all([api("/api/episodes")]);
  const m = state.meta;
  const today = m.today;
  const dated = eps.filter((e) => e.number != null);
  const backlog = eps.filter((e) => e.number == null);
  const todayN = m.anchor_number + Math.round((new Date(today) - new Date(m.anchor_date)) / 86400000);
  const minN = Math.max(1, Math.min(todayN, ...dated.map((e) => e.number)));
  const maxN = Math.max(todayN + 6, ...dated.map((e) => e.number + 3));
  const byNum = Object.fromEntries(dated.map((e) => [e.number, e]));
  const dateOf = (n) => addDays(m.anchor_date, n - m.anchor_number);
  const sched = await api(`/api/schedule?start=${dateOf(minN)}&end=${dateOf(maxN)}`);
  const holidays = {};
  sched.events.forEach((ev) => (holidays[ev.date] = [...(holidays[ev.date] || []), ev.title]));

  const counts = Object.fromEntries(m.statuses.map((s) => [s.key, eps.filter((e) => e.status === s.key).length]));
  const futureEmpty = Array.from({ length: maxN - Math.max(minN, todayN) + 1 }, (_, i) => Math.max(minN, todayN) + i)
    .filter((n) => !byNum[n]).length;

  const rows = [];
  for (let n = minN; n <= maxN; n++) {
    const e = byNum[n];
    const d = dateOf(n);
    if (filter && (!e || e.status !== filter)) continue;
    if (!e && !filter && !expanded) {
      // long run of free days -> 2 rows + one collapsed row (holidays inside stay visible)
      let end = n;
      while (end + 1 <= maxN && !byNum[end + 1]) end++;
      if (end - n >= 4) {
        rows.push(emptyRow(n, d, holidays[d], today), emptyRow(n + 1, dateOf(n + 1), holidays[dateOf(n + 1)], today));
        const hidden = [];
        for (let k = n + 2; k <= end; k++) if (holidays[dateOf(k)]) hidden.push(`${fmtDate(dateOf(k), false)}: ${holidays[dateOf(k)].join(", ")}`);
        rows.push(`<div class="q-row empty collapsed" data-expand="1"><span></span><span class="q-num muted">…</span>
          <span class="q-date muted">${fmtDate(dateOf(n + 2), false)} — ${fmtDate(dateOf(end), false)}</span>
          <span class="q-title muted">ещё ${end - n - 1} свободных дней${hidden.length ? `<span class="hol">🎉 ${esc(hidden.join(" · "))}</span>` : ""}</span>
          <button class="ghost small">показать</button><span></span></div>`);
        n = end;
        continue;
      }
    }
    rows.push(e ? epRow(e, d, holidays[d], today) : emptyRow(n, d, holidays[d], today));
  }

  view().innerHTML = `
    <div class="row"><h1>Очередь серий</h1><div class="spacer"></div>
      <a class="btn" href="#/calendar">Календарь</a>
      ${can.edit() ? `<button class="primary" id="new-ep">+ Новая серия</button>` : ""}</div>
    <div class="card anchor-bar row">
      <span>Серия №</span><input id="anchor-n" type="number" min="1" value="${m.anchor_number}" style="width:80px">
      <span>выходит</span><input id="anchor-d" type="date" value="${m.anchor_date}" style="width:160px">
      <span class="muted">— дальше по 1 серии в день. Номер = день выхода.</span>
      <div class="spacer"></div>
      ${can.edit() ? `<button id="anchor-save">Применить</button><button class="ghost" id="compact" title="Сдвинуть серии, чтобы не было пустых дней (закреплённые даты не трогаются)">Убрать пропуски</button>` : ""}
    </div>
    <div class="row stats">
      <button class="chip-btn ${!filter ? "on" : ""}" data-filter="">Все · ${eps.length}</button>
      ${m.statuses.map((s) => `<button class="chip-btn ${filter === s.key ? "on" : ""}" data-filter="${s.key}" style="--c:${STATUS_COLORS[s.key]}"><i></i>${esc(s.name)} · ${counts[s.key]}</button>`).join("")}
      <span class="muted small">Свободных дней впереди: ${futureEmpty}</span>
    </div>
    <div class="queue" id="queue">${rows.join("") || `<p class="muted">Нет серий с таким статусом.</p>`}</div>
    <h2>Бэклог <span class="muted small">— серии без даты. Перетащите в очередь, чтобы назначить номер.</span></h2>
    <div class="queue backlog drop" data-backlog="1">${backlog.map((e) => epRow(e, null, null, today)).join("") || `<div class="muted small empty-drop">Перетащите сюда серию, чтобы убрать её из очереди</div>`}</div>`;

  $("#new-ep") && ($("#new-ep").onclick = () => newEpisodeDialog());
  $$("[data-expand]").forEach((r) => (r.onclick = () => { expanded = true; renderQueue(); }));
  $$("[data-filter]").forEach((b) => (b.onclick = () => { filter = b.dataset.filter || null; renderQueue(); }));
  if (can.edit()) {
    $("#anchor-save").onclick = async () => {
      await api("/api/schedule/anchor", { method: "PUT", json: { number: +$("#anchor-n").value, date: $("#anchor-d").value } });
      await refreshMeta(); toast("Точка отсчёта обновлена"); renderQueue();
    };
    $("#compact").onclick = async () => { await api("/api/schedule/compact", { json: {} }); renderQueue(); };
  }
  bindQueue();
}

async function refreshMeta() { state.meta = await api("/api/meta"); }

function epRow(e, d, hol, today) {
  const past = d && d < today;
  const isToday = d === today;
  return `<div class="q-row ep ${past ? "past" : ""} ${isToday ? "today" : ""}" draggable="${can.edit()}" data-id="${e.id}" data-number="${e.number ?? ""}" style="--c:${STATUS_COLORS[e.status]}">
    <span class="grip" title="Перетащите">⋮⋮</span>
    <span class="q-num">${e.number != null ? `<input class="num-input" data-num="${e.id}" type="number" min="1" value="${e.number}" title="Номер серии — можно поменять вручную" ${can.edit() ? "" : "disabled"}>` : "—"}</span>
    <span class="q-date">${d ? `${fmtDate(d)}${isToday ? ` <b class="today-tag">сегодня</b>` : ""}` : `<span class="muted">без даты</span>`}
      ${hol ? `<div class="hol">🎉 ${esc(hol.join(", "))}</div>` : ""}</span>
    <span class="q-title"><b>${esc(e.title || "Без названия")}</b>
      <span class="muted small">${e.shots} шотов · ${fmtDur(e.seconds)}${e.over ? ` · <span class="warn-t">⚠ не влезают: ${e.over}</span>` : ""}${e.shots ? ` · видео ${e.with_take}/${e.shots}` : ""}${e.open_fixes ? ` · <span class="warn-t">правок: ${e.open_fixes}</span>` : ""}</span></span>
    ${statusPill(e.status)}
    ${e.number != null ? `<button class="ghost pin ${e.pinned ? "on" : ""}" data-pin="${e.id}" title="${e.pinned ? "Дата закреплена: при перестановках серия остаётся в этот день" : "Закрепить дату (например, под праздник)"}">📌</button>` : `<span></span>`}
  </div>`;
}

function emptyRow(n, d, hol, today) {
  const past = d < today;
  return `<div class="q-row empty ${past ? "past" : ""} ${d === today ? "today" : ""}" data-number="${n}">
    <span></span><span class="q-num muted">${n}</span>
    <span class="q-date">${fmtDate(d)}${d === today ? ` <b class="today-tag">сегодня</b>` : ""}${hol ? `<div class="hol">🎉 ${esc(hol.join(", "))}</div>` : ""}</span>
    <span class="q-title muted">свободный день</span>
    ${can.edit() && !past ? `<button class="ghost small" data-create="${d}">+ серия на этот день</button>` : "<span></span>"}<span></span>
  </div>`;
}

function bindQueue() {
  $$(".q-row.ep").forEach((r) => r.addEventListener("click", (e) => {
    if (e.target.closest("input,button")) return;
    location.hash = `#/episodes/${r.dataset.id}`;
  }));
  $$("[data-pin]").forEach((b) => (b.onclick = async () => {
    await api(`/api/episodes/${b.dataset.pin}/pin`, { json: { pinned: !b.classList.contains("on") } });
    renderQueue();
  }));
  $$("[data-num]").forEach((inp) => (inp.onchange = async () => {
    await api(`/api/episodes/${inp.dataset.num}/move`, { json: { number: +inp.value } });
    renderQueue();
  }));
  $$("[data-create]").forEach((b) => (b.onclick = () => newEpisodeDialog({ date: b.dataset.create })));
  if (!can.edit()) return;

  let dragId = null;
  $$(".q-row.ep").forEach((r) => {
    r.addEventListener("dragstart", (e) => { dragId = r.dataset.id; r.classList.add("dragging"); e.dataTransfer.effectAllowed = "move"; });
    r.addEventListener("dragend", () => { r.classList.remove("dragging"); $$(".drop-over").forEach((x) => x.classList.remove("drop-over")); });
  });
  const targets = [...$$(".q-row[data-number]").filter((r) => r.dataset.number), ...$$("[data-backlog]")];
  targets.forEach((t) => {
    t.addEventListener("dragover", (e) => { e.preventDefault(); t.classList.add("drop-over"); });
    t.addEventListener("dragleave", () => t.classList.remove("drop-over"));
    t.addEventListener("drop", async (e) => {
      e.preventDefault();
      t.classList.remove("drop-over");
      if (!dragId) return;
      const number = t.dataset.backlog ? null : +t.dataset.number;
      await api(`/api/episodes/${dragId}/move`, { json: { number } });
      dragId = null;
      renderQueue();
    });
  });
}
