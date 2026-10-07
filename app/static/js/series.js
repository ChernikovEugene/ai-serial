// «Серии»: сверху карточки арок; внутри арки — карточки серий для быстрых синопсисов (или список).
// Серии арки идут в общей очереди выхода (1 серия в день), порядок меняется перетаскиванием карточек.
// Черновики — серии без даты: не удалены, но в очередь не идут. Подробное ТЗ серии — на её странице (#/episodes/ID).
import { $, $$, api, ARC_STATUS_COLORS, can, closeModal, esc, fmtDate, goBack, modal, state, statusPill, STATUS_COLORS, toast, view } from "./core.js";
import { arcDialog, ensureAssets } from "./arcs.js";

const NO_ARC = { id: "none", title: "Без арки", notes: "", members: [], status: null };
const VIEW_KEY = "seriesView";
const WRITER_STATUSES = ["synopsis", "synopsis_review", "synopsis_ok", "dev", "review"];

let listView = (() => { try { return localStorage.getItem(VIEW_KEY) === "list"; } catch { return false; } })();

/** Можно ли править карточку: продюсер — всегда, сценарист — пока серия на его этапах. */
const editable = (e) => can.admin() || (can.write() && WRITER_STATUSES.includes(e.status));
const shortDate = (iso) => (iso ? fmtDate(iso) : "");

// ---------------- главный экран: арки сверху, очередь выкладки ниже ----------------

const HISTORY_DAYS = 5; // выложенные серии без арки уходят в историю через столько дней
let showHistory = false;

/** Разбор очереди: живые арки и арки в архиве; выложенные серии закончившихся арок (и старые «вне арки») уходят в историю. */
function splitQueue(arcs, eps, today) {
  const queued = eps.filter((e) => e.number != null).sort((x, y) => x.number - y.number);
  const archived = arcs.filter((a) => a.archived);
  // Арка закончилась, когда все её серии в очереди выложены — тогда они уходят в «Историю»
  const finished = new Set(arcs.filter((a) => {
    const own = queued.filter((e) => e.arc_id === a.id);
    return own.length && own.every((e) => e.status === "posted");
  }).map((a) => a.id));
  const archivedIds = new Set(archived.map((a) => a.id));
  const isHistory = (e) => e.status === "posted" && (e.arc_id ? finished.has(e.arc_id) || archivedIds.has(e.arc_id) : daysAgo(e.date, today) > HISTORY_DAYS);
  return { live: arcs.filter((a) => !a.archived), archived, history: queued.filter(isHistory), active: queued.filter((e) => !isHistory(e)) };
}

export async function renderSeries() {
  const [arcs, eps] = await Promise.all([api("/api/arcs"), api("/api/episodes"), ensureAssets()]);
  const loose = eps.filter((e) => !e.arc_id);
  const today = state.meta.today;
  const { live, history, active } = splitQueue(arcs, eps, today);
  const cards = [...live, ...(loose.length ? [{ ...NO_ARC, episodes: loose.filter((e) => e.number != null).length, drafts: loose.filter((e) => e.number == null).length }] : [])];

  view().innerHTML = `
    <div class="row"><h1>Серии</h1><div class="spacer"></div>
      <a class="btn" href="#/archive" title="Закончившиеся арки и выложенные серии: чтобы вернуться и вспомнить сюжет">Архив</a>
      ${can.edit() ? `<button id="new-ep">+ Новая серия</button><button class="primary" id="new-arc">+ Новая арка</button>` : ""}</div>
    <div class="arc-grid compact">
      ${cards.map((a) => arcCard(a, eps.filter((e) => (a.id === "none" ? !e.arc_id : e.arc_id === a.id)))).join("")}
    </div>
    <div class="row queue-head"><h2>Очередь выкладки</h2>
      <span class="muted small">1 серия в день. Перетащите строку, чтобы поменять порядок; выложенные и закреплённые (📌) стоят на месте.</span>
      <div class="spacer"></div>${can.edit() ? `<button class="ghost small" id="compact" title="Сдвинуть серии, чтобы не было пустых дней (закреплённые не трогаются)">Убрать пропуски</button>` : ""}</div>
    <div class="ep-list queue-list" data-dnd="schedule">${queueRows(active, today, arcs)}</div>
    ${history.length ? `<button class="ghost small history-toggle" id="history">${showHistory ? "▾" : "▸"} История: ${plural(history.length, "выложенная серия", "выложенные серии", "выложенных серий")} завершённых арок</button>
      ${showHistory ? `<div class="ep-list queue-list history">${queueRows(history, today, arcs, false)}</div>` : ""}` : ""}`;

  $("#new-ep") && ($("#new-ep").onclick = () => newEpisodeQuick(arcs, renderSeries));
  $("#new-arc") && ($("#new-arc").onclick = () => arcDialog({ onChange: (arc) => arc && (location.hash = `#/series/${arc.id}`) }));
  $("#compact") && ($("#compact").onclick = async () => { await api("/api/schedule/compact", { json: {} }); toast("Пропуски убраны"); renderSeries(); });
  $("#history") && ($("#history").onclick = () => { showHistory = !showHistory; renderSeries(); });
  $$(".queue-row[data-open]").forEach((r) => r.addEventListener("click", (ev) => {
    if (!ev.target.closest("button,a,input")) location.hash = `#/episodes/${r.dataset.open}`;
  }));
  $$("[data-create]").forEach((b) => (b.onclick = () => newEpisodeQuick(arcs, renderSeries, b.dataset.create)));
  if (can.edit()) bindQueueDrag(renderSeries);
}

/** Страница «Архив»: закончившиеся арки (внутри — все серии с синопсисами) и выложенные серии закрытых арок. */
export async function renderArchive() {
  const [arcs, eps] = await Promise.all([api("/api/arcs"), api("/api/episodes"), ensureAssets()]);
  const today = state.meta.today;
  const { archived, history } = splitQueue(arcs, eps, today);
  view().innerHTML = `
    <div class="row"><button class="ghost" id="arch-back" title="Назад">←</button><h1 style="margin:0">Архив</h1></div>
    <p class="muted small">Выложенное и закрытое. Откройте арку, чтобы перечитать синопсисы и сюжет; вернуть арку в работу можно на её странице («↩ Из архива»).</p>
    <h2>Арки <span class="muted small">${archived.length}</span></h2>
    ${archived.length ? `<div class="arc-grid compact">${archived.map((a) => arcCard(a, eps.filter((e) => e.arc_id === a.id))).join("")}</div>`
      : `<p class="muted small">Пока пусто. Когда арка закончится, на её странице нажмите «В архив».</p>`}
    <h2>Выложенные серии <span class="muted small">${history.length}</span></h2>
    ${history.length ? `<div class="ep-list queue-list history">${queueRows(history, today, arcs, false)}</div>` : `<p class="muted small">Выложенных серий закрытых арок пока нет.</p>`}`;
  $("#arch-back").onclick = () => goBack("#/series");
  $$(".queue-row[data-open]").forEach((r) => r.addEventListener("click", () => (location.hash = `#/episodes/${r.dataset.open}`)));
}

const daysAgo = (iso, today) => Math.round((new Date(today + "T00:00:00") - new Date(iso + "T00:00:00")) / 86400000);
const addDay = (iso, n) => { const d = new Date(iso + "T00:00:00"); d.setDate(d.getDate() + n); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`; };

/** Строки очереди: серии по дням, перед серией каждой арки — отбивка арки, между сериями — свободные дни
 * (пропуски) с кнопкой «+ серия на этот день» (`gaps`). */
function queueRows(list, today, arcs, gaps = true) {
  if (!list.length) return `<p class="muted">В очереди пока нет серий. Добавьте их в арке или кнопкой «+ Новая серия».</p>`;
  const out = [];
  const seen = new Set();
  let prev;
  list.forEach((e, i) => {
    const key = e.arc_id ?? "none";
    if (key !== prev) {
      out.push(arcBreak(arcs.find((a) => a.id === e.arc_id), seen.has(key)));
      seen.add(key);
      prev = key;
    }
    out.push(queueRow(e, today));
    const next = list[i + 1];
    if (gaps && next && next.number - e.number > 1) {
      const gap = next.number - e.number - 1;
      const first = addDay(e.date, 1);
      out.push(`<div class="queue-gap"><span>${gap === 1 ? `Свободный день · ${fmtDate(first)}` : `Свободно ${gap} дн. · ${fmtDate(first)} — ${fmtDate(addDay(e.date, gap))}`}</span>
        ${can.edit() && first >= today ? `<button class="ghost small" data-create="${first}">+ серия на ${fmtDate(first, false)}</button>` : ""}</div>`);
    }
  });
  return out.join("");
}

/** Отбивка арки в очереди; `again` — арка продолжается после перерыва (между её сериями вклинилась другая). */
function arcBreak(arc, again) {
  if (!arc) return `<a class="q-arc none" href="#/series/none"><span class="arc-tag">Вне арки</span>
    <span class="muted small">праздники, события, отдельные ролики</span></a>`;
  const syn = (arc.notes || "").replace(/\s+/g, " ");
  return `<a class="q-arc ${again ? "again" : ""}" href="#/series/${arc.id}" title="Открыть арку">
    <span class="arc-tag">${again ? "Арка · продолжение" : "Арка"}</span><b>${esc(arc.title)}</b>
    <span class="status small" style="--c:${ARC_STATUS_COLORS[arc.status]}">${esc(arc.status_name)}</span>
    ${arc.start_date ? `<span class="muted small">${shortDate(arc.start_date)} — ${shortDate(arc.end_date)} · ${plural(arc.episodes, "серия", "серии", "серий")}</span>` : ""}
    ${!again && syn ? `<span class="q-arc-syn muted small">${esc(syn.slice(0, 180))}${syn.length > 180 ? "…" : ""}</span>` : ""}
  </a>`;
}

function queueRow(e, today) {
  const movable = can.edit() && e.status !== "posted" && !e.pinned;
  const arc = e.arc_id ? `<span class="arc-chip">Серия ${e.arc_number}</span>` : `<span class="arc-chip none">Вне арки</span>`;
  return `<div class="ep-card queue-row ${e.date === today ? "today" : ""} ${e.date < today ? "past" : ""}" data-ep="${e.id}" data-open="${e.id}" data-movable="${movable ? 1 : ""}" style="--c:${STATUS_COLORS[e.status]}">
    <div class="q-when">${movable ? `<span class="grip">⋮⋮</span>` : ""}<b>${fmtDate(e.date)}</b>${e.date === today ? ` <span class="today-tag">сегодня</span>` : ""}${e.pinned ? " 📌" : ""}</div>
    <div>${arc}</div>
    <div class="q-text"><b>${esc(e.title || "Без названия")}</b><span class="muted small">${esc((e.synopsis || "").replace(/\s+/g, " ").slice(0, 160)) || "синопсиса нет"}</span></div>
    <div>${statusPill(e.status, true)}</div>
  </div>`;
}

function bindQueueDrag(rerender) {
  let drag = null;
  $$(".queue-row[data-movable='1']").forEach((row) => {
    row.draggable = true;
    row.addEventListener("dragstart", (ev) => { drag = row; row.classList.add("dragging"); ev.dataTransfer.effectAllowed = "move"; });
    row.addEventListener("dragend", () => { row.classList.remove("dragging"); $$(".drop-before,.drop-after").forEach((x) => x.classList.remove("drop-before", "drop-after")); });
  });
  const box = $("[data-dnd='schedule']");
  box?.addEventListener("dragover", (ev) => {
    if (!drag) return;
    ev.preventDefault();
    const over = ev.target.closest(".queue-row");
    $$(".drop-before,.drop-after", box).forEach((x) => x.classList.remove("drop-before", "drop-after"));
    if (over && over !== drag) over.classList.add(lowerHalf(ev, over) ? "drop-after" : "drop-before");
  });
  box?.addEventListener("drop", async (ev) => {
    if (!drag) return;
    ev.preventDefault();
    const over = ev.target.closest(".queue-row");
    if (!over || over === drag) return;
    over[lowerHalf(ev, over) ? "after" : "before"](drag);
    const ids = $$(".queue-row", box).map((r) => +r.dataset.ep);
    drag = null;
    await api("/api/schedule/reorder", { json: { ids } });
    rerender();
  });
}

const lowerHalf = (ev, el) => { const r = el.getBoundingClientRect(); return ev.clientY > r.top + r.height / 2; };

/** «+ Новая серия» — в том числе вне арки (праздник, событие). */
function newEpisodeQuick(arcs, rerender, date = "") {
  const m = modal(`<h1>Новая серия</h1>
    <label>Название</label><input id="nq-title" placeholder="Например: Хэллоуин у Ксю">
    <label>Синопсис</label><textarea id="nq-syn" rows="4" placeholder="О чём серия…"></textarea>
    <label>Арка</label><select id="nq-arc"><option value="">Вне арки (праздник, событие, отдельный ролик)</option>
      ${arcs.map((a) => `<option value="${a.id}">${esc(a.title)}</option>`).join("")}</select>
    <label>Когда выходит</label>
    <div class="nq-when">
      <label class="check"><input type="radio" name="nq-when" value="date" ${date ? "checked" : ""}> В конкретный день (закрепить 📌)
        <input id="nq-date" type="date" value="${date}" style="width:170px;margin-left:8px"></label>
      <label class="check"><input type="radio" name="nq-when" value="queue" ${date ? "" : "checked"}> В конец очереди (арки или общей)</label>
      <label class="check"><input type="radio" name="nq-when" value="draft"> В черновики</label>
    </div>
    <div class="row" style="margin-top:14px"><div class="spacer"></div><button class="ghost" id="nq-cancel">Отмена</button><button class="primary" id="nq-save">Создать</button></div>`);
  $("#nq-cancel", m).onclick = closeModal;
  $("#nq-date", m).onfocus = () => ($("input[value=date]", m).checked = true);
  $("#nq-save", m).onclick = async () => {
    const when = $("input[name=nq-when]:checked", m).value;
    const d = $("#nq-date", m).value;
    if (when === "date" && !d) return toast("Выберите день", true);
    await api("/api/episodes", { json: {
      title: $("#nq-title", m).value.trim(), synopsis: $("#nq-syn", m).value, arc_id: +$("#nq-arc", m).value || null,
      date: when === "date" ? d : null, backlog: when === "draft" } });
    closeModal();
    toast("Серия создана");
    rerender();
  };
  setTimeout(() => $("#nq-title", m).focus(), 50);
}

function arcCard(a, eps) {
  const queued = eps.filter((e) => e.number != null);
  const span = queued.length ? `${shortDate(queued[0].date)} — ${shortDate(queued.at(-1).date)}` : "даты не назначены";
  const bar = Object.keys(STATUS_COLORS).map((k) => {
    const n = eps.filter((e) => e.status === k).length;
    return n ? `<i style="flex:${n};background:${STATUS_COLORS[k]}" title="${esc(state.meta.statuses.find((s) => s.key === k)?.name || k)}: ${n}"></i>` : "";
  }).join("");
  const status = a.status ? `<span class="status small" style="--c:${ARC_STATUS_COLORS[a.status]}">${esc(a.status_name)}</span>` : "";
  return `<a class="arc-card ${a.archived ? "archived" : ""}" href="#/series/${a.id}">
    <div class="row">${a.archived ? `<span class="badge">в архиве</span>` : ""}${status}<div class="spacer"></div><span class="muted small">${esc(span)}</span></div>
    <h2>${esc(a.title)}</h2>
    <p class="arc-card-syn">${esc(a.notes || (a.id === "none" ? "Серии вне арок: праздники, события, отдельные ролики." : "Синопсис арки пока не написан."))}</p>
    <div class="arc-card-bar">${bar || `<i style="flex:1;background:var(--line)"></i>`}</div>
    <div class="muted small">${plural(a.episodes, "серия", "серии", "серий")} в очереди${a.drafts ? ` · ${plural(a.drafts, "черновик", "черновика", "черновиков")}` : ""}</div>
  </a>`;
}

const plural = (n, one, few, many) =>
  `${n} ${n % 10 === 1 && n % 100 !== 11 ? one : n % 10 >= 2 && n % 10 <= 4 && (n % 100 < 12 || n % 100 > 14) ? few : many}`;

// ---------------- арка: карточки серий ----------------

export async function renderArc(arcKey) {
  const [arcs, eps] = await Promise.all([api("/api/arcs"), api("/api/episodes"), ensureAssets()]);
  const arc = arcKey === "none" ? NO_ARC : arcs.find((a) => a.id === +arcKey);
  if (!arc) { view().innerHTML = `<p>Арка не найдена. <a href="#/series">К сериям</a></p>`; return; }
  const own = eps.filter((e) => (arc.id === "none" ? !e.arc_id : e.arc_id === arc.id));
  const queued = own.filter((e) => e.number != null).sort((x, y) => x.number - y.number);
  const drafts = own.filter((e) => e.number == null).sort((x, y) => x.position - y.position || x.id - y.id);
  const real = arc.id !== "none";
  const members = (arc.members || []).map((mm) => {
    const a = state.assets.find((x) => x.id === mm.asset_id);
    const v = a?.versions.find((x) => x.id === mm.version_id);
    return a ? `${a.kind === "location" ? "📍 " : ""}${a.name}${v ? ` · ${v.label}` : ""}` : "";
  }).filter(Boolean);

  view().innerHTML = `
    <div class="row arc-head">
      <button class="ghost" id="arc-back" title="Назад">←</button>
      <h1 style="margin:0">${esc(arc.title)}</h1>
      ${real && can.edit() ? `<select id="arc-status" class="status-select" style="--c:${ARC_STATUS_COLORS[arc.status]}">
          ${["writing", "client", "approved"].map((k) => `<option value="${k}" ${arc.status === k ? "selected" : ""}>${{ writing: "Синопсисы пишутся", client: "Синопсисы у клиента", approved: "Синопсисы согласованы" }[k]}</option>`).join("")}
        </select>` : real ? `<span class="status" style="--c:${ARC_STATUS_COLORS[arc.status]}">${esc(arc.status_name)}</span>` : ""}
      <div class="spacer"></div>
      ${real ? `<a class="btn" href="/api/arcs/${arc.id}/pdf" target="_blank" title="Синопсисы арки для клиента">PDF для клиента</a>` : ""}
      ${real && can.edit() ? `<button class="ghost" id="arc-edit">Арка и участники</button>
        <button class="ghost" id="arc-archive" title="${arc.archived ? "Вернуть арку на главную «Серий»" : "Арка закончилась: убрать с главной, но не удалять"}">${arc.archived ? "↩ Из архива" : "🗄 В архив"}</button>
        <button class="ghost danger" id="arc-del" title="Удалить арку (серии останутся)">🗑 Удалить арку</button>` : ""}
    </div>
    ${real && arc.archived ? `<p class="muted small arc-archived-note">🗄 Арка в архиве: на главной «Серий» её нет. Здесь можно перечитать синопсисы и сюжет.</p>` : ""}
    ${real ? `<div class="card arc-syn-card">
      <label style="margin-top:0">Синопсис арки</label>
      <textarea id="arc-syn" rows="3" placeholder="О чём арка, что меняется в персонаже, локации, сюжете" ${can.edit() ? "" : "disabled"}>${esc(arc.notes)}</textarea>
      ${members.length ? `<div class="arc-members">${members.map((x) => `<span class="badge">${esc(x)}</span>`).join("")}</div>` : ""}
    </div>` : ""}
    <div class="row series-tools">
      <div class="seg"><button class="${listView ? "" : "on"}" data-view="cards">Карточки</button><button class="${listView ? "on" : ""}" data-view="list">Список</button></div>
      <span class="muted small">${can.edit() ? "Перетащите карточку, чтобы поменять порядок выхода. Выложенные и закреплённые (📌) серии стоят на месте." : ""}</span>
    </div>
    <h2>В очереди <span class="muted small">${queued.length}</span></h2>
    <div class="${listView ? "ep-list" : "ep-grid"}" data-dnd="queue">
      ${queued.map((e) => epCard(e)).join("")}
      ${can.edit() ? `<button class="ep-add" data-add="queue">+ Серия</button>` : ""}
    </div>
    <h2>Черновики <span class="muted small">${drafts.length} · не удалены, но в очередь не идут</span></h2>
    <div class="${listView ? "ep-list" : "ep-grid"}" data-dnd="drafts">
      ${drafts.map((e) => epCard(e)).join("") || (can.edit() ? "" : `<p class="muted small">Черновиков нет.</p>`)}
      ${can.edit() ? `<button class="ep-add" data-add="drafts">+ Черновик</button>` : ""}
    </div>`;

  const rerender = () => renderArc(arcKey);
  $("#arc-back").onclick = () => goBack("#/series");
  $$("[data-view]").forEach((b) => (b.onclick = () => {
    listView = b.dataset.view === "list";
    try { localStorage.setItem(VIEW_KEY, listView ? "list" : "cards"); } catch {}
    rerender();
  }));
  if (real && can.edit()) {
    $("#arc-status").onchange = async (ev) => {
      await api(`/api/arcs/${arc.id}/status`, { json: { status: ev.target.value } });
      toast("Статус арки обновлён — серии на этапе синопсиса получили его же");
      rerender();
    };
    $("#arc-edit").onclick = () => arcDialog({ arc, onChange: (saved) => (saved ? rerender() : (location.hash = "#/series")) });
    $("#arc-archive").onclick = async () => {
      await api(`/api/arcs/${arc.id}/archive`, { json: { archived: !arc.archived } });
      toast(arc.archived ? "Арка вернулась на главную «Серий»" : "Арка в архиве: она на странице «Архив»");
      if (arc.archived) rerender(); else location.hash = "#/archive";
    };
    $("#arc-del").onclick = async () => {
      if (!confirm(`Удалить арку «${arc.title}»? Серии не удалятся: они останутся на своих днях в разделе «Без арки».\n\nЕсли арка просто закончилась, лучше отправить её «В архив».`)) return;
      await api(`/api/arcs/${arc.id}`, { method: "DELETE" });
      toast("Арка удалена, серии остались «Без арки»");
      location.hash = "#/series";
    };
    $("#arc-syn").onchange = async (ev) => {
      await api(`/api/arcs/${arc.id}`, { method: "PUT", json: { title: arc.title, notes: ev.target.value, members: arc.members } });
      arc.notes = ev.target.value;
      toast("Синопсис арки сохранён");
    };
  }
  $$("[data-add]").forEach((b) => (b.onclick = async () => {
    const ep = await api(`/api/arcs/${arc.id}/episodes`, { json: { draft: b.dataset.add === "drafts" } });
    await rerender();
    $(`[data-ep="${ep.id}"] .ep-title`)?.focus();
  }));
  bindCards(arc, rerender);
}

function epCard(e) {
  const ed = editable(e);
  const head = e.number != null
    ? `<b>Серия ${e.arc_number ?? e.number}</b><span class="muted small">${shortDate(e.date)}${e.pinned ? " 📌" : ""}</span>`
    : `<b class="muted">Черновик</b>`;
  const movable = can.edit() && e.status !== "posted" && !(e.number != null && e.pinned);
  return `<div class="ep-card" data-ep="${e.id}" data-movable="${movable ? 1 : ""}" style="--c:${STATUS_COLORS[e.status]}">
    <div class="ep-card-head">${movable ? `<span class="grip" title="Потяните, чтобы переставить">⋮⋮</span>` : ""}${head}<div class="spacer"></div>${statusPill(e.status, true)}</div>
    <input class="ep-title" value="${esc(e.title)}" placeholder="Название серии" ${ed ? "" : "disabled"}>
    <textarea class="ep-syn" rows="${listView ? 1 : 4}" placeholder="Синопсис: о чём серия…" ${ed ? "" : "disabled"}>${esc(e.synopsis)}</textarea>
    <div class="ep-card-foot">
      <a class="btn go-tz" href="#/episodes/${e.id}" title="Открыть страницу серии: подробное ТЗ, шоты, ролик">Перейти к ТЗ →${e.shots ? ` <span class="go-tz-n">${plural(e.shots, "шот", "шота", "шотов")}</span>` : ""}</a><div class="spacer"></div>
      ${can.edit() ? `${e.number != null ? `<button class="ghost small" data-pin="${e.id}" data-date="${e.date || ""}" title="Закрепить за датой (под событие)">📌</button>
        <button class="ghost small" data-draft="${e.id}" title="Убрать из очереди, не удаляя">В черновик</button>`
        : `<button class="ghost small" data-queue="${e.id}">В очередь</button>`}` : ""}
      ${can.admin() ? `<button class="ghost small danger" data-del-ep="${e.id}" title="Удалить серию совсем">🗑</button>` : ""}
    </div>
  </div>`;
}

function bindCards(arc, rerender) {
  // Автосохранение названия и синопсиса при уходе из поля
  $$(".ep-card").forEach((card) => {
    const id = card.dataset.ep;
    $(".ep-title", card).onchange = (ev) => api(`/api/episodes/${id}`, { method: "PUT", json: { title: ev.target.value } }).then(() => toast("Сохранено"));
    const syn = $(".ep-syn", card);
    syn.onchange = (ev) => api(`/api/episodes/${id}`, { method: "PUT", json: { synopsis: ev.target.value } }).then(() => toast("Сохранено"));
    if (listView) {
      const fit = () => { syn.style.height = "auto"; syn.style.height = syn.scrollHeight + "px"; };
      syn.oninput = fit; fit();
    }
  });
  $$("[data-draft]").forEach((b) => (b.onclick = async () => {
    await api(`/api/episodes/${b.dataset.draft}/move`, { json: { number: null } });
    toast("Серия в черновиках: из очереди убрана, но не удалена");
    rerender();
  }));
  $$("[data-queue]").forEach((b) => (b.onclick = async () => {
    await api(`/api/episodes/${b.dataset.queue}/queue`, { json: {} });
    toast("Серия вернулась в очередь — в конец арки");
    rerender();
  }));
  $$("[data-del-ep]").forEach((b) => (b.onclick = async () => {
    const t = $(".ep-title", b.closest(".ep-card")).value || "без названия";
    if (!confirm(`Удалить серию «${t}» совсем? Пропадут её сценарий, шоты и ролики, вернуть нельзя.\n\nЕсли серия просто не нужна сейчас, нажмите «В черновик».`)) return;
    await api(`/api/episodes/${b.dataset.delEp}`, { method: "DELETE" });
    toast("Серия удалена");
    rerender();
  }));
  $$("[data-pin]").forEach((b) => (b.onclick = () => pinDialog(+b.dataset.pin, b.dataset.date, rerender)));
  if (can.edit()) bindDrag(arc, rerender);
}

/** Перетаскивание карточек внутри «В очереди» и внутри «Черновиков». */
function bindDrag(arc, rerender) {
  let drag = null;
  $$(".ep-card[data-movable='1']").forEach((card) => {
    // В полях ввода — обычное выделение текста, за остальную карточку — перетаскивание
    card.addEventListener("mousedown", (ev) => (card.draggable = !ev.target.closest("input,textarea,button,a,select")));
    card.addEventListener("dragstart", (ev) => { drag = card; card.classList.add("dragging"); ev.dataTransfer.effectAllowed = "move"; });
    card.addEventListener("dragend", () => { card.classList.remove("dragging"); card.draggable = false; $$(".drop-before,.drop-after").forEach((x) => x.classList.remove("drop-before", "drop-after")); });
  });
  $$("[data-dnd]").forEach((box) => {
    box.addEventListener("dragover", (ev) => {
      if (!drag || drag.parentElement !== box) return;
      ev.preventDefault();
      const over = ev.target.closest(".ep-card");
      $$(".drop-before,.drop-after", box).forEach((x) => x.classList.remove("drop-before", "drop-after"));
      if (over && over !== drag) over.classList.add(afterHalf(ev, over) ? "drop-after" : "drop-before");
    });
    box.addEventListener("drop", async (ev) => {
      if (!drag || drag.parentElement !== box) return;
      ev.preventDefault();
      const over = ev.target.closest(".ep-card");
      if (over && over !== drag) over[afterHalf(ev, over) ? "after" : "before"](drag);
      else box.querySelector(".ep-add")?.before(drag);
      const ids = $$(".ep-card", box).map((c) => +c.dataset.ep);
      drag = null;
      await api(`/api/arcs/${arc.id}/order`, { json: { ids } });
      rerender();
    });
  });
}

const afterHalf = (ev, el) => {
  const r = el.getBoundingClientRect();
  return listView ? ev.clientY > r.top + r.height / 2 : ev.clientX > r.left + r.width / 2;
};

function pinDialog(id, current, rerender) {
  const m = modal(`<h1>Дата выхода</h1>
    <p class="muted">Закреплённая серия выходит в этот день при любых перестановках — например, под праздник или событие.</p>
    <label>Дата</label><input id="pin-date" type="date" value="${current || ""}" style="width:200px">
    <div class="row" style="margin-top:16px"><button class="ghost" id="unpin">Открепить</button><div class="spacer"></div>
      <button class="ghost" id="cancel">Отмена</button><button class="primary" id="save">Закрепить</button></div>`);
  $("#cancel", m).onclick = closeModal;
  $("#unpin", m).onclick = async () => { await api(`/api/episodes/${id}/pin`, { json: { pinned: false } }); closeModal(); rerender(); };
  $("#save", m).onclick = async () => {
    const d = $("#pin-date", m).value;
    if (!d) return toast("Выберите дату", true);
    await api(`/api/episodes/${id}/move`, { json: { date: d } });
    await api(`/api/episodes/${id}/pin`, { json: { pinned: true } });
    closeModal();
    rerender();
  };
}
