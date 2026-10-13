// Значки в меню и уведомления. Раз в 20 с спрашиваем сервер: сколько срочных серий (🔥 у «Продакшна», видят все),
// какие серии сгенерировались (точка в цвет статуса у «Продакшна» и звук — нейронщику), сколько новых заявок (админу).
import { $, api, esc, state, STATUS_COLORS, toast } from "./core.js";

export const badges = { urgent: 0, notifications: [], reports: 0 };
let timer = null;
let known = null; // id уведомлений, о которых уже прозвучал сигнал

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
    drawBadges();
    timer = setTimeout(tick, 20000);
  };
  tick();
}

export function stopBadges() { clearTimeout(timer); timer = null; known = null; }

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
function chime() {
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

export const isEditor = () => state.meta?.user?.role === "editor";
