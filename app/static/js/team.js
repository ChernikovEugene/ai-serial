// «Команда»: кто на проекте, чем занят, как связаться (ник в Telegram) и с какой ролью. Аккаунты и пароли.
import { $, $$, api, closeModal, esc, fmtDateTime, modal, state, toast, view } from "./core.js";

const tgLink = (tg) => (tg ? `<a href="https://t.me/${encodeURIComponent(tg)}" target="_blank" rel="noopener">@${esc(tg)}</a>` : `<span class="muted">не указан</span>`);

export async function renderTeam() {
  const users = await api("/api/users");
  const me = state.meta.user;
  const admin = me.role === "admin";
  view().innerHTML = `
    <div class="row"><h1>Команда</h1><div class="spacer"></div>${admin ? `<button class="primary" id="add-user">+ Добавить человека</button>` : ""}</div>
    <div class="role-grid">
      ${["writer", "editor", "admin"].map((r) => {
        const people = users.filter((u) => u.role === r);
        return `<div class="card role-card">
          <h2>${esc(state.meta.roles[r])}</h2>
          <ul>${(state.meta.role_duties[r] || []).map((d) => `<li>${esc(d)}</li>`).join("")}</ul>
          <div class="role-people">${people.length ? people.map((u) => `<span class="badge">${esc(u.name)}</span>`).join("") : `<span class="muted small">пока никого — «+ Добавить человека»</span>`}</div>
        </div>`;
      }).join("")}
    </div>
    <h2>Люди</h2>
    <div class="card">
      <table class="atable users"><thead><tr><th>Имя</th><th>Роль</th><th>Задачи на проекте</th><th>Telegram</th><th>Логин</th><th></th></tr></thead><tbody>
      ${users.map((u) => `<tr>
        <td><b>${esc(u.name)}</b>${u.id === me.id ? " <span class='muted small'>(вы)</span>" : ""}</td>
        <td>${admin ? `<select data-role="${u.id}" style="width:auto">${Object.entries(state.meta.roles).map(([k, n]) => `<option value="${k}" ${u.role === k ? "selected" : ""}>${esc(n)}</option>`).join("")}</select>` : esc(state.meta.roles[u.role])}</td>
        <td>${u.tasks ? esc(u.tasks) : `<span class="muted">не указаны</span>`}</td>
        <td>${tgLink(u.tg)}</td>
        <td class="muted small">${esc(u.login)}<br>${fmtDateTime(u.created_at)}</td>
        <td>${admin || u.id === me.id ? `<button class="ghost small" data-edit="${u.id}">Изменить</button> <button class="ghost small" data-pass="${u.id}">Сменить пароль</button>` : ""}
          ${admin && u.id !== me.id ? `<button class="ghost small danger" data-deluser="${u.id}">Удалить</button>` : ""}</td></tr>`).join("")}
      </tbody></table>
      ${admin ? `<p class="muted small" style="margin-top:12px">Сейчас приложение доступно только на этом компьютере. Чтобы зашли другие люди, его нужно будет разместить на сервере — аккаунты уже готовы к этому.</p>` : ""}
    </div>`;

  $("#add-user") && ($("#add-user").onclick = () => userDialog());
  $$("[data-role]").forEach((sel) => (sel.onchange = async () => {
    try { await api(`/api/users/${sel.dataset.role}`, { method: "PUT", json: { role: sel.value } }); toast("Роль изменена"); }
    finally { renderTeam(); }
  }));
  $$("[data-deluser]").forEach((b) => (b.onclick = async () => {
    if (!confirm("Удалить пользователя? Его комментарии останутся.")) return;
    await api(`/api/users/${b.dataset.deluser}`, { method: "DELETE" }); renderTeam();
  }));
  $$("[data-edit]").forEach((b) => (b.onclick = () => editDialog(users.find((u) => u.id === +b.dataset.edit), admin)));
  $$("[data-pass]").forEach((b) => (b.onclick = () => {
    const m = modal(`<h1>Новый пароль</h1><input id="np" type="password" autocomplete="new-password">
      <div class="row" style="margin-top:14px"><div class="spacer"></div><button class="ghost" id="cancel">Отмена</button><button class="primary" id="ok">Сохранить</button></div>`);
    $("#cancel", m).onclick = closeModal;
    $("#ok", m).onclick = async () => { await api(`/api/users/${b.dataset.pass}`, { method: "PUT", json: { password: $("#np", m).value } }); closeModal(); toast("Пароль изменён"); };
  }));
}

function editDialog(u, admin) {
  const m = modal(`<h1>${esc(u.name)}</h1>
    <label>Имя</label><input id="e-name" value="${esc(u.name)}">
    <label>Ник в Telegram (для связи)</label><input id="e-tg" value="${esc(u.tg)}" placeholder="@ник">
    ${admin ? `<label>Задачи на проекте</label><textarea id="e-tasks" rows="3" placeholder="Например: пишет сценарии, ведёт календарь">${esc(u.tasks)}</textarea>` : ""}
    <div class="row" style="margin-top:14px"><div class="spacer"></div><button class="ghost" id="cancel">Отмена</button><button class="primary" id="ok">Сохранить</button></div>`);
  $("#cancel", m).onclick = closeModal;
  $("#ok", m).onclick = async () => {
    const json = { name: $("#e-name", m).value, tg: $("#e-tg", m).value };
    if (admin) json.tasks = $("#e-tasks", m).value;
    await api(`/api/users/${u.id}`, { method: "PUT", json });
    closeModal(); toast("Сохранено"); renderTeam();
  };
}

function userDialog() {
  const m = modal(`<h1>Новый участник</h1>
    <label>Имя</label><input id="u-name">
    <label>Логин</label><input id="u-login" autocomplete="off">
    <label>Пароль (от 4 символов)</label><input id="u-pass" type="password" autocomplete="new-password">
    <label>Роль</label><select id="u-role">${Object.entries(state.meta.roles).map(([k, n]) => `<option value="${k}" ${k === "writer" ? "selected" : ""}>${esc(n)}</option>`).join("")}</select>
    <label>Ник в Telegram (для связи)</label><input id="u-tg" placeholder="@ник">
    <label>Задачи на проекте</label><textarea id="u-tasks" rows="2"></textarea>
    <div class="row" style="margin-top:14px"><div class="spacer"></div><button class="ghost" id="cancel">Отмена</button><button class="primary" id="ok">Добавить</button></div>`);
  $("#cancel", m).onclick = closeModal;
  $("#ok", m).onclick = async () => {
    await api("/api/users", { json: { name: $("#u-name", m).value, login: $("#u-login", m).value, password: $("#u-pass", m).value,
      role: $("#u-role", m).value, tg: $("#u-tg", m).value, tasks: $("#u-tasks", m).value } });
    closeModal(); toast("Участник добавлен"); renderTeam();
  };
}
