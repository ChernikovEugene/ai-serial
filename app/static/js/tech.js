// «Тех. требования»: параметры генерации (реально уходят в нейросеть) и шаблоны ТТ — видео, звук, монтаж.
// Шаблон с галочкой «Подставлять в промпт» добавляется в промпт каждого шота сразу после общего стиля.
import { $, $$, api, can, closeModal, esc, modal, state, toast, view } from "./core.js";

const KIND_COLORS = { video: "#b36bff", sound: "#25d0d6", edit: "#ff8a3d", other: "#8b93a3" };
const canEditTemplates = () => can.gen(); // продюсер и монтажёр

export async function renderTech() {
  const [s, t] = await Promise.all([api("/api/settings"), api("/api/templates")]);
  const admin = can.admin();
  const ro = admin ? "" : "disabled";
  const field = (k, label, type = "input", extra = "") => type === "textarea"
    ? `<label>${label}</label><textarea data-k="${k}" rows="3" ${ro}>${esc(s[k])}</textarea>`
    : `<label>${label}</label><input data-k="${k}" value="${esc(s[k])}" ${extra} ${ro}>`;
  const inPrompt = t.templates.filter((x) => x.in_prompt && x.prompt);

  view().innerHTML = `
    <div class="row"><h1>Технические требования</h1><div class="spacer"></div>
      ${canEditTemplates() ? `<button class="primary" id="tt-add">+ Шаблон</button>` : ""}</div>

    <h2>Шаблоны ТТ</h2>
    <p class="muted small">Памятки по формату, нейросети, звуку и монтажу. С галочкой «Подставлять в промпт» текст шаблона
      сам добавляется в промпт каждого шота${inPrompt.length ? ` — сейчас подставляется: ${inPrompt.map((x) => `«${esc(x.name)}»`).join(", ")}` : " — сейчас ни один не подставляется"}.</p>
    <div class="tt-grid">
      ${t.templates.map((x) => ttCard(x, t.kinds)).join("") || `<p class="muted">Шаблонов пока нет.</p>`}
    </div>

    <h2>Генерация</h2>
    <p class="muted small">Эти параметры уходят в нейросеть при каждой генерации.${admin ? "" : " Менять их может продюсер."}</p>
    <div class="two-col">
      <div class="card">
        <h3 style="margin-top:0">Формат</h3>
        ${field("veo_model", "Модель (фото-референсы есть только у Veo 3.1)")}
        <div class="row"><div style="flex:1">${field("aspect_ratio", "Соотношение сторон")}</div>
          <div style="flex:1">${field("resolution", "Разрешение (720p / 1080p)")}</div>
          <div style="flex:1">${field("max_shot_seconds", "Лимит шота, с")}</div></div>
        <div class="row"><div style="flex:1">${field("dialogue_language", "Язык реплик")}</div>
          <div style="flex:1">${field("words_per_second", "Темп речи, слов/с")}</div></div>
      </div>
      <div class="card">
        <h3 style="margin-top:0">Промпт</h3>
        ${field("style", "Общий стиль сериала (в начале каждого промпта)", "textarea")}
        ${field("negative_prompt", "Негативный промпт по умолчанию (чего не должно быть в кадре)", "textarea")}
      </div>
    </div>
    ${admin ? `<div class="row" style="margin-top:12px"><div class="spacer"></div><button class="primary" id="gen-save">Сохранить параметры генерации</button></div>` : ""}`;

  if (admin) $("#gen-save").onclick = async () => {
    const values = Object.fromEntries($$("[data-k]").map((i) => [i.dataset.k, i.value]));
    await api("/api/settings", { method: "PUT", json: values });
    state.meta = await api("/api/meta");
    toast("Параметры генерации сохранены");
  };
  $("#tt-add") && ($("#tt-add").onclick = () => ttDialog(null, t.kinds));
  $$("[data-tt-edit]").forEach((b) => (b.onclick = () => ttDialog(t.templates.find((x) => x.id === +b.dataset.ttEdit), t.kinds)));
  $$("[data-tt-toggle]").forEach((cb) => (cb.onchange = async () => {
    const x = t.templates.find((y) => y.id === +cb.dataset.ttToggle);
    await api(`/api/templates/${x.id}`, { method: "PUT", json: { ...x, in_prompt: cb.checked } });
    toast(cb.checked ? "Шаблон подставляется в промпты" : "Шаблон больше не подставляется в промпты");
    renderTech();
  }));
}

function ttCard(x, kinds) {
  return `<div class="card tt-card" style="--c:${KIND_COLORS[x.kind]}">
    <div class="row"><span class="status small" style="--c:${KIND_COLORS[x.kind]}">${esc(kinds[x.kind])}</span><div class="spacer"></div>
      ${canEditTemplates() ? `<button class="ghost small" data-tt-edit="${x.id}">Изменить</button>` : ""}</div>
    <h3>${esc(x.name)}</h3>
    ${x.model ? `<div class="muted small">Нейросеть: <b>${esc(x.model)}</b></div>` : ""}
    ${x.specs ? `<div class="tt-specs">${esc(x.specs)}</div>` : ""}
    ${x.prompt ? `<div class="tt-prompt" title="Текст для промпта"><code>${esc(x.prompt)}</code></div>` : ""}
    ${x.notes ? `<div class="muted small tt-notes">${esc(x.notes)}</div>` : ""}
    ${x.prompt ? `<label class="check"><input type="checkbox" data-tt-toggle="${x.id}" ${x.in_prompt ? "checked" : ""} ${canEditTemplates() ? "" : "disabled"}> Подставлять в промпт</label>` : ""}
  </div>`;
}

function ttDialog(x, kinds) {
  const m = modal(`<h1>${x ? "Шаблон" : "Новый шаблон"}</h1>
    <div class="row"><div style="flex:2"><label>Название</label><input id="tt-name" value="${esc(x?.name || "")}" placeholder="Например: Видео для Reels"></div>
      <div style="flex:1"><label>Вид</label><select id="tt-kind">${Object.entries(kinds).map(([k, n]) => `<option value="${k}" ${x?.kind === k ? "selected" : ""}>${n}</option>`).join("")}</select></div></div>
    <label>Нейросеть</label><input id="tt-model" value="${esc(x?.model || "")}" placeholder="Veo 3.1, Kling 2.1, Sora, Runway…">
    <label>Параметры (формат, размер, длительность, fps, звук…)</label>
    <textarea id="tt-specs" rows="4" placeholder="9:16, 1080×1920&#10;Шот до 8 с, 24 fps">${esc(x?.specs || "")}</textarea>
    <label>Текст для промпта (лучше на английском)</label>
    <textarea id="tt-prompt" rows="3" placeholder="Realistic smartphone-style vertical footage, natural light…">${esc(x?.prompt || "")}</textarea>
    <label class="check"><input type="checkbox" id="tt-in" ${x?.in_prompt ? "checked" : ""}> Подставлять в промпт каждого шота</label>
    <label>Пометки для команды (в промпт не идут)</label>
    <textarea id="tt-notes" rows="2">${esc(x?.notes || "")}</textarea>
    <div class="row" style="margin-top:14px">${x ? `<button class="ghost danger" id="tt-del">Удалить</button>` : ""}
      <div class="spacer"></div><button class="ghost" id="tt-cancel">Отмена</button><button class="primary" id="tt-save">Сохранить</button></div>`, true);
  $("#tt-cancel", m).onclick = closeModal;
  $("#tt-save", m).onclick = async () => {
    const body = { name: $("#tt-name", m).value, kind: $("#tt-kind", m).value, model: $("#tt-model", m).value,
      specs: $("#tt-specs", m).value, prompt: $("#tt-prompt", m).value, notes: $("#tt-notes", m).value, in_prompt: $("#tt-in", m).checked };
    await api(x ? `/api/templates/${x.id}` : "/api/templates", { method: x ? "PUT" : "POST", json: body });
    closeModal(); toast("Шаблон сохранён"); renderTech();
  };
  if (x) $("#tt-del", m).onclick = async () => {
    if (!confirm(`Удалить шаблон «${x.name}»?`)) return;
    await api(`/api/templates/${x.id}`, { method: "DELETE" });
    closeModal(); renderTech();
  };
  setTimeout(() => $("#tt-name", m).focus(), 50);
}
