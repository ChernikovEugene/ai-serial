// Posting calendar: month grid with episodes, holidays and own events; drag episodes between days.
import { $, $$, api, can, closeModal, esc, isoDate, modal, MONTHS, state, STATUS_COLORS, toast, view } from "./core.js";
import { newEpisodeDialog } from "./episode.js";
import { arcDialog, bindArcDividers, ensureAssets } from "./arcs.js";

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
      <a class="btn" href="#/series">Серии</a>
      <button id="prev">←</button><b class="month-title">${MONTHS[first.getMonth()]} ${first.getFullYear()}</b><button id="next">→</button>
      <button class="ghost" id="today">Сегодня</button></div>
    <div class="row cal-filters">
      ${LAYERS.map(([k, name]) => `<label class="check"><input type="checkbox" data-layer="${k}" ${show[k] ? "checked" : ""}> ${name}</label>`).join("")}
      <span class="muted small">1 серия в день. Перетащите серию на другой день; клик по числу — своё событие или новая арка.</span>
    </div>
    <div class="cal">
      ${["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"].map((d) => `<div class="cal-h">${d}</div>`).join("")}
      ${days.map((d) => {
        const iso = isoDate(d);
        const eps = byDate[iso] || [];
        const other = d.getMonth() !== first.getMonth();
        return `<div class="cal-day ${other ? "other" : ""} ${iso === today ? "today" : ""} ${iso < today ? "past" : ""}" data-date="${iso}">
          <div class="cal-num" data-addev="${iso}" title="Добавить событие">${d.getDate()}</div>
          ${show.arcs && arcAt[iso] ? `<div class="cal-arc" data-arc="${arcAt[iso].id}" title="${esc(arcAt[iso].notes || "")}">▶ Арка: ${esc(arcAt[iso].title)}</div>` : ""}
          ${(evs[iso] || []).filter((ev) => (ev.builtin ? show.holidays : show.own)).map((ev) => `<div class="cal-ev ${ev.builtin ? "" : "own"}">🎉 ${esc(ev.title)}${!ev.builtin && can.edit() ? ` <span data-delev="${ev.id}" title="Удалить">✕</span>` : ""}</div>`).join("")}
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
  $$(".cal-ep").forEach((el) => el.addEventListener("click", () => (location.hash = `#/episodes/${el.dataset.id}`)));
  bindArcDividers(renderCalendar);
  $$("[data-create]").forEach((b) => (b.onclick = () => newEpisodeDialog({ date: b.dataset.create })));
  $$("[data-delev]").forEach((b) => (b.onclick = async (e) => {
    e.stopPropagation();
    await api(`/api/calendar-events/${b.dataset.delev}`, { method: "DELETE" }); renderCalendar();
  }));
  if (!can.edit()) return;
  $$("[data-addev]").forEach((b) => (b.onclick = () => addEventDialog(b.dataset.addev)));

  let dragId = null;
  $$(".cal-ep").forEach((el) => el.addEventListener("dragstart", () => (dragId = el.dataset.id)));
  $$(".cal-day").forEach((day) => {
    day.addEventListener("dragover", (e) => { e.preventDefault(); day.classList.add("drop-over"); });
    day.addEventListener("dragleave", () => day.classList.remove("drop-over"));
    day.addEventListener("drop", async (e) => {
      e.preventDefault();
      day.classList.remove("drop-over");
      if (!dragId) return;
      await api(`/api/episodes/${dragId}/move`, { json: { date: day.dataset.date } });
      dragId = null;
      renderCalendar();
    });
  });
}

function addEventDialog(iso) {
  const m = modal(`<h1>Событие на ${iso.split("-").reverse().join(".")}</h1>
    <label>Название (праздник, инфоповод, тренд)</label><input id="ev-title" placeholder="Например: выход нового iPhone">
    <label class="check"><input type="checkbox" id="ev-yearly"> повторять каждый год</label>
    <div class="row" style="margin-top:14px"><button class="ghost" id="new-arc">▶ Начать новую арку с этого дня</button>
      <div class="spacer"></div><button class="ghost" id="cancel">Отмена</button><button class="primary" id="save">Добавить</button></div>`);
  $("#cancel", m).onclick = closeModal;
  $("#new-arc", m).onclick = () => arcDialog({ date: iso, onChange: renderCalendar });
  $("#save", m).onclick = async () => {
    const title = $("#ev-title", m).value.trim();
    if (!title) return toast("Введите название", true);
    await api("/api/calendar-events", { json: { date: iso, title, yearly: $("#ev-yearly", m).checked } });
    closeModal(); renderCalendar();
  };
  setTimeout(() => $("#ev-title", m).focus(), 50);
}
