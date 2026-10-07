// «Продакшн» — страница нейронщика-монтажёра: серии очереди по этапам производства.
// Серии без готового ТЗ видны как «Ждёт ТЗ» (название, дата, арка), но не открываются.
import { $$, api, esc, fmtDate, state, STATUS_COLORS, view } from "./core.js";

const COLUMNS = [
  { key: "wait", name: "Ждёт ТЗ", hint: "сценарист ещё пишет", statuses: ["synopsis", "synopsis_review", "synopsis_ok", "dev", "review"] },
  { key: "approved", name: "ТЗ готово", hint: "можно генерировать", statuses: ["approved"] },
  { key: "generating", name: "Генерация", statuses: ["generating"] },
  { key: "fixes", name: "Монтаж", statuses: ["fixes"] },
  { key: "client_review", name: "У клиента", hint: "ролик на согласовании", statuses: ["client_review"] },
  { key: "ready", name: "Готов к постингу", statuses: ["ready"] },
];
const POSTED_SHOWN = 10;

let arcFilter = "all";
let showPosted = false;

export async function renderProduction() {
  const eps = await api("/api/production");
  const today = state.meta.today;
  const arcs = [...new Map(eps.filter((e) => e.arc_id).map((e) => [e.arc_id, e.arc_title])).entries()];
  const shown = eps.filter((e) => arcFilter === "all" || (arcFilter === "none" ? !e.arc_id : e.arc_id === +arcFilter));
  const posted = shown.filter((e) => e.status === "posted").sort((a, b) => b.number - a.number);
  const chip = (key, text) => `<button class="chip-btn ${String(arcFilter) === String(key) ? "on" : ""}" data-arc-filter="${key}">${esc(text)}</button>`;

  view().innerHTML = `
    <div class="row"><h1>Продакшн</h1><div class="spacer"></div>
      <span class="muted small">Серии очереди по этапам производства, внутри колонки — по дню выхода.</span></div>
    <div class="row prod-filters">
      ${chip("all", `Все арки · ${eps.length}`)}
      ${arcs.map(([id, title]) => chip(id, title)).join("")}
      ${eps.some((e) => !e.arc_id) ? chip("none", "Вне арки") : ""}
    </div>
    <div class="prod-board">
      ${COLUMNS.map((col) => {
        const items = shown.filter((e) => col.statuses.includes(e.status)).sort((a, b) => a.number - b.number);
        return `<section class="prod-col" style="--c:${STATUS_COLORS[col.statuses.at(-1)]}">
          <header><b>${col.name}</b><span class="muted small">${items.length}${col.hint ? ` · ${col.hint}` : ""}</span></header>
          ${items.map((e) => card(e, today)).join("") || `<p class="muted small">Пусто</p>`}
        </section>`;
      }).join("")}
    </div>
    ${posted.length ? `<button class="ghost small history-toggle" id="posted">${showPosted ? "▾" : "▸"} Опубликовано: ${posted.length}</button>
      ${showPosted ? `<div class="prod-posted">${posted.slice(0, POSTED_SHOWN).map((e) => card(e, today)).join("")}</div>` : ""}` : ""}`;

  $$("[data-arc-filter]").forEach((b) => (b.onclick = () => { arcFilter = b.dataset.arcFilter; renderProduction(); }));
  const toggle = document.getElementById("posted");
  if (toggle) toggle.onclick = () => { showPosted = !showPosted; renderProduction(); };
}

function card(e, today) {
  const late = e.date < today && e.status !== "posted";
  const soon = !late && e.date <= addDays(today, 2) && !["ready", "posted"].includes(e.status);
  const arc = e.arc_id ? `${esc(e.arc_title)} · серия ${e.arc_number}` : "Вне арки";
  const body = `<div class="row"><span class="muted small">${fmtDate(e.date)}${e.pinned ? " 📌" : ""}</span><div class="spacer"></div>
      ${late ? `<span class="sm-flag bad">опаздывает</span>` : soon ? `<span class="sm-flag risk">скоро выход</span>` : ""}</div>
    <b>${esc(e.title || "Без названия")}</b>
    <span class="arc-label">${arc}</span>
    ${e.locked ? `<span class="muted small">Ждёт ТЗ от сценариста</span>`
      : `<span class="muted small">${e.shots ? `${e.shots} шотов · видео ${e.with_take}/${e.shots}` : "шотов пока нет"}${e.open_fixes ? ` · <span class="warn-t">правок: ${e.open_fixes}</span>` : ""}${e.redo ? ` · на перегенерацию: ${e.redo}` : ""}</span>`}`;
  return e.locked
    ? `<div class="prod-card locked" style="--c:${STATUS_COLORS[e.status]}">${body}</div>`
    : `<a class="prod-card" href="#/episodes/${e.id}/shots" style="--c:${STATUS_COLORS[e.status]}">${body}</a>`;
}

function addDays(iso, n) {
  const d = new Date(iso + "T00:00:00");
  d.setDate(d.getDate() + n);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}
