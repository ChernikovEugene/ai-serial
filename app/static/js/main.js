// Router, navigation and login.
import { $, $$, api, can, esc, state, toast, view } from "./core.js";
import { renderQueue } from "./queue.js";
import { renderCalendar } from "./calendar.js";
import { renderEpisode, stopEpisodePolling } from "./episode.js";
import { renderAssets, renderAsset } from "./assets.js";
import { renderSettings, renderPublish } from "./settings.js";
import { renderStatus, stopStatusPolling } from "./status.js";
import { initFeedback, stopFeedback } from "./feedback.js";

let lastHash = location.hash;

async function route() {
  if (window.__scriptDirty?.() && !confirm("Сценарий не сохранён. Уйти без сохранения?")) {
    history.replaceState(null, "", lastHash);
    return;
  }
  window.__scriptDirty = null;
  window.onbeforeunload = null;
  lastHash = location.hash;
  stopEpisodePolling();
  stopStatusPolling();
  const [, section, id, sub] = location.hash.split("/");
  if (section === "login") return renderLogin();
  if (!state.meta?.user) {
    try { state.meta = await api("/api/meta"); } catch { return; }
  }
  drawNav(section || "queue");
  try {
    if (!section || section === "queue") return await renderQueue();
    if (section === "calendar") return await renderCalendar();
    if (section === "episodes") return id ? await renderEpisode(+id, sub || (can.gen() && !can.write() ? "shots" : "script")) : await renderQueue();
    if (section === "characters") return id ? await renderAsset(+id) : await renderAssets("character");
    if (section === "locations") return id ? await renderAsset(+id) : await renderAssets("location");
    if (section === "status") return await renderStatus();
    if (section === "settings") return await renderSettings();
    if (section === "publish") return renderPublish();
  } catch (e) { console.error(e); }
}

function drawNav(section) {
  const active = section === "episodes" ? "queue" : section;
  const u = state.meta.user;
  $("#nav").hidden = false;
  $("#nav").innerHTML = `
    <div class="brand">🎬 Студия</div>
    <a href="#/queue" data-nav="queue">Серии</a>
    <a href="#/calendar" data-nav="calendar">Календарь</a>
    <a href="#/characters" data-nav="characters">Персонажи</a>
    <a href="#/locations" data-nav="locations">Локации</a>
    <a href="#/status" data-nav="status">Статус</a>
    <a href="#/settings" data-nav="settings">Настройки и команда</a>
    <a href="#/publish" data-nav="publish">Публикация</a>
    <div class="nav-foot">
      Veo: ${state.meta.veo_provider === "gemini" ? `<span class="badge ok">API</span>` : `<span class="badge warn">заглушка</span>`}
      <div class="me"><b>${esc(u.name)}</b><br><span class="muted small">${esc(state.meta.roles[u.role])}</span></div>
      <button class="ghost small" id="logout">Выйти</button>
    </div>`;
  $$("#nav a").forEach((a) => a.classList.toggle("active", a.dataset.nav === active));
  $("#logout").onclick = async () => {
    await api("/api/auth/logout", { json: {} });
    state.meta = null;
    stopFeedback();
    location.hash = "#/login";
  };
  initFeedback();
}

async function renderLogin() {
  $("#nav").hidden = true;
  const st = await api("/api/auth/state");
  if (st.user) { state.meta = null; location.hash = "#/queue"; return; }
  const setup = !st.has_users;
  view().innerHTML = `<div class="login card">
    <h1>🎬 Студия сериала</h1>
    <p class="muted">${setup ? "Первый запуск: создайте свой аккаунт администратора. Потом в «Настройки и команда» можно добавить других людей." : "Войдите в аккаунт"}</p>
    ${setup ? `<label>Ваше имя</label><input id="l-name" placeholder="Егор">` : ""}
    <label>Логин</label><input id="l-login" autocomplete="username">
    <label>Пароль</label><input id="l-pass" type="password" autocomplete="${setup ? "new-password" : "current-password"}">
    <button class="primary" id="l-go" style="width:100%;margin-top:16px">${setup ? "Создать и войти" : "Войти"}</button></div>`;
  const go = async () => {
    const body = { login: $("#l-login").value, password: $("#l-pass").value, name: setup ? $("#l-name").value : "" };
    await api(setup ? "/api/auth/setup" : "/api/auth/login", { json: body });
    state.meta = null;
    location.hash = "#/queue";
  };
  $("#l-go").onclick = go;
  $("#l-pass").addEventListener("keydown", (e) => e.key === "Enter" && go());
  ($("#l-name") || $("#l-login")).focus();
}

window.addEventListener("hashchange", route);
window.addEventListener("unhandledrejection", (e) => { if (e.reason?.message !== "auth") console.error(e.reason); });
if (!location.hash || location.hash === "#/") location.hash = "#/queue"; else route();
