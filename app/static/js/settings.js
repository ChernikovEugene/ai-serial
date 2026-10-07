// Settings (Veo, breakdown, GitHub) and the publish stub. The team lives in team.js.
import { $, $$, api, can, esc, state, toast, view } from "./core.js";

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
    </div>
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
    ${admin ? `<div class="row" style="margin-top:14px"><div class="spacer"></div><button class="primary" id="save">Сохранить настройки</button></div>` : ""}`;

  if (admin) {
    $("#save").onclick = async () => {
      const values = Object.fromEntries($$("[data-k]").map((i) => [i.dataset.k, i.value]));
      await api("/api/settings", { method: "PUT", json: values });
      state.meta = await api("/api/meta");
      toast("Настройки сохранены"); renderSettings();
    };
  }
}

export function renderPublish() {
  view().innerHTML = `<h1>Публикация в TikTok</h1>
    <div class="card"><p>Следующий этап: сборка серии из выбранных дублей в один ролик и публикация в аккаунт TikTok через TikTok Content Posting API,
    со статусом «Опубликовано» автоматически.</p>
    <p class="muted">Сейчас готовые видео лежат в папке <code>data/media/renders</code>, а серию целиком можно посмотреть во вкладке «Просмотр и правки».</p></div>`;
}
