// Shared helpers: API calls, toasts, modals, formatting, statuses.
export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
export const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
export const media = (p) => `/media/${p}`;
export const view = () => $("#view");

export const state = { meta: null, assets: [] };

export const STATUS_COLORS = {
  dev: "#8b93a3", review: "#f5b642", approved: "#4d9fff", generating: "#b36bff",
  fixes: "#ff8a3d", ready: "#3ccf7e", posted: "#25d0d6",
};

export async function api(path, opts = {}) {
  const init = { ...opts };
  if (opts.json !== undefined) {
    init.body = JSON.stringify(opts.json);
    init.headers = { "Content-Type": "application/json" };
    init.method = init.method || "POST";
  }
  const r = await fetch(path, init);
  if (r.status === 401 && !path.startsWith("/api/auth/")) {
    location.hash = "#/login";
    throw new Error("auth");
  }
  if (!r.ok) {
    let msg = r.statusText;
    try { const j = await r.json(); msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail); } catch {}
    if (!opts.quiet) toast(msg, true);
    const err = new Error(msg);
    err.status = r.status;
    throw err;
  }
  return r.json();
}

export function toast(text, err = false) {
  const d = document.createElement("div");
  d.textContent = text;
  if (err) d.className = "err";
  $("#toast").append(d);
  setTimeout(() => d.remove(), err ? 7000 : 3000);
}

export function modal(html, wide = false) {
  $("#modal").classList.toggle("wide", wide);
  $("#modal-body").innerHTML = html;
  $("#modal").showModal();
  return $("#modal-body");
}
export const closeModal = () => $("#modal").close();

export function statusPill(key, small = false) {
  const st = state.meta.statuses.find((s) => s.key === key) || { name: key };
  return `<span class="status ${small ? "small" : ""}" style="--c:${STATUS_COLORS[key] || "#888"}">${esc(st.name)}</span>`;
}

export function statusSelect(current, attrs = "") {
  const allowed = allowedStatuses(current);
  return `<select class="status-select" ${attrs} ${allowed.length ? "" : "disabled"} style="--c:${STATUS_COLORS[current]}">${state.meta.statuses
    .filter((s) => s.key === current || allowed.includes(s.key))
    .map((s) => `<option value="${s.key}" ${s.key === current ? "selected" : ""}>${esc(s.name)}</option>`).join("")}</select>`;
}

export const WEEKDAYS = ["вс", "пн", "вт", "ср", "чт", "пт", "сб"];
const MONTHS_GEN = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"];
export const MONTHS = ["Январь", "Февраль", "Март", "Апрель", "Май", "Июнь", "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"];

export function fmtDate(iso, withWeekday = true) {
  if (!iso) return "";
  const d = new Date(iso + "T00:00:00");
  return `${d.getDate()} ${MONTHS_GEN[d.getMonth()]}${withWeekday ? `, ${WEEKDAYS[d.getDay()]}` : ""}`;
}
export const fmtDateTime = (iso) => (iso ? iso.replace("T", " ").slice(0, 16) : "");

export function fmtDur(sec) {
  sec = Math.round(sec || 0);
  return `${Math.floor(sec / 60)}:${String(sec % 60).padStart(2, "0")}`;
}

export function isoDate(d) {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}
export function addDays(iso, n) {
  const d = new Date(iso + "T00:00:00");
  d.setDate(d.getDate() + n);
  return isoDate(d);
}

// Mirrors the server rules in app/auth.py (the server is what actually enforces them).
export const can = {
  edit: () => ["admin", "writer"].includes(state.meta?.user?.role),   // script, shots content, library, schedule
  write: () => ["admin", "writer"].includes(state.meta?.user?.role),  // same as edit, clearer in episode code
  gen: () => ["admin", "editor"].includes(state.meta?.user?.role),    // prompts, generation, takes, re-dos
  admin: () => state.meta?.user?.role === "admin",
};

/** Statuses the current user may move an episode to from `current` (empty list = read-only). */
export function allowedStatuses(current) {
  const flow = state.meta.status_flow;
  if (flow.from && !flow.from.includes(current)) return [];
  return state.meta.statuses.map((s) => s.key).filter((k) => k !== current && (!flow.to || flow.to.includes(k)));
}

export async function loadAssets() {
  state.assets = await api("/api/assets");
  return state.assets;
}

export function fitMeter(est, max) {
  const pct = Math.min(100, (est / max) * 100);
  const cls = est > max ? "over" : est > max - 0.8 ? "tight" : "ok";
  return `<div class="fit ${cls}" title="Оценка ${est} с из ${max} с"><span style="width:${pct}%"></span></div>`;
}
