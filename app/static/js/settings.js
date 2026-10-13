// Settings (video generation via Magnific, breakdown, GitHub) and the publish stub. The team lives in team.js.
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
      <div class="row"><input id="anchor-d" type="date" value="${firstDay(state.meta)}" style="width:180px" ${can.schedule() ? "" : "disabled"}>
        ${can.schedule() ? `<button id="anchor-save">Применить</button>` : ""}
        <span class="muted small">дальше по одной серии в день; закреплённые за датой (📌) серии не сдвигаются</span></div>
    </div>
    <div class="card">
        <h2 style="margin-top:0">Генерация видео (Magnific API)</h2>
        <p class="muted small">Все нейросети, кроме «Другой», уходят через Magnific (бывший Freepik API): Veo 3.1, Kling 3 Omni, Seedance 2.0,
          Hailuo 3, WAN 2.7, Runway Gen-4.5. Нейросеть выбирается в карточке шота. Формат, стиль и шаблоны промптов — во вкладке «Тех. требования».</p>
        <label>Режим</label>
        <select data-k="gen_mode" ${ro}>
          <option value="stub" ${s.gen_mode === "stub" ? "selected" : ""}>Заглушка — ничего не отправлять, сохранять запрос в файл</option>
          <option value="live" ${s.gen_mode === "live" ? "selected" : ""}>Реальная генерация (платно, тратит кредиты)</option></select>
        ${field("magnific_api_key", "API-ключ Magnific (кабинет Magnific → API Keys; хранится только у вас в data/studio.db)", "input", `type="password" placeholder="FPSX…" autocomplete="off"`)}
        <label>Разрешение</label>
        <select data-k="magnific_resolution" ${ro} style="max-width:260px">
          <option value="480p" ${s.magnific_resolution === "480p" ? "selected" : ""}>480p</option>
          <option value="720p" ${s.magnific_resolution === "720p" ? "selected" : ""}>720p</option>
          <option value="1080p" ${s.magnific_resolution === "1080p" ? "selected" : ""}>1080p</option>
          <option value="4k" ${s.magnific_resolution === "4k" ? "selected" : ""}>4k</option></select>
        <p class="muted small">720p дешевле и подходит для черновиков. Если модель не умеет выбранное, берётся ближайшее (Hailuo 3 всегда 768p).</p>
        ${field("public_base_url", "Публичный адрес студии (например, https://studio.onrender.com)", "input", `placeholder="пусто: студия на ноутбуке"`)}
        <p class="muted small">Kling 3 Omni, WAN 2.7 и Veo 3.1 с референсами принимают картинки только ссылкой. Когда студия доступна из интернета,
          укажите её адрес: картинки уйдут по защищённой ссылке. Без адреса с ноутбука работают Seedance 2.0, Hailuo 3, Runway и Veo без референсов.</p>
        <label>Veo 3.1 отправлять</label>
        <select data-k="veo_route" ${ro}>
          <option value="magnific" ${s.veo_route === "magnific" ? "selected" : ""}>через Magnific (ключ выше)</option>
          <option value="google" ${s.veo_route === "google" ? "selected" : ""}>напрямую через Google (Gemini API, свой ключ)</option></select>
        ${field("veo_api_key", "API-ключ Google AI Studio (только для Veo напрямую через Google)", "input", `type="password" placeholder="AIza…" autocomplete="off"`)}
        <div style="max-width:260px">${field("veo_parallel", "Одновременных генераций")}</div>
        <p class="muted small">Число одновременных генераций применяется после перезапуска приложения. Готовое видео скачивается в студию сразу:
          ссылка Magnific на ролик живёт 30 минут.</p>
    </div>
    <div class="card" style="margin-top:14px">
      <h2 style="margin-top:0">GitHub (отправка заявок в правки Claude)</h2>
      <p class="muted small">Кнопка «Отправить в правки Claude» на вкладке «Заявки» создаёт задачу в репозитории на GitHub.
        Для этого сайту нужен токен с доступом только к этому репозиторию: на GitHub → Settings → Developer settings →
        Fine-grained tokens, репозиторий ai-serial, права Issues и Contents — «Read and write».</p>
      ${field("github_token", "Токен GitHub (хранится только на сервере студии)", "input", `type="password" placeholder="github_pat_…" autocomplete="off"`)}
    </div>
    ${admin ? `<div class="card" style="margin-top:14px" id="integrations"></div>` : ""}
    ${admin ? `<div class="row" style="margin-top:14px"><div class="spacer"></div><button class="primary" id="save">Сохранить настройки</button></div>` : ""}`;

  if (admin) drawIntegrations();
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

const KINDS = [["magnific", "Magnific"], ["veo", "Veo 3.1"], ["kling", "Kling 3 Omni"], ["seedance", "Seedance 2.0"], ["hailuo", "Hailuo 3"],
  ["wan", "WAN 2.7"], ["runway", "Runway Gen-4.5"], ["elevenlabs", "ElevenLabs (озвучка)"], ["other", "Другая"]];

async function drawIntegrations() {
  const box = $("#integrations");
  const list = await api("/api/integrations");
  const opts = (sel) => KINDS.map(([k, n]) => `<option value="${k}" ${k === sel ? "selected" : ""}>${n}</option>`).join("");
  box.innerHTML = `<h2 style="margin-top:0">Нейросети и API-ключи</h2>
    <p class="muted small">Добавьте нейросеть: дайте ей имя и приложите API-ключ. Ключи хранятся только у вас в data/studio.db. Видео генерируется через Magnific (ключ выше); здесь можно хранить ключи других сервисов, например ElevenLabs.</p>
    ${list.map((i) => `<div class="int-row" data-id="${i.id}">
      <input data-i="name" value="${esc(i.name)}" style="width:200px" placeholder="Имя">
      <select data-i="kind" style="width:auto">${opts(i.kind)}</select>
      <input data-i="api_key" type="password" value="${esc(i.key_mask)}" placeholder="API-ключ" autocomplete="off" style="width:260px">
      <button class="small" data-isave>Сохранить</button><button class="ghost small danger" data-idel>Удалить</button></div>`).join("") || `<div class="muted small">Пока ничего не добавлено</div>`}
    <h3>Добавить нейросеть</h3>
    <div class="int-row" id="int-new">
      <input data-i="name" style="width:200px" placeholder="Имя, например: Kling основной">
      <select data-i="kind" style="width:auto">${opts("other")}</select>
      <input data-i="api_key" type="password" style="width:260px" placeholder="API-ключ" autocomplete="off">
      <button class="primary small" id="int-add">Добавить</button></div>`;
  const read = (row) => Object.fromEntries($$("[data-i]", row).map((i) => [i.dataset.i, i.value]));
  $("#int-add", box).onclick = async () => {
    await api("/api/integrations", { json: read($("#int-new", box)) });
    toast("Нейросеть добавлена"); drawIntegrations();
  };
  $$("[data-isave]", box).forEach((b) => (b.onclick = async () => {
    const row = b.closest(".int-row");
    await api(`/api/integrations/${row.dataset.id}`, { method: "PUT", json: read(row) });
    toast("Сохранено"); drawIntegrations();
  }));
  $$("[data-idel]", box).forEach((b) => (b.onclick = async () => {
    if (!confirm("Удалить эту нейросеть и её ключ?")) return;
    await api(`/api/integrations/${b.closest(".int-row").dataset.id}`, { method: "DELETE" });
    drawIntegrations();
  }));
}
