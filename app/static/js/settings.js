// Settings (video generation via Magnific, breakdown, GitHub) and the publish stub. The team lives in team.js.
import { $, $$, api, can, engineName, esc, state, toast, view } from "./core.js";

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
        <h2 style="margin-top:0">Генерация (Google и Magnific)</h2>
        <p class="muted small">В карточке шота выбираются две нейросети: <b>Gemini Omni 1.1</b> (видео по стартовому и финальному кадрам) и <b>Рендерферма</b>.
          Кадры для Gemini Omni рисует <b>Nano Banana 2.1</b> через Magnific (бывший Freepik API), а само видео Gemini Omni идёт напрямую в Google (Gemini API): в API Magnific Omni пока нет. Видео всегда 720p, 3 или 5 секунд, кадры 1K, формат 9:16. Формат, стиль и шаблоны промптов — во вкладке «Тех. требования».</p>
        <label>Режим</label>
        <select data-k="gen_mode" ${ro}>
          <option value="stub" ${s.gen_mode === "stub" ? "selected" : ""}>Заглушка — ничего не отправлять, сохранять запрос в файл</option>
          <option value="live" ${s.gen_mode === "live" ? "selected" : ""}>Реальная генерация (платно, тратит кредиты)</option></select>
        ${field("magnific_api_key", "API-ключ Magnific (кабинет Magnific → API Keys; хранится только у вас в data/studio.db)", "input", `type="password" placeholder="FPSX…" autocomplete="off"`)}
        <label>Кадры Nano Banana (для Gemini Omni) идут через</label>
        <select data-k="frames_route" ${ro}>
          <option value="magnific" ${s.frames_route !== "google" ? "selected" : ""}>Magnific (ключ Magnific)</option>
          <option value="google" ${s.frames_route === "google" ? "selected" : ""}>Google напрямую (Gemini API, ключ Google)</option></select>
        <p class="muted small">Кадры всегда 1K (2K и 4K не запрашиваются, чтобы не тратить лишние кредиты). Переключайте когда нужно: уже готовые кадры не меняются.</p>
        <label>Видео Gemini Omni идёт через</label>
        <select data-k="omni_route" ${ro}>
          <option value="google" ${s.omni_route !== "magnific" ? "selected" : ""}>Google напрямую (Gemini API, ключ Google)</option>
          <option value="magnific" ${s.omni_route === "magnific" ? "selected" : ""}>Magnific (когда там откроют Omni в API)</option></select>
        ${field("veo_api_key", "API-ключ Google (Gemini API; aistudio.google.com/apikey, нужен платный аккаунт с оплатой за использование)", "input", `type="password" placeholder="AIza…" autocomplete="off"`)}
        ${field("public_base_url", "Публичный адрес студии (например, https://pari-altushka.ru), по желанию", "input", `placeholder="пусто: студия на ноутбуке"`)}
        <p class="muted small">Если указан, картинки кадров для Gemini Omni уходят в Magnific защищённой ссылкой на студию, а не файлом внутри запроса.</p>
        <details style="margin-top:8px"><summary class="muted small">Адрес Gemini Omni в Magnific (нужен, только если выбрана доставка через Magnific)</summary>
          ${field("omni_path", "Адрес Gemini Omni 1.1", "input", `placeholder="/v1/ai/video/gemini-omni-1-1"`)}
          <p class="muted small">В открытой документации Magnific пока нет страницы Gemini Omni, поэтому студия использует предполагаемые адреса.
            Если генерация не работает, текст ответа Magnific виден в дубле: пришлите его, и адрес поправим.</p></details>
        <div style="max-width:260px">${field("veo_parallel", "Одновременных генераций")}</div>
        <p class="muted small">Число одновременных генераций применяется после перезапуска приложения. Готовое видео и кадры скачиваются в студию сразу:
          ссылка Magnific на результат живёт недолго.</p>
    </div>
    <div class="card" style="margin-top:14px">
      <h2 style="margin-top:0">Рендерферма (Пульт, MiniMax H3)</h2>
      <p class="muted small">Шоты с нейросетью «Рендерферма (H3)» забирает Пульт студийной рендер-фермы. Студия сама к ферме не обращается (она за закрытой сетью): кнопка «Сгенерировать» готовит шоты серии,
        а Пульт раз в 10 секунд спрашивает у сайта новые, присылает статус, процент (генерация 0–50 %, доводка до 1080p 50–100 %), черновик и готовое видео. Режим «Заглушка / Реальная генерация» общий.</p>
      ${field("farmhub_secret", `Секрет связи с Пультом ${envMark(s, "secret")}`, "input", `type="password" placeholder="whsec_…" autocomplete="off"`)}
      <p class="muted small">Секрет владелец Пульта берёт в файле <code>site-credentials.txt</code> (строка <code>FARMHUB_SECRET=…</code>) и передаёт напрямую, в чаты его не присылают.
        Его же можно задать переменной сервера <code>FARMHUB_SECRET</code>: она главнее поля. Пульт ходит на <code>${esc((s.public_base_url || location.origin).replace(/\/$/, ""))}/api/farmhub/…</code>.
        ${farmSeen(s)}</p>
      <p class="muted small">Описания шота (действие, камера, голос, место) студия отправляет как написаны, на любом языке: русский текст идёт с пометкой <code>translate</code>, и Пульт переводит его сам,
        реплика остаётся русской. Другие нейросети (Magnific и др.) это не затрагивает. Пока Пульт перевод не умеет, он вернёт шот с причиной в карточке.</p>
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

const farmSeen = (s) => s.farmhub_seen == null ? `<b>Пульт ещё не обращался</b> с последнего запуска студии.`
  : s.farmhub_seen < 120 ? `<b>Пульт на связи:</b> последнее обращение ${s.farmhub_seen} с назад.`
  : `<b>Пульт молчит ${Math.round(s.farmhub_seen / 60)} мин:</b> ферма выключена или нет связи, шоты подождут.`;
const envMark = (s, k) => (s.farmhub_env?.[k] ? `<span class="badge ok">задано на сервере</span>` : "");

const KINDS = [["magnific", "Magnific"], ["gemini_omni", "Gemini Omni 1.1"], ["renderfarm", "Рендерферма (H3)"], ["elevenlabs", "ElevenLabs (озвучка)"], ["other", "Другая"]];

async function drawIntegrations() {
  const box = $("#integrations");
  const list = await api("/api/integrations");
  // старые нейросети (Veo, Kling…) из выбора убраны, но уже сохранённые ключи не теряем
  const opts = (sel) => (KINDS.some(([k]) => k === sel) ? KINDS : [...KINDS, [sel, engineName(sel)]])
    .map(([k, n]) => `<option value="${k}" ${k === sel ? "selected" : ""}>${esc(n)}</option>`).join("");
  box.innerHTML = `<h2 style="margin-top:0">Нейросети и API-ключи</h2>
    <p class="muted small">Добавьте нейросеть: дайте ей имя и приложите API-ключ. Ключи хранятся только у вас в data/studio.db. Видео и кадры генерируются через Magnific (ключ выше) или на рендер-ферме (её секрет вписан в карточке «Рендерферма» выше); здесь же ключи других сервисов, например ElevenLabs.</p>
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
