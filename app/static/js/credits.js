// Остаток кредитов Magnific в меню: аккаунт, по ключу которого работает студия. Обновляется сам, по клику — сразу.
import { $, api, can, esc } from "./core.js";

const EVERY_MS = 5 * 60 * 1000;
let last = null;       // последний ответ сервера
let timer = null;
let fetching = false;

const fmt = (n) => Math.round(n).toLocaleString("ru-RU").replace(/ /g, " ");

function paint() {
  const el = $("#credits");
  if (!el) return;
  const c = last;
  if (!c) { el.className = "credits off"; el.innerHTML = `Кредиты Magnific: загружаю…`; return; }
  const at = c.at ? c.at.slice(11, 16) : "";
  if (!c.ok) {
    el.className = "credits off";
    el.title = `${c.message || "нет данных"}\nНажмите, чтобы проверить ещё раз.`;
    el.innerHTML = `Кредиты Magnific: нет данных`;
    return;
  }
  const low = c.total ? c.available / c.total < 0.1 : c.available < 1000;
  el.className = `credits ${low ? "low" : ""}`;
  el.title = `Остаток кредитов на аккаунте Magnific${c.total ? ` (тариф ${fmt(c.total)}, потрачено ${fmt(c.spent ?? c.total - c.available)})` : ""}.\nПроверено в ${at}. Нажмите, чтобы обновить.`;
  el.innerHTML = `Кредиты Magnific<br><b>${fmt(c.available)}</b>${c.total ? ` <span class="muted">из ${fmt(c.total)}</span>` : ""}
    ${c.total ? `<div class="cbar"><span style="width:${Math.max(0, Math.min(100, (c.available / c.total) * 100))}%"></span></div>` : ""}`;
}

async function load(refresh = false) {
  if (fetching || !can.gen()) return;
  fetching = true;
  try { last = await api(`/api/magnific/credits${refresh ? "?refresh=true" : ""}`, { quiet: true }); } catch { last = last || { ok: false, message: "Не удалось связаться со студией" }; }
  fetching = false;
  paint();
}

/** Нарисовать плашку (меню перерисовывается при каждом переходе) и запустить обновление раз в 5 минут. */
export function startCredits() {
  const el = $("#credits");
  if (!el || !can.gen()) return;
  el.onclick = () => { last = null; paint(); load(true); };
  paint();
  if (!last || Date.now() - (last._t || 0) > EVERY_MS) load().then(() => { if (last) last._t = Date.now(); });
  clearInterval(timer);
  timer = setInterval(() => { if (!document.hidden) load().then(() => { if (last) last._t = Date.now(); }); }, EVERY_MS);
}

export function stopCredits() { clearInterval(timer); last = null; }
