// Settings (Veo, breakdown, GitHub) and the publish stub. The team lives in team.js.
import { $, $$, api, can, esc, state, toast, view } from "./core.js";

/** День выхода серии № 1 по точке отсчёта (anchor_number выходит anchor_date). */
function firstDay(m) {
  const d = new Date(m.anchor_date + "T00:00:00");
  d.setDate(d.getDate() + 1 - m.anchor_number);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

export async function renderSettings() {
  const s = await api("/api/settings");
  const admin = can.admin();
  const ro = admin ? "" : "disabled";
  const field = (k, label, type = "input", extra = "") => type === "textarea"
    ? `<label>${label}</label><textarea data-k="${k}" rows="3" ${ro}>${esc(s[k])}</textarea>`
    : `<label>${label}</label><input data-k="${k}" value="${esc(s[k])}" ${extra} ${ro}>`;
  view().innerHTML = `
    <h1>Настройки</h1>
    ${admin ? "" : `<p class="muted">Менять настройки может только администратор.</p>`}
    <div class="card" style="margin-bottom:14px">
      <h2 style="margin-top:0">Проект</h2>
      ${field("series_title", "Название сериала (стоит в PDF для клиента)")}
      <label>Первая серия сериала выходит</label>
      <div class="row"><input id="anchor-d" type="date" value="${firstDay(state.meta)}" style="width:180px" ${can.write() ? "" : "disabled"}>
        ${can.write() ? `<button id="anchor-save">Применить</button>` : ""}
        <span class="muted small">дальше по одной серии в день; закреплённые за датой (📌) серии не сдвигаются</span></div>
    </div>
    <div class="card">
        <h2 style="margin-top:0">Подключение к нейросети (Veo)</h2>
        <p class="muted small">Формат, модель, стиль и шаблоны промптов — во вкладке «Тех. требования».</p>
        <label>Режим</label>
        <select data-k="veo_provider" ${ro}>
          <option value="stub" ${s.veo_provider === "stub" ? "selected" : ""}>Заглушка — ничего не отправлять, сохранять запрос в файл</option>
          <option value="gemini" ${s.veo_provider === "gemini" ? "selected" : ""}>Gemini API — реальная генерация (платно)</option></select>
        ${field("veo_api_key", "API-ключ Google AI Studio (хранится только у вас в data/studio.db)", "input", `type="password" placeholder="AIza…" autocomplete="off"`)}
        <div style="max-width:260px">${field("veo_parallel", "Одновременных генераций")}</div>
        <p class="muted small">Число одновременных генераций применяется после перезапуска приложения.</p>
    </div>
    <div class="card" style="margin-top:14px">
      <h2 style="margin-top:0">GitHub (кнопка «Правка»)</h2>
      <p class="muted small">Правки хранятся в задачах (Issues) репозитория на GitHub. Если на компьютере выполнен вход в GitHub CLI
        (<code>gh auth login</code>), токен не нужен.</p>
      ${field("github_token", "Токен GitHub (хранится только у вас в data/studio.db)", "input", `type="password" placeholder="ghp_…" autocomplete="off"`)}
    </div>
    ${admin ? `<div class="row" style="margin-top:14px"><div class="spacer"></div><button class="primary" id="save">Сохранить настройки</button></div>` : ""}`;

  if (admin) {
    $("#save").onclick = async () => {
      const values = Object.fromEntries($$("[data-k]").map((i) => [i.dataset.k, i.value]));
      await api("/api/settings", { method: "PUT", json: values });
      state.meta = await api("/api/meta");
      toast("Настройки сохранены"); renderSettings();
    };
  }
  $("#anchor-save") && ($("#anchor-save").onclick = async () => {
    await api("/api/schedule/anchor", { method: "PUT", json: { number: 1, date: $("#anchor-d").value } });
    state.meta = await api("/api/meta");
    toast("Дата старта обновлена: даты серий пересчитаны");
  });
}

export function renderPublish() {
  view().innerHTML = `<h1>Публикация в TikTok</h1>
    <div class="card"><p>Следующий этап: сборка серии из выбранных дублей в один ролик и публикация в аккаунт TikTok через TikTok Content Posting API,
    со статусом «Опубликовано» автоматически.</p>
    <p class="muted">Сейчас готовые видео лежат в папке <code>data/media/renders</code>, а серию целиком можно посмотреть во вкладке «Просмотр и правки».</p></div>`;
}
