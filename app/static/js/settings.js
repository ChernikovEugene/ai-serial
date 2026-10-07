// Settings (Veo, breakdown) and team accounts.
import { $, $$, api, can, closeModal, esc, fmtDateTime, modal, state, toast, view } from "./core.js";

export async function renderSettings() {
  const [s, users] = await Promise.all([api("/api/settings"), api("/api/users")]);
  const admin = can.admin();
  const ro = admin ? "" : "disabled";
  const field = (k, label, type = "input", extra = "") => type === "textarea"
    ? `<label>${label}</label><textarea data-k="${k}" rows="3" ${ro}>${esc(s[k])}</textarea>`
    : `<label>${label}</label><input data-k="${k}" value="${esc(s[k])}" ${extra} ${ro}>`;
  view().innerHTML = `
    <h1>Настройки</h1>
    ${admin ? "" : `<p class="muted">Менять настройки может только администратор.</p>`}
    <div class="two-col">
      <div class="card">
        <h2 style="margin-top:0">Veo 3</h2>
        <label>Режим</label>
        <select data-k="veo_provider" ${ro}>
          <option value="stub" ${s.veo_provider === "stub" ? "selected" : ""}>Заглушка — ничего не отправлять, сохранять запрос в файл</option>
          <option value="gemini" ${s.veo_provider === "gemini" ? "selected" : ""}>Gemini API — реальная генерация (платно)</option></select>
        ${field("veo_api_key", "API-ключ Google AI Studio (хранится только у вас в data/studio.db)", "input", `type="password" placeholder="AIza…" autocomplete="off"`)}
        ${field("veo_model", "Модель (фото-референсы есть только у Veo 3.1)")}
        <div class="row"><div style="flex:1">${field("aspect_ratio", "Соотношение сторон")}</div>
          <div style="flex:1">${field("resolution", "Разрешение (720p / 1080p)")}</div>
          <div style="flex:1">${field("veo_parallel", "Одновременных генераций")}</div></div>
        <p class="muted small">Число одновременных генераций применяется после перезапуска приложения.</p>
      </div>
      <div class="card">
        <h2 style="margin-top:0">Разбивка и промпты</h2>
        ${field("style", "Общий стиль сериала (в начале каждого промпта)", "textarea")}
        ${field("negative_prompt", "Негативный промпт по умолчанию", "textarea")}
        <div class="row"><div style="flex:1">${field("dialogue_language", "Язык реплик")}</div>
          <div style="flex:1">${field("words_per_second", "Темп речи, слов/с")}</div>
          <div style="flex:1">${field("max_shot_seconds", "Лимит шота, с")}</div></div>
      </div>
    </div>
    <div class="card" style="margin-top:14px">
      <h2 style="margin-top:0">GitHub (кнопка «Правка»)</h2>
      <p class="muted small">Правки хранятся в задачах (Issues) репозитория на GitHub. Если на компьютере выполнен вход в GitHub CLI
        (<code>gh auth login</code>), токен не нужен.</p>
      ${field("github_token", "Токен GitHub (хранится только у вас в data/studio.db)", "input", `type="password" placeholder="ghp_…" autocomplete="off"`)}
    </div>
    ${admin ? `<div class="row" style="margin-top:14px"><div class="spacer"></div><button class="primary" id="save">Сохранить настройки</button></div>` : ""}

    <h2>Команда</h2>
    <div class="card">
      <table class="atable users"><thead><tr><th>Имя</th><th>Логин</th><th>Роль</th><th>Добавлен</th><th></th></tr></thead><tbody>
      ${users.map((u) => `<tr><td>${esc(u.name)}${u.id === state.meta.user.id ? " <span class='muted small'>(вы)</span>" : ""}</td><td>${esc(u.login)}</td>
        <td>${admin ? `<select data-role="${u.id}" style="width:auto">${Object.entries(state.meta.roles).map(([k, n]) => `<option value="${k}" ${u.role === k ? "selected" : ""}>${esc(n)}</option>`).join("")}</select>` : esc(state.meta.roles[u.role])}</td>
        <td class="muted small">${fmtDateTime(u.created_at)}</td>
        <td>${admin || u.id === state.meta.user.id ? `<button class="ghost small" data-pass="${u.id}">Сменить пароль</button>` : ""}
          ${admin && u.id !== state.meta.user.id ? `<button class="ghost small danger" data-deluser="${u.id}">Удалить</button>` : ""}</td></tr>`).join("")}
      </tbody></table>
      ${admin ? `<div class="row" style="margin-top:12px"><button id="add-user">+ Добавить человека</button>
        <span class="muted small">Сейчас приложение доступно только на этом компьютере. Чтобы зашли другие люди, его нужно будет разместить на сервере — аккаунты уже готовы к этому.</span></div>` : ""}
    </div>`;

  if (admin) {
    $("#save").onclick = async () => {
      const values = Object.fromEntries($$("[data-k]").map((i) => [i.dataset.k, i.value]));
      await api("/api/settings", { method: "PUT", json: values });
      state.meta = await api("/api/meta");
      toast("Настройки сохранены"); renderSettings();
    };
    $("#add-user").onclick = () => userDialog();
    $$("[data-role]").forEach((sel) => (sel.onchange = async () => {
      try { await api(`/api/users/${sel.dataset.role}`, { method: "PUT", json: { role: sel.value } }); toast("Роль изменена"); }
      finally { renderSettings(); }
    }));
    $$("[data-deluser]").forEach((b) => (b.onclick = async () => {
      if (!confirm("Удалить пользователя? Его комментарии останутся.")) return;
      await api(`/api/users/${b.dataset.deluser}`, { method: "DELETE" }); renderSettings();
    }));
  }
  $$("[data-pass]").forEach((b) => (b.onclick = () => {
    const m = modal(`<h1>Новый пароль</h1><input id="np" type="password" autocomplete="new-password">
      <div class="row" style="margin-top:14px"><div class="spacer"></div><button class="ghost" id="cancel">Отмена</button><button class="primary" id="ok">Сохранить</button></div>`);
    $("#cancel", m).onclick = closeModal;
    $("#ok", m).onclick = async () => { await api(`/api/users/${b.dataset.pass}`, { method: "PUT", json: { password: $("#np", m).value } }); closeModal(); toast("Пароль изменён"); };
  }));
}

function userDialog() {
  const m = modal(`<h1>Новый участник</h1>
    <label>Имя</label><input id="u-name">
    <label>Логин</label><input id="u-login" autocomplete="off">
    <label>Пароль (от 4 символов)</label><input id="u-pass" type="password" autocomplete="new-password">
    <label>Роль</label><select id="u-role">${Object.entries(state.meta.roles).map(([k, n]) => `<option value="${k}" ${k === "writer" ? "selected" : ""}>${esc(n)}</option>`).join("")}</select>
    <div class="row" style="margin-top:14px"><div class="spacer"></div><button class="ghost" id="cancel">Отмена</button><button class="primary" id="ok">Добавить</button></div>`);
  $("#cancel", m).onclick = closeModal;
  $("#ok", m).onclick = async () => {
    await api("/api/users", { json: { name: $("#u-name", m).value, login: $("#u-login", m).value, password: $("#u-pass", m).value, role: $("#u-role", m).value } });
    closeModal(); toast("Участник добавлен"); renderSettings();
  };
}

export function renderPublish() {
  view().innerHTML = `<h1>Публикация в TikTok</h1>
    <div class="card"><p>Следующий этап: сборка серии из выбранных дублей в один ролик и публикация в аккаунт TikTok через TikTok Content Posting API,
    со статусом «Опубликовано» автоматически.</p>
    <p class="muted">Сейчас готовые видео лежат в папке <code>data/media/renders</code>, а серию целиком можно посмотреть во вкладке «Просмотр и правки».</p></div>`;
}
