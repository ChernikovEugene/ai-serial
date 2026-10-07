// Characters & locations library with versions (costumes / variants) and rollback.
import { $, $$, api, can, closeModal, esc, fmtDateTime, loadAssets, media, modal, toast, view } from "./core.js";

const KIND = {
  character: { title: "Персонажи", one: "персонаж", icon: "👤", path: "characters",
    hint: "В сценарии пишите <code>@Имя</code> — подставится внешность и фото персонажа. <code>@Имя:Версия</code> — конкретный костюм." },
  location: { title: "Локации", one: "локация", icon: "🏙️", path: "locations",
    hint: "В сценарии пишите <code>@Название</code> или используйте его в заголовке сцены (<code>ИНТ. КАФЕ — ДЕНЬ</code>)." },
};
const handle = (name) => "@" + name.trim().replace(/\s+/g, "_");

export async function renderAssets(kind) {
  const assets = (await loadAssets()).filter((a) => a.kind === kind);
  const k = KIND[kind];
  view().innerHTML = `
    <div class="row lib-tabs">${Object.entries(KIND).map(([key, v]) => `<a class="chip-btn ${key === kind ? "on" : ""}" href="#/${v.path}">${v.title}</a>`).join("")}</div>
    <div class="row"><h1>${k.title}</h1><div class="spacer"></div>
      <input id="search" placeholder="Поиск" style="width:200px">
      ${can.edit() ? `<button class="primary" id="new">+ Добавить</button>` : ""}</div>
    <p class="muted">${k.hint}</p>
    <div class="grid" id="grid"></div>`;
  const draw = (q = "") => {
    const list = assets.filter((a) => (a.name + " " + a.aliases.join(" ")).toLowerCase().includes(q.toLowerCase()));
    $("#grid").innerHTML = list.map((a) => {
      const v = a.versions.find((x) => x.id === a.active_version_id) || a.versions.at(-1) || { images: [] };
      return `<div class="card tile" data-id="${a.id}">
        <div class="thumb" style="${v.images[0] ? `background-image:url('${media(v.images[0])}')` : ""}">${v.images[0] ? "" : k.icon}</div>
        <div class="meta"><b>${esc(a.name)}</b><span class="muted small">${esc(handle(a.name))} · v${v.version_no || 1} ${esc(v.label || "")} · версий: ${a.versions.length}</span></div></div>`;
    }).join("") || `<p class="muted">Пока пусто.</p>`;
    $$(".tile").forEach((t) => (t.onclick = () => (location.hash = `#/${k.path}/${t.dataset.id}`)));
  };
  draw();
  $("#search").oninput = (e) => draw(e.target.value);
  $("#new") && ($("#new").onclick = () => versionDialog({ kind }));
}

export async function renderAsset(id) {
  const all = await loadAssets();
  const a = all.find((x) => x.id === id);
  if (!a) { view().innerHTML = `<p>Не найдено</p>`; return; }
  const k = KIND[a.kind];
  const active = a.versions.find((v) => v.id === a.active_version_id) || a.versions.at(-1);
  const ro = can.edit() ? "" : "disabled";
  view().innerHTML = `
    <div class="row"><a href="#/${k.path}" class="btn ghost">←</a><h1 style="margin:0">${esc(a.name)}</h1>
      <span class="badge">${k.one}</span><code class="handle" title="Скопировать" id="copy">${esc(handle(a.name))}</code><div class="spacer"></div>
      ${can.admin() ? `<button class="danger ghost" id="del">Удалить</button>` : ""}</div>
    <div class="two-col" style="margin-top:16px">
      <div class="card">
        <label>Название</label><input id="a-name" value="${esc(a.name)}" ${ro}>
        <label>Другие имена через запятую — по ним тоже узнаём в тексте</label>
        <input id="a-aliases" value="${esc(a.aliases.join(", "))}" ${ro}>
        ${can.edit() ? `<div class="row" style="margin-top:10px"><div class="spacer"></div><button id="save-meta">Сохранить</button></div>` : ""}
        <h2>Активная версия: v${active.version_no} ${esc(active.label)}</h2>
        <p class="muted small">Активная версия используется по умолчанию. В конкретной серии можно выбрать другую (вкладка «Сценарий» → «Версии для этой серии»).</p>
        <div class="gallery">${active.images.map((p, i) => `<a href="${media(p)}" target="_blank"><img src="${media(p)}">${i === 0 ? `<span class="main-ref">главный реф</span>` : ""}</a>`).join("") || `<span class="muted">Нет фото — Veo нарисует по описанию.</span>`}</div>
        <label>Описание для промпта</label><div class="pre">${esc(active.description) || `<span class="muted">—</span>`}</div>
        ${a.kind === "character" ? `<label>Голос</label><div>${esc(active.voice) || `<span class="muted">—</span>`}</div>` : ""}
        ${active.notes ? `<label>Заметки</label><div class="pre">${esc(active.notes)}</div>` : ""}
        ${can.edit() ? `<div class="row" style="margin-top:14px"><button class="primary" id="new-ver">+ Новая версия на основе активной</button></div>` : ""}
      </div>
      <div>
        <h2 style="margin-top:0">Версии</h2>
        <div class="versions">${[...a.versions].reverse().map((v) => `
          <div class="card version ${v.id === a.active_version_id ? "active" : ""}">
            <div class="vthumb" style="${v.images[0] ? `background-image:url('${media(v.images[0])}')` : ""}"></div>
            <div><b>v${v.version_no} · ${esc(v.label)}</b> <code class="small">${esc(handle(a.name))}:${esc((v.label || "v" + v.version_no).replace(/\s+/g, "_"))}</code>
              <div class="muted small">${fmtDateTime(v.created_at)} · фото: ${v.images.length}${v.parent_version_id ? ` · на основе v${(a.versions.find((x) => x.id === v.parent_version_id) || {}).version_no ?? "?"}` : ""}</div>
              <div class="small">${esc(v.description.slice(0, 160))}${v.description.length > 160 ? "…" : ""}</div></div>
            <div class="col">
              ${v.id === a.active_version_id ? `<span class="badge ok">активная</span>` : can.edit() ? `<button data-activate="${v.id}">Сделать активной</button>` : ""}
              ${can.edit() ? `<button class="ghost" data-fork="${v.id}">Изменить → новая</button>` : ""}</div>
          </div>`).join("")}</div>
      </div>
    </div>`;
  $("#copy").onclick = () => { navigator.clipboard.writeText(handle(a.name)); toast("Скопировано"); };
  if (!can.edit()) return;
  $("#save-meta").onclick = async () => {
    await api(`/api/assets/${id}`, { method: "PUT", json: { name: $("#a-name").value, aliases: $("#a-aliases").value.split(",") } });
    toast("Сохранено"); renderAsset(id);
  };
  $("#del").onclick = async () => {
    if (!confirm(`Удалить «${a.name}» со всеми версиями? В шотах пропадёт ссылка на него.`)) return;
    await api(`/api/assets/${id}`, { method: "DELETE" }); location.hash = `#/${k.path}`;
  };
  $("#new-ver").onclick = () => versionDialog({ kind: a.kind, asset: a, base: active });
  $$("[data-fork]").forEach((b) => (b.onclick = () => versionDialog({ kind: a.kind, asset: a, base: a.versions.find((v) => v.id === +b.dataset.fork) })));
  $$("[data-activate]").forEach((b) => (b.onclick = async () => {
    await api(`/api/assets/${id}/versions/${b.dataset.activate}/activate`, { json: {} });
    toast("Версия активирована. Она применится к сериям при следующем сохранении сценария."); renderAsset(id);
  }));
}

function versionDialog({ kind, asset, base }) {
  const isNew = !asset, isChar = kind === "character";
  const m = modal(`
    <h1>${isNew ? (isChar ? "Новый персонаж" : "Новая локация") : `Новая версия: ${esc(asset.name)}`}</h1>
    ${isNew ? `<label>Название ${isChar ? "(имя, как в сценарии)" : "(как в заголовке сцены)"}</label><input id="v-name">
      <label>Другие имена через запятую</label><input id="v-aliases" placeholder="${isChar ? "Машка, Мария" : "Кофейня"}">`
      : `<p class="muted small">Новая версия на основе v${base.version_no}. Старая остаётся — к ней можно вернуться в любой момент.</p>`}
    <label>Название версии ${isChar ? "(костюм, образ)" : "(время суток, сезон, вариант)"}</label>
    <input id="v-label" value="${isNew ? "Базовый" : ""}" placeholder="${isChar ? "Пижама, Школьная форма…" : "Ночь, Зима…"}">
    <label>Описание для Veo (лучше на английском: внешность, возраст, одежда, детали)</label>
    <textarea id="v-desc" rows="4">${esc(base?.description || "")}</textarea>
    ${isChar ? `<label>Голос (тембр, манера речи)</label><input id="v-voice" value="${esc(base?.voice || "")}" placeholder="young female voice, sarcastic, fast">` : ""}
    <label>Заметки для команды (в промпт не идут)</label><textarea id="v-notes" rows="2">${esc(base?.notes || "")}</textarea>
    ${base?.images?.length ? `<label>Фото из v${base.version_no} — снимите галочку, чтобы не переносить</label>
      <div class="img-pick">${base.images.map((p) => `<label><input type="checkbox" checked value="${esc(p)}"><img src="${media(p)}"></label>`).join("")}</div>` : ""}
    <label>Добавить фото (PNG / JPG / WEBP). Первое фото — главный референс для Veo.</label>
    <input id="v-files" type="file" multiple accept="image/png,image/jpeg,image/webp">
    <div class="row" style="margin-top:16px"><div class="spacer"></div>
      <button class="ghost" id="cancel">Отмена</button><button class="primary" id="save">Сохранить</button></div>`);
  $("#cancel", m).onclick = closeModal;
  $("#save", m).onclick = async () => {
    const fd = new FormData();
    fd.append("label", $("#v-label", m).value);
    fd.append("description", $("#v-desc", m).value);
    fd.append("voice", isChar ? $("#v-voice", m).value : "");
    fd.append("notes", $("#v-notes", m).value);
    for (const f of $("#v-files", m).files) fd.append("images", f);
    let res;
    if (isNew) {
      const name = $("#v-name", m).value.trim();
      if (!name) return toast("Укажите название", true);
      fd.append("kind", kind); fd.append("name", name); fd.append("aliases", $("#v-aliases", m).value);
      res = await api("/api/assets", { method: "POST", body: fd });
    } else {
      fd.append("keep_images", JSON.stringify($$(".img-pick input:checked", m).map((i) => i.value)));
      fd.append("base_version_id", base.id);
      res = await api(`/api/assets/${asset.id}/versions`, { method: "POST", body: fd });
    }
    closeModal();
    toast("Сохранено");
    const target = `#/${KIND[kind].path}/${res.id}`;
    if (location.hash === target) renderAsset(res.id); else location.hash = target;
  };
  setTimeout(() => ($("#v-name", m) || $("#v-label", m)).focus(), 50);
}
