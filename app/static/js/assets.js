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
    <div class="lib-tabs">${Object.entries(KIND).map(([key, v]) => `<a class="${key === kind ? "on" : ""}" href="#/${v.path}">${v.title}</a>`).join("")}</div>
    <div class="row"><h1>${k.title}</h1><div class="spacer"></div>
      <input id="search" placeholder="Поиск" style="width:200px">
      ${can.library() ? `<button class="primary" id="new">+ Добавить</button>` : ""}</div>
    <p class="muted">${k.hint}</p>
    <div class="grid" id="grid"></div>`;
  const draw = (q = "") => {
    const list = assets.filter((a) => (a.name + " " + a.aliases.join(" ") + " " + a.tag).toLowerCase().includes(q.toLowerCase()));
    $("#grid").innerHTML = list.map((a) => {
      const v = a.versions.find((x) => x.id === a.active_version_id) || a.versions.at(-1) || { images: [] };
      return `<div class="card tile" data-id="${a.id}">
        <div class="thumb" style="${v.images[0] ? `background-image:url('${media(v.images[0])}')` : ""}">${v.images[0] ? "" : k.icon}</div>
        <div class="meta"><b>${esc(a.name)}</b><span class="muted small">${esc(handle(a.name))} · <span title="Метка для промпта">@${esc(a.tag)}</span> · v${v.version_no || 1} ${esc(v.label || "")} · версий: ${a.versions.length}</span></div></div>`;
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
  const ro = can.library() ? "" : "disabled";
  view().innerHTML = `
    <div class="row"><a href="#/${k.path}" class="btn ghost">←</a><h1 style="margin:0">${esc(a.name)}</h1>
      <span class="badge">${k.one}</span><code class="handle" title="Скопировать" id="copy">${esc(handle(a.name))}</code><div class="spacer"></div>
      ${can.library() ? `<button class="danger ghost" id="del">Удалить</button>` : ""}</div>
    <div class="two-col" style="margin-top:16px">
      <div class="card">
        <label>Название</label><input id="a-name" value="${esc(a.name)}" ${ro}>
        <label>Другие имена через запятую — по ним тоже узнаём в тексте</label>
        <input id="a-aliases" value="${esc(a.aliases.join(", "))}" ${ro}>
        <label>Метка для промпта <span class="muted small">— латиницей, своя у каждого. Так ${a.kind === "location" ? "локация" : "персонаж"} называется в промпте для ручной генерации, и так же называются скачанные фото</span></label>
        <div class="tag-input"><span>@</span><input id="a-tag" value="${esc(a.tag)}" ${ro} pattern="[a-z0-9_]+" spellcheck="false" autocapitalize="off"></div>
        ${can.library() ? `<div class="row" style="margin-top:10px"><div class="spacer"></div><button id="save-meta">Сохранить</button></div>` : ""}
        <h2>Активная версия: v${active.version_no} ${esc(active.label)}</h2>
        <p class="muted small">Активная версия используется по умолчанию. В конкретной серии можно выбрать другую (вкладка «Сценарий» → «Версии для этой серии»).</p>
        <div class="gallery">${active.images.map((p, i) => `<figure class="ph" data-img="${esc(p)}">
          <a href="${media(p)}" target="_blank"><img src="${media(p)}" draggable="true"></a>${i === 0 ? `<span class="main-ref">главный реф</span>` : ""}
          ${can.library() || (active.image_names || {})[p] ? `<input class="pname" data-pname value="${esc((active.image_names || {})[p] || "")}" placeholder="название (необязательно)" ${can.library() ? "" : "disabled"}>` : ""}
          <figcaption><button class="ghost small" data-copy title="Скопировать фото, чтобы вставить в видеогенератор (Ctrl+V)">Копировать</button>
            <a class="btn ghost small" href="${media(p)}" download="${esc(a.tag + (i ? "_" + (i + 1) : "") + ((p.match(/\.\w+$/) || [".png"])[0]))}" title="Скачать файл с именем по метке">Скачать</a>
            ${can.library() && i > 0 ? `<button class="ghost small" data-main title="Сделать главным референсом (уходит в видеогенератор первым)">★</button>` : ""}
            ${can.library() ? `<button class="ghost small danger" data-rm title="Убрать фото из версии">✕</button>` : ""}</figcaption></figure>`).join("")
          || `<span class="muted">Нет фото — нейросеть нарисует по описанию.</span>`}</div>
        ${can.library() ? `<div class="dropzone" id="add-photos" style="margin-top:10px"></div>
          <div class="muted small" style="margin-top:4px">Добавляйте сколько угодно фото: ракурсы со всех сторон, крупно лицо, в полный рост${a.kind === "location" ? ", разные углы помещения" : ""}. Первое фото — главный референс для видеогенератора, звёздочка ★ делает главным другое.</div>` : ""}
        ${statesHtml(a, active)}
        <label>Описание для промпта</label><div class="pre">${esc(active.description) || `<span class="muted">—</span>`}</div>
        ${a.kind === "character" ? `<label>Голос</label><div>${esc(active.voice) || `<span class="muted">—</span>`}</div>` : ""}
        ${active.notes ? `<label>Заметки</label><div class="pre">${esc(active.notes)}</div>` : ""}
        ${can.library() ? `<div class="row" style="margin-top:14px"><button class="primary" id="new-ver">+ Новая версия на основе активной</button></div>` : ""}
      </div>
      <div>
        <h2 style="margin-top:0">Версии</h2>
        <div class="versions">${[...a.versions].reverse().map((v) => `
          <div class="card version ${v.id === a.active_version_id ? "active" : ""}">
            <div class="vthumb" style="${v.images[0] ? `background-image:url('${media(v.images[0])}')` : ""}"></div>
            <div><b>v${v.version_no} · ${esc(v.label)}</b> <code class="small">${esc(handle(a.name))}:${esc((v.label || "v" + v.version_no).replace(/\s+/g, "_"))}</code>
              <div class="muted small">${fmtDateTime(v.created_at)} · фото: ${v.images.length}${v.states?.length ? ` · состояний: ${v.states.length}` : ""}${v.parent_version_id ? ` · на основе v${(a.versions.find((x) => x.id === v.parent_version_id) || {}).version_no ?? "?"}` : ""}</div>
              <div class="small">${esc(v.description.slice(0, 160))}${v.description.length > 160 ? "…" : ""}</div></div>
            <div class="col">
              ${v.id === a.active_version_id ? `<span class="badge ok">активная</span>` : can.library() ? `<button data-activate="${v.id}">Сделать активной</button>` : ""}
              ${can.library() ? `<button class="ghost" data-fork="${v.id}">Изменить → новая</button>` : ""}
              ${can.library() && v.id !== a.active_version_id ? `<button class="ghost small danger" data-delver="${v.id}" title="Удалить эту версию">🗑 Удалить</button>` : ""}</div>
          </div>`).join("")}</div>
      </div>
    </div>`;
  $("#copy").onclick = () => { navigator.clipboard.writeText(handle(a.name)); toast("Скопировано"); };
  $$("[data-copy]").forEach((b) => (b.onclick = () => copyImage(media(b.closest("[data-img]").dataset.img))));
  if (!can.library()) return;
  const setImages = async (images) => {
    await api(`/api/assets/${id}/versions/${active.id}/images`, { method: "PUT", json: { images } });
    renderAsset(id);
  };
  $$("[data-pname]").forEach((inp) => (inp.onchange = async () => {
    await api(`/api/assets/${id}/versions/${active.id}/image-names`, { method: "PUT", json: { names: { [inp.closest("[data-img]").dataset.img]: inp.value } } });
    toast("Сохранено");
    renderAsset(id);
  }));
  $$("[data-main]").forEach((b) => (b.onclick = () => {
    const p = b.closest("[data-img]").dataset.img;
    setImages([p, ...active.images.filter((x) => x !== p)]).then(() => toast("Главный референс изменён"));
  }));
  $$("[data-rm]").forEach((b) => (b.onclick = () => {
    if (!confirm("Убрать это фото из версии?")) return;
    const p = b.closest("[data-img]").dataset.img;
    setImages(active.images.filter((x) => x !== p));
  }));
  dropzone($("#add-photos"), async (files) => {
    if (!files.length) return;
    const fd = new FormData();
    files.forEach((f) => fd.append("images", f));
    toast(`Загружаю фото: ${files.length}…`);
    await api(`/api/assets/${id}/versions/${active.id}/images`, { method: "POST", body: fd });
    toast(`Добавлено фото: ${files.length}`);
    renderAsset(id);
  });
  bindStates(a, active, () => renderAsset(id));
  $("#save-meta").onclick = async () => {
    const tag = $("#a-tag").value.trim().replace(/^@/, "").toLowerCase();
    if (!/^[a-z0-9_]{1,40}$/.test(tag)) return toast("Метка — только латиница, цифры и «_», без пробелов: например, altushka", true);
    await api(`/api/assets/${id}`, { method: "PUT", json: { name: $("#a-name").value, aliases: $("#a-aliases").value.split(","), tag } });
    toast("Сохранено"); renderAsset(id);
  };
  $("#del").onclick = async () => {
    if (!confirm(`Удалить «${a.name}» со всеми версиями? В шотах пропадёт ссылка на него.`)) return;
    await api(`/api/assets/${id}`, { method: "DELETE" }); location.hash = `#/${k.path}`;
  };
  $("#new-ver").onclick = () => versionDialog({ kind: a.kind, asset: a, base: active });
  $$("[data-fork]").forEach((b) => (b.onclick = () => versionDialog({ kind: a.kind, asset: a, base: a.versions.find((v) => v.id === +b.dataset.fork) })));
  $$("[data-delver]").forEach((b) => (b.onclick = async () => {
    const v = a.versions.find((x) => x.id === +b.dataset.delver);
    const u = await api(`/api/assets/${id}/versions/${v.id}/usage`);
    const act = a.versions.find((x) => x.id === a.active_version_id);
    const where = [
      u.shots && `шоты — ${u.shots} (серий: ${u.episodes})`,
      u.cast && `«Версии для серии» — ${u.cast}`,
      u.arcs && `участники арок — ${u.arcs}`,
    ].filter(Boolean);
    const msg = `Удалить версию v${v.version_no} «${v.label}» у «${a.name}»? Её фото тоже удалятся. Вернуть будет нельзя.`
      + (where.length ? `\n\nГде используется: ${where.join("; ")}. Там подставится активная версия v${act.version_no} «${act.label}».` : "");
    if (!confirm(msg)) return;
    await api(`/api/assets/${id}/versions/${v.id}`, { method: "DELETE" });
    toast(`Версия v${v.version_no} удалена`);
    renderAsset(id);
  }));
  $$("[data-activate]").forEach((b) => (b.onclick = async () => {
    await api(`/api/assets/${id}/versions/${b.dataset.activate}/activate`, { json: {} });
    toast("Версия активирована. Она применится к сериям при следующем сохранении сценария."); renderAsset(id);
  }));
}

// ---------- состояния версии ----------
// Версия = образ («Пижама», «Зима»). Состояние = в каком виде этот образ бывает по сценарию («проснулась», «плачет», «ночью»).
// У каждого состояния свои картинки с названиями. Нет состояний — у версии одно состояние: её фото. Когда готовятся кадры
// для Gemini Omni, в карточке шота выбирают версию героя, а в блоке кадров — состояние.

function statesHtml(a, v) {
  const lib = can.library();
  const states = v.states || [];
  return `<h3 style="margin:16px 0 4px">Состояния версии «${esc(v.label)}»</h3>
    <p class="muted small" style="margin:0 0 6px">Состояние — в каком виде ${a.kind === "location" ? "локация" : "персонаж"} бывает в сценарии: ${a.kind === "location" ? "«днём», «ночью», «после ремонта»" : "«проснулась», «плачет», «в халате»"}.
      Фото выше — общий пул версии: в состояние можно взять любые из него или загрузить свои. Если состояний нет, у версии одно состояние: все фото. Если есть, при подготовке кадров для Gemini Omni можно выбрать нужное.</p>
    <div class="states" id="states">${states.map((st) => `<div class="state-card" data-state="${st.id}">
      <div class="row"><input data-sname value="${esc(st.name)}" ${lib ? "" : "disabled"} style="max-width:260px" title="Название состояния">
        <span class="muted small">картинок: ${st.images.length}</span><div class="spacer"></div>
        ${lib ? `<button class="ghost small danger" data-sdel>Удалить состояние</button>` : ""}</div>
      <div class="state-imgs">${st.images.map((i) => `<figure class="state-img" data-path="${esc(i.path)}">
        <a href="${media(i.path)}" target="_blank"><img src="${media(i.path)}"></a>
        <input data-iname value="${esc(i.name)}" placeholder="название" ${lib ? "" : "disabled"} title="Название картинки">
        ${lib ? `<button class="ghost small rm" data-irm title="Убрать картинку">✕</button>` : ""}</figure>`).join("") || `<span class="muted small">Нет картинок</span>`}</div>
      ${lib && v.images.length ? `<div class="muted small" style="margin-top:8px">Общие фото версии: нажмите на фото, чтобы добавить его в состояние или убрать</div>
        <div class="pool-pick">${v.images.map((p) => `<figure class="pool-ph ${st.images.some((i) => i.path === p) ? "on" : ""}" data-pool="${esc(p)}" title="${esc((v.image_names || {})[p] || "")}">
          <img src="${media(p)}"><figcaption>${esc((v.image_names || {})[p] || "")}</figcaption></figure>`).join("")}</div>` : ""}
      ${lib ? `<div class="dropzone small-drop" data-sdrop style="margin-top:8px"></div>` : ""}</div>`).join("") || `<div class="muted small">Состояний пока нет.</div>`}</div>
    ${lib ? `<div class="state-card" style="margin-top:10px" id="state-new">
      <b class="small">Новое состояние</b>
      <div class="row" style="margin-top:6px"><input id="sn-name" placeholder="${a.kind === "location" ? "Ночью" : "Плачет"}" style="max-width:260px">
        <button class="primary small" id="sn-add">Добавить состояние</button></div>
      <div class="dropzone small-drop" id="sn-drop" style="margin-top:8px"></div><div class="img-new" id="sn-picked"></div></div>` : ""}`;
}

function bindStates(a, v, refresh) {
  if (!can.library()) return;
  const base = `/api/assets/${a.id}/versions/${v.id}/states`;
  const stateOf = (el) => v.states.find((s) => s.id === +el.closest("[data-state]").dataset.state);
  $$("[data-sname]").forEach((inp) => (inp.onchange = async () => {
    const st = stateOf(inp);
    if (!inp.value.trim()) { inp.value = st.name; return toast("Назовите состояние", true); }
    await api(`${base}/${st.id}`, { method: "PUT", json: { name: inp.value } }).then(() => toast("Сохранено"), () => (inp.value = st.name));
    refresh();
  }));
  $$("[data-sdel]").forEach((b) => (b.onclick = async () => {
    const st = stateOf(b);
    if (!confirm(`Удалить состояние «${st.name}» и его картинки (${st.images.length})?`)) return;
    await api(`${base}/${st.id}`, { method: "DELETE" });
    refresh();
  }));
  $$("[data-pool]").forEach((f) => (f.onclick = async () => {
    const st = stateOf(f);
    const path = f.dataset.pool;
    const has = st.images.some((i) => i.path === path);
    const images = has ? st.images.filter((i) => i.path !== path) : [...st.images, { path, name: (v.image_names || {})[path] || "" }];
    await api(`${base}/${st.id}`, { method: "PUT", json: { images } });
    refresh();
  }));
  $$("[data-iname]").forEach((inp) => (inp.onchange = async () => {
    const st = stateOf(inp);
    const path = inp.closest("[data-path]").dataset.path;
    await api(`${base}/${st.id}`, { method: "PUT", json: { images: st.images.map((i) => (i.path === path ? { ...i, name: inp.value } : i)) } });
    toast("Сохранено");
    refresh();
  }));
  $$("[data-irm]").forEach((b) => (b.onclick = async () => {
    const st = stateOf(b);
    const path = b.closest("[data-path]").dataset.path;
    if (!confirm("Убрать эту картинку из состояния?")) return;
    await api(`${base}/${st.id}`, { method: "PUT", json: { images: st.images.filter((i) => i.path !== path) } });
    refresh();
  }));
  $$("[data-sdrop]").forEach((z) => dropzone(z, async (files) => {
    if (!files.length) return;
    const st = stateOf(z);
    const fd = new FormData();
    files.forEach((f) => fd.append("images", f));
    toast(`Загружаю картинки: ${files.length}…`);
    await api(`${base}/${st.id}/images`, { method: "POST", body: fd });
    refresh();
  }));
  // новое состояние: название и картинки (названия картинок берутся из имён файлов, потом их можно поменять)
  const picked = [];
  const drawPicked = () => {
    $("#sn-picked").innerHTML = picked.map((f, i) => `<figure><img src="${URL.createObjectURL(f)}"><button class="ghost small" data-sn-un="${i}" title="Убрать">✕</button></figure>`).join("");
    $$("[data-sn-un]").forEach((b) => (b.onclick = () => { picked.splice(+b.dataset.snUn, 1); drawPicked(); }));
  };
  dropzone($("#sn-drop"), (files) => { picked.push(...files); drawPicked(); });
  $("#sn-add").onclick = async () => {
    const name = $("#sn-name").value.trim();
    if (!name) return toast("Назовите состояние: например, «плачет» или «ночью»", true);
    const fd = new FormData();
    fd.append("name", name);
    fd.append("names", JSON.stringify(picked.map((f) => f.name.replace(/\.\w+$/, ""))));
    picked.forEach((f) => fd.append("images", f));
    await api(base, { method: "POST", body: fd });
    toast(`Состояние «${name}» добавлено`);
    refresh();
  };
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
    <label>Фото (PNG / JPG / WEBP): сколько угодно, лучше со всех сторон. Первое фото — главный референс для видеогенератора.</label>
    <div class="dropzone" id="v-drop"></div>
    <div class="img-new" id="v-new"></div>
    <div class="row" style="margin-top:16px"><div class="spacer"></div>
      <button class="ghost" id="cancel">Отмена</button><button class="primary" id="save">Сохранить</button></div>`);
  $("#cancel", m).onclick = closeModal;
  const picked = [];
  const drawPicked = () => {
    $("#v-new", m).innerHTML = picked.map((f, i) => `<figure><img src="${URL.createObjectURL(f)}"><button class="ghost small" data-unpick="${i}" title="Убрать">✕</button></figure>`).join("");
    $$("[data-unpick]", m).forEach((b) => (b.onclick = () => { picked.splice(+b.dataset.unpick, 1); drawPicked(); }));
  };
  dropzone($("#v-drop", m), (files) => { picked.push(...files); drawPicked(); });
  $("#save", m).onclick = async () => {
    const fd = new FormData();
    fd.append("label", $("#v-label", m).value);
    fd.append("description", $("#v-desc", m).value);
    fd.append("voice", isChar ? $("#v-voice", m).value : "");
    fd.append("notes", $("#v-notes", m).value);
    for (const f of picked) fd.append("images", f);
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

/** Поле для фото: клик — выбор в проводнике (можно сразу несколько), перетаскивание файлов, вставка из буфера (Ctrl+V). */
function dropzone(el, onFiles) {
  if (!el) return;
  el.innerHTML = `<input type="file" multiple accept="image/png,image/jpeg,image/webp" hidden>
    <div>📷 Перетащите фото сюда или нажмите, чтобы выбрать <span class="muted small">(можно несколько сразу; Ctrl+V вставит из буфера)</span></div>`;
  el.tabIndex = 0;
  const input = $("input", el);
  const images = (list) => [...list].filter((f) => /^image\/(png|jpeg|webp)$/.test(f.type));
  el.onclick = () => input.click();
  input.onclick = (e) => e.stopPropagation();
  input.onchange = () => { onFiles(images(input.files)); input.value = ""; };
  el.addEventListener("dragover", (e) => { e.preventDefault(); el.classList.add("drop-over"); });
  el.addEventListener("dragleave", () => el.classList.remove("drop-over"));
  el.addEventListener("drop", (e) => {
    e.preventDefault(); el.classList.remove("drop-over");
    const files = images(e.dataTransfer.files);
    if (!files.length) return toast("Перетащите файлы PNG, JPG или WEBP", true);
    onFiles(files);
  });
  el._onFiles = onFiles;
}

// Ctrl+V с картинкой в буфере: в зону, над которой курсор, или в зону рядом с полем, где стоит фокус
document.addEventListener("paste", (e) => {
  const files = [...(e.clipboardData?.files || [])].filter((f) => /^image\/(png|jpeg|webp)$/.test(f.type));
  if (!files.length) return;
  const zones = $$(".dropzone").filter((z) => z._onFiles && z.isConnected);
  const a = document.activeElement;
  const zone = zones.find((z) => z.matches(":hover")) || zones.find((z) => z.contains(a) || z.parentElement?.contains(a))
    || (zones.length === 1 ? zones[0] : null);
  if (!zone) return;
  e.preventDefault();
  zone._onFiles(files);
});

/** Копирует фото в буфер как картинку (для вставки в видеогенератор). Браузер принимает только PNG, поэтому JPG/WEBP перекодируются. */
async function copyImage(url) {
  try {
    const blob = await (await fetch(url)).blob();
    let png = blob;
    if (blob.type !== "image/png") {
      const bmp = await createImageBitmap(blob);
      const c = Object.assign(document.createElement("canvas"), { width: bmp.width, height: bmp.height });
      c.getContext("2d").drawImage(bmp, 0, 0);
      png = await new Promise((r) => c.toBlob(r, "image/png"));
    }
    await navigator.clipboard.write([new ClipboardItem({ "image/png": png })]);
    toast("Фото скопировано: вставьте его в генератор (Ctrl+V)");
  } catch {
    toast("Не удалось скопировать: нажмите «Скачать» или перетащите фото мышкой", true);
  }
}
