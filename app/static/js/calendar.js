// Posting calendar: month grid with episodes, holidays and own events; drag episodes between days.
import { $, $$, api, can, closeModal, esc, isoDate, modal, MONTHS, state, STATUS_COLORS, toast, view } from "./core.js";
import { newEpisodeDialog } from "./episode.js";
import { arcDialog, bindArcDividers, ensureAssets } from "./arcs.js";

const WEEKDAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"];
let cursor = null; // first day of shown month

// Что показывать в календаре (галочки над сеткой); выбор запоминается в браузере
const LAYERS = [["eps", "Публикации"], ["holidays", "Праздники"], ["own", "Свои события"], ["arcs", "Арки"]];
const SHOW_KEY = "calendarShow";
const show = (() => {
  const all = Object.fromEntries(LAYERS.map(([k]) => [k, true]));
  try { return { ...all, ...JSON.parse(localStorage.getItem(SHOW_KEY) || "{}") }; } catch { return all; }
})();

export async function renderCalendar() {
  if (!cursor) { const t = new Date(state.meta.today + "T00:00:00"); cursor = new Date(t.getFullYear(), t.getMonth(), 1); }
  const first = new Date(cursor);
  const gridStart = new Date(first);
  gridStart.setDate(1 - ((first.getDay() + 6) % 7));
  const days = Array.from({ length: 42 }, (_, i) => { const d = new Date(gridStart); d.setDate(gridStart.getDate() + i); return d; });
  const [sched] = await Promise.all([api(`/api/schedule?start=${isoDate(days[0])}&end=${isoDate(days[41])}`), ensureAssets()]);
  const arcAt = Object.fromEntries(sched.arcs.map((a) => [a.start_date, a]));
  const today = state.meta.today;
  const byDate = {};
  sched.episodes.forEach((e) => (byDate[e.date] = [...(byDate[e.date] || []), e]));
  const evs = {};
  sched.events.forEach((e) => (evs[e.date] = [...(evs[e.date] || []), e]));

  view().innerHTML = `
    <div class="row"><h1>Календарь постинга</h1><div class="spacer"></div>
      ${can.edit() ? `<button id="add-ev">+ Своё событие</button>` : ""}
      <button id="my-evs">Свои события</button>
      <a class="btn" href="#/series">Серии</a>
      <div class="row cal-nav"><button id="prev">←</button><b class="month-title">${MONTHS[first.getMonth()]} ${first.getFullYear()}</b><button id="next">→</button>
        <button class="ghost" id="today">Сегодня</button></div></div>
    <div class="row cal-filters">
      ${LAYERS.map(([k, name]) => `<label class="check"><input type="checkbox" data-layer="${k}" ${show[k] ? "checked" : ""}> ${name}</label>`).join("")}
      <span class="muted small">1 серия в день. Перетащите серию на другой день. Свои события: кнопка «+ Своё событие» или клик по числу; клик по событию — изменить или удалить, «⟩» — растянуть на несколько дней.</span>
    </div>
    <div class="cal">
      ${["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"].map((d) => `<div class="cal-h">${d}</div>`).join("")}
      ${days.map((d) => {
        const iso = isoDate(d);
        const eps = byDate[iso] || [];
        const other = d.getMonth() !== first.getMonth();
        return `<div class="cal-day ${other ? "other" : ""} ${iso === today ? "today" : ""} ${iso < today ? "past" : ""}" data-date="${iso}">
          <div class="cal-num" data-addev="${iso}" data-wd="${WEEKDAYS[(d.getDay() + 6) % 7]}" title="Добавить событие">${d.getDate()}</div>
          ${show.arcs && arcAt[iso] ? `<div class="cal-arc" data-arc="${arcAt[iso].id}" title="${esc(arcAt[iso].notes || "")}">▶ Арка: ${esc(arcAt[iso].title)}</div>` : ""}
          ${(evs[iso] || []).filter((ev) => (ev.builtin ? show.holidays : show.own)).map((ev) => evChip(ev)).join("")}
          ${(show.eps ? eps : []).map((e) => `<div class="cal-ep" draggable="${can.edit()}" data-id="${e.id}" style="--c:${STATUS_COLORS[e.status]}" title="${esc(e.status_name)}">
              <b>${e.arc_number ?? e.number}</b> ${esc(e.title || "Без названия")}${e.pinned ? " 📌" : ""}<div class="small">${esc(e.status_name)}</div></div>`).join("")}
          ${show.eps && !eps.length && iso >= today && can.edit() ? `<button class="cal-add ghost small" data-create="${iso}">+ серия</button>` : ""}
        </div>`;
      }).join("")}
    </div>`;

  $$("[data-layer]").forEach((cb) => (cb.onchange = () => {
    show[cb.dataset.layer] = cb.checked;
    try { localStorage.setItem(SHOW_KEY, JSON.stringify(show)); } catch {}
    renderCalendar();
  }));
  $("#prev").onclick = () => { cursor.setMonth(cursor.getMonth() - 1); renderCalendar(); };
  $("#next").onclick = () => { cursor.setMonth(cursor.getMonth() + 1); renderCalendar(); };
  $("#today").onclick = () => { cursor = null; renderCalendar(); };
  $("#my-evs").onclick = () => eventsListDialog();
  if ($("#add-ev")) $("#add-ev").onclick = () => eventDialog(null, today);
  $$(".cal-ep").forEach((el) => el.addEventListener("click", () => (location.hash = `#/episodes/${el.dataset.id}`)));
  bindArcDividers(renderCalendar);
  $$("[data-create]").forEach((b) => (b.onclick = () => newEpisodeDialog({ date: b.dataset.create })));
  $$("[data-delev]").forEach((b) => (b.onclick = async (e) => {
    e.stopPropagation();
    if (!confirm("Удалить событие?")) return;
    await api(`/api/calendar-events/${b.dataset.delev}`, { method: "DELETE" }); renderCalendar();
  }));
  if (!can.edit()) return;
  $$("[data-addev]").forEach((b) => (b.onclick = () => eventDialog(null, b.dataset.addev, { arcButton: true })));
  // Своё событие: клик — изменить, «⟩» на последнем дне — потянуть на другой день, чтобы растянуть или сжать
  const allEvs = Object.values(evs).flat();
  $$("[data-evid]").forEach((el) => (el.onclick = (e) => {
    if (e.target.closest("[data-delev]")) return;
    const ev = allEvs.find((x) => x.id === +el.dataset.evid);
    if (ev) eventDialog({ id: ev.id, date: ev.ev_date, end_date: ev.ev_end, title: ev.title, yearly: ev.yearly, color: ev.color });
  }));
  let growing = null;
  $$("[data-grow]").forEach((h) => {
    h.addEventListener("dragstart", (e) => { e.stopPropagation(); growing = allEvs.find((x) => x.id === +h.dataset.grow && x.last); e.dataTransfer.effectAllowed = "move"; });
    h.addEventListener("dragend", () => (growing = null));
    h.addEventListener("click", (e) => e.stopPropagation());
  });

  let dragId = null;
  $$(".cal-ep").forEach((el) => el.addEventListener("dragstart", () => (dragId = el.dataset.id)));
  $$(".cal-day").forEach((day) => {
    day.addEventListener("dragover", (e) => { e.preventDefault(); day.classList.add("drop-over"); });
    day.addEventListener("dragleave", () => day.classList.remove("drop-over"));
    day.addEventListener("drop", async (e) => {
      e.preventDefault();
      day.classList.remove("drop-over");
      if (growing) {
        const ev = growing; growing = null;
        const shift = Math.round((new Date(day.dataset.date + "T00:00:00") - new Date(ev.occ_start + "T00:00:00")) / 86400000);
        if (shift < 0) return toast("Событие не может заканчиваться раньше начала", true);
        await api(`/api/calendar-events/${ev.id}`, { method: "PUT", json: { date: ev.ev_date, title: ev.title, yearly: ev.yearly, color: ev.color, end_date: addDays(ev.ev_date, shift) } });
        return renderCalendar();
      }
      if (!dragId) return;
      await api(`/api/episodes/${dragId}/move`, { json: { date: day.dataset.date } });
      dragId = null;
      renderCalendar();
    });
  });
}

const addDays = (iso, n) => { const d = new Date(iso + "T00:00:00"); d.setDate(d.getDate() + n); return isoDate(d); };

/** Чип события в ячейке дня. Своё событие на несколько дней показывается в каждом дне; название — в первом (и в первом дне недели). */
function evChip(ev) {
  if (ev.builtin) return `<div class="cal-ev">🎉 ${esc(ev.title)}</div>`;
  const edit = can.edit();
  const multi = ev.ev_end > ev.ev_date;
  const label = ev.first || new Date(ev.date + "T00:00:00").getDay() === 1 ? `${esc(ev.title)}` : `<span class="ev-cont">↳ ${esc(ev.title)}</span>`;
  return `<div style="--ec:${evColor(ev)}" class="cal-ev own ${multi ? "multi" : ""} ${ev.first ? "first" : ""} ${ev.last ? "last" : ""}" ${edit ? `data-evid="${ev.id}" title="Нажмите, чтобы изменить"` : ""}>
    ${label}${ev.first && edit ? ` <span data-delev="${ev.id}" title="Удалить">✕</span>` : ""}${ev.last && edit ? `<span class="ev-grow" draggable="true" data-grow="${ev.id}" title="Потяните на другой день, чтобы растянуть событие">⟩</span>` : ""}</div>`;
}

// Цвета своих событий: отличаются от праздников (оранжевый текст с 🎉) и от статусов серий
const EV_COLORS = ["#7c5cff", "#2f80ed", "#00a389", "#e0457b", "#f2994a", "#6b7280"];
const evColor = (ev) => ev.color || EV_COLORS[0];
const fmtD = (iso) => iso.split("-").reverse().join(".");

/** Добавить (ev = null) или изменить своё событие: название, даты, повтор, цвет. */
function eventDialog(ev, iso, { arcButton = false } = {}) {
  const e = ev || { date: iso, end_date: iso, title: "", yearly: false, color: "" };
  let color = evColor(e);
  const m = modal(`<h1>${ev ? "Своё событие" : "Новое событие"}</h1>
    <label>Название (праздник, инфоповод, тренд, съёмка)</label><input id="ev-title" value="${esc(e.title)}" placeholder="Например: выход нового iPhone">
    <div class="row"><div style="flex:1"><label>С</label><input id="ev-from" type="date" value="${e.date}"></div>
      <div style="flex:1"><label>По (включительно)</label><input id="ev-to" type="date" value="${e.end_date || e.date}"></div></div>
    <label>Цвет в календаре</label>
    <div class="ev-colors">${EV_COLORS.map((c) => `<button type="button" class="ev-color ${c === color ? "on" : ""}" data-color="${c}" style="--ec:${c}" aria-label="цвет ${c}"></button>`).join("")}</div>
    <label class="check"><input type="checkbox" id="ev-yearly" ${e.yearly ? "checked" : ""}> повторять каждый год</label>
    <div class="row" style="margin-top:14px">${ev ? `<button class="ghost danger" id="ev-del">Удалить</button>` : ""}
      ${arcButton ? `<button class="ghost" id="new-arc">▶ Начать новую арку с этого дня</button>` : ""}
      <div class="spacer"></div><button class="ghost" id="cancel">Отмена</button><button class="primary" id="save">${ev ? "Сохранить" : "Добавить"}</button></div>`);
  $$(".ev-color", m).forEach((b) => (b.onclick = () => {
    color = b.dataset.color;
    $$(".ev-color", m).forEach((x) => x.classList.toggle("on", x === b));
  }));
  $("#ev-from", m).onchange = () => { if ($("#ev-to", m).value < $("#ev-from", m).value) $("#ev-to", m).value = $("#ev-from", m).value; };
  $("#cancel", m).onclick = closeModal;
  if (arcButton) $("#new-arc", m).onclick = () => arcDialog({ date: iso, onChange: renderCalendar });
  if (ev) $("#ev-del", m).onclick = async () => {
    if (!confirm(`Удалить событие «${ev.title}»?`)) return;
    await api(`/api/calendar-events/${ev.id}`, { method: "DELETE" }); closeModal(); renderCalendar();
  };
  $("#save", m).onclick = async () => {
    const title = $("#ev-title", m).value.trim(), from = $("#ev-from", m).value, to = $("#ev-to", m).value;
    if (!title || !from) return toast("Укажите название и день начала", true);
    if (to && to < from) return toast("Событие не может заканчиваться раньше начала", true);
    const json = { date: from, title, yearly: $("#ev-yearly", m).checked, color, end_date: !to || to === from ? "" : to };
    if (ev) await api(`/api/calendar-events/${ev.id}`, { method: "PUT", json });
    else await api("/api/calendar-events", { json });
    closeModal(); renderCalendar();
  };
  setTimeout(() => $("#ev-title", m).focus(), 50);
}

/** Меню «Свои события»: список всех событий с переходом к месяцу, правкой и удалением. */
async function eventsListDialog() {
  const list = await api("/api/calendar-events");
  const edit = can.edit();
  const m = modal(`<h1>Свои события</h1>
    <p class="muted small">В календаре свои события — цветная плашка, праздники — оранжевый текст с 🎉.</p>
    ${list.length ? `<div class="ev-list">${list.map((ev) => `<div class="ev-row" style="--ec:${evColor(ev)}">
        <span class="ev-dot"></span>
        <div class="ev-main"><b>${esc(ev.title)}</b><div class="muted small">${fmtD(ev.date)}${ev.end_date ? ` – ${fmtD(ev.end_date)}` : ""}${ev.yearly ? " · каждый год" : ""}</div></div>
        <button class="ghost small" data-goto="${ev.date}">Показать</button>
        ${edit ? `<button class="ghost small" data-ed="${ev.id}">Изменить</button><button class="ghost small danger" data-rm="${ev.id}">Удалить</button>` : ""}
      </div>`).join("")}</div>` : `<p class="muted">Своих событий пока нет.</p>`}
    <div class="row" style="margin-top:14px">${edit ? `<button class="primary" id="ev-new">+ Добавить событие</button>` : ""}<div class="spacer"></div><button class="ghost" id="cancel">Закрыть</button></div>`);
  $("#cancel", m).onclick = closeModal;
  if (edit) $("#ev-new", m).onclick = () => eventDialog(null, state.meta.today);
  $$("[data-goto]", m).forEach((b) => (b.onclick = () => {
    const d = new Date(b.dataset.goto + "T00:00:00");
    cursor = new Date(d.getFullYear(), d.getMonth(), 1); closeModal(); renderCalendar();
  }));
  $$("[data-ed]", m).forEach((b) => (b.onclick = () => eventDialog(list.find((x) => x.id === +b.dataset.ed))));
  $$("[data-rm]", m).forEach((b) => (b.onclick = async () => {
    const ev = list.find((x) => x.id === +b.dataset.rm);
    if (!confirm(`Удалить событие «${ev.title}»?`)) return;
    await api(`/api/calendar-events/${ev.id}`, { method: "DELETE" });
    renderCalendar(); eventsListDialog();
  }));
}
