// Значки в меню и уведомления. Раз в 20 с спрашиваем сервер: сколько срочных серий (🔥 у «Продакшна», видят все),
// какие серии сгенерировались (точка в цвет статуса у «Продакшна» и звук — нейронщику), сколько новых заявок (админу).
import { $, api, can, esc, STATUS_COLORS, toast } from "./core.js";

export const badges = { urgent: 0, notifications: [], reports: 0, tray: [], tray_unseen: 0 };
let timer = null;
let known = null; // id уведомлений, о которых уже прозвучал сигнал
let trayKnown = null; // id событий трея, о которых уже было сообщение

export function startBadges() {
  if (timer) return;
  const tick = async () => {
    try { Object.assign(badges, await api("/api/badges", { quiet: true })); } catch { /* сеть или выход */ }
    const ids = badges.notifications.map((n) => n.id);
    if (known) {
      const fresh = badges.notifications.filter((n) => !known.has(n.id));
      if (fresh.length) {
        chime();
        fresh.forEach((n) => toast(`✓ ${n.text}`));
      }
    }
    known = new Set(ids);
    const fresh = (badges.tray || []).filter((n) => !n.seen && trayKnown && !trayKnown.has(n.id) && !known.has(n.id));
    if (fresh.length) {
      chime();
      fresh.slice(0, 3).forEach((n) => toast(`🔔 ${n.text}`));
    }
    trayKnown = new Set((badges.tray || []).map((n) => n.id));
    drawBadges();
    drawTray();
    timer = setTimeout(tick, 20000);
  };
  tick();
}

export function stopBadges() { clearTimeout(timer); timer = null; known = null; trayKnown = null; $("#tray")?.remove(); }

// ---------- трей уведомлений (колокольчик справа вверху) ----------
const KIND_ICON = { writer_edit: "✏️", episode_new: "🆕", generated: "🎬", announce: "📣" };
let trayOpen = false;

function ago(iso) {
  const t = new Date(iso).getTime();
  if (!t) return "";
  const m = Math.max(0, Math.round((Date.now() - t) / 60000));
  if (m < 1) return "только что";
  if (m < 60) return `${m} мин назад`;
  if (m < 1440) return `${Math.round(m / 60)} ч назад`;
  return new Date(iso).toLocaleDateString("ru-RU", { day: "numeric", month: "short" });
}

function trayEl() {
  let el = $("#tray");
  if (!el) {
    el = document.createElement("div");
    el.id = "tray";
    document.body.append(el);
    document.addEventListener("click", (e) => { if (trayOpen && !e.target.closest("#tray")) { trayOpen = false; drawTray(); } });
  }
  return el;
}

export function drawTray() {
  const el = trayEl();
  const items = badges.tray || [];
  const unseen = badges.tray_unseen || 0;
  const draft = $(".tray-announce textarea", el)?.value || ""; // набранное объявление не теряем при обновлении раз в 20 с
  el.innerHTML = `<button class="tray-btn ${unseen ? "has" : ""}" type="button" aria-label="Уведомления" title="Уведомления">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9"/><path d="M10.3 21a1.94 1.94 0 0 0 3.4 0"/></svg>
      ${unseen ? `<span class="tray-count">${unseen > 99 ? "99+" : unseen}</span>` : ""}</button>
    ${trayOpen ? `<div class="tray-panel">
      <div class="tray-head"><b>Уведомления</b>${unseen ? `<button class="ghost small" data-tray-all>Отметить прочитанными</button>` : ""}</div>
      <div class="tray-list">${items.length ? items.map((n) => `<div class="tray-item ${n.seen ? "" : "new"}" data-tray="${n.id}" ${n.episode_id ? `data-ep="${n.episode_id}"` : ""}>
          <span class="tray-ic">${KIND_ICON[n.kind] || "🔔"}</span>
          <div><div>${esc(n.text)}</div><div class="muted small">${esc(ago(n.created_at))}</div></div></div>`).join("")
        : `<div class="muted small" style="padding:14px">Пока ничего нового.</div>`}</div>
      ${can.admin() ? `<div class="tray-announce"><textarea rows="2" maxlength="500" placeholder="Объявить команде: что нового или важного на сайте"></textarea>
        <button class="small primary" data-tray-say>Отправить всем</button></div>` : ""}</div>` : ""}`;
  if (draft) $(".tray-announce textarea", el).value = draft;
  $(".tray-btn", el).onclick = (e) => { e.stopPropagation(); trayOpen = !trayOpen; drawTray(); };
  if (!trayOpen) return;
  $("[data-tray-all]", el)?.addEventListener("click", async () => {
    await api("/api/notifications/seen", { json: { all: true } });
    badges.tray.forEach((n) => (n.seen = true));
    badges.tray_unseen = 0;
    drawTray();
  });
  $$tray("[data-tray]", el).forEach((row) => (row.onclick = async () => {
    const id = +row.dataset.tray;
    const n = badges.tray.find((x) => x.id === id);
    if (n && !n.seen) {
      n.seen = true;
      badges.tray_unseen = Math.max(0, badges.tray_unseen - 1);
      try { await api("/api/notifications/seen", { json: { ids: [id] }, quiet: true }); } catch { /* не страшно */ }
    }
    trayOpen = false;
    drawTray();
    if (row.dataset.ep) location.hash = `#/episodes/${row.dataset.ep}/${n?.kind === "writer_edit" ? "shots" : "main"}`;
  }));
  $("[data-tray-say]", el)?.addEventListener("click", async () => {
    const ta = $("textarea", el);
    if (!ta.value.trim()) return;
    await api("/api/announce", { json: { text: ta.value } });
    toast("Объявление отправлено команде");
    ta.value = "";
    try { Object.assign(badges, await api("/api/badges", { quiet: true })); } catch { /* не страшно */ }
    drawTray();
  });
}
const $$tray = (sel, el) => [...el.querySelectorAll(sel)];

/** Рисует значки рядом с пунктами меню (вызывается и после каждой перерисовки меню). */
export function drawBadges() {
  const prod = $('#nav a[data-nav="production"]');
  if (prod) {
    const color = STATUS_COLORS[badges.notifications[0]?.ep_status] || STATUS_COLORS.fixes;
    prod.querySelector(".nav-badges")?.remove();
    const n = badges.notifications.length;
    if (badges.urgent || n) {
      prod.insertAdjacentHTML("beforeend", `<span class="nav-badges">
        ${badges.urgent ? `<span class="nav-urgent" title="Срочных серий: ${badges.urgent}">🔥${badges.urgent > 1 ? badges.urgent : ""}</span>` : ""}
        ${n ? `<span class="nav-dot" style="--c:${color}" title="${esc(badges.notifications.map((x) => x.text).join("\n"))}">${n}</span>` : ""}</span>`);
    }
  }
  const rep = $('#nav a[data-nav="reports"]');
  if (rep) {
    rep.querySelector(".nav-badges")?.remove();
    if (badges.reports) rep.insertAdjacentHTML("beforeend", `<span class="nav-badges"><span class="nav-dot" style="--c:${STATUS_COLORS.review}">${badges.reports}</span></span>`);
  }
}

/** Серию открыли — уведомления о ней прочитаны. */
export async function seenEpisode(eid) {
  if (!badges.notifications.some((n) => n.episode_id === eid)) return;
  badges.notifications = badges.notifications.filter((n) => n.episode_id !== eid);
  drawBadges();
  try { await api("/api/notifications/seen", { json: { episode_id: eid }, quiet: true }); } catch { /* не страшно */ }
}

export const generatedIds = () => new Set(badges.notifications.map((n) => n.episode_id));

/** Короткий мягкий сигнал из двух нот (без файла). Браузер играет звук, только если вкладка студии открыта. */
export function chime() {
  try {
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    [[880, 0], [1320, 0.16]].forEach(([f, t]) => {
      const o = ctx.createOscillator();
      const g = ctx.createGain();
      o.type = "sine";
      o.frequency.value = f;
      g.gain.setValueAtTime(0.0001, ctx.currentTime + t);
      g.gain.exponentialRampToValueAtTime(0.25, ctx.currentTime + t + 0.02);
      g.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + t + 0.45);
      o.connect(g).connect(ctx.destination);
      o.start(ctx.currentTime + t);
      o.stop(ctx.currentTime + t + 0.5);
    });
  } catch { /* звук недоступен */ }
}
