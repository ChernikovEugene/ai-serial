// Блок «Кадры для Gemini Omni» в карточке шота: стартовый и финальный кадры рисует Nano Banana 2.1 (API Magnific).
// На вход кадру: промпт (пишется руками) и картинки-референсы: герои и локация шота из библиотеки (с выбором версии и
// состояния), свои картинки, а для финального кадра ещё и готовый стартовый. Размер 1K, формат 9:16.
// Когда оба кадра вернулись, блок становится зелёным и шот можно отправлять в Gemini Omni.
import { $, $$, api, esc, media, state, toast } from "./core.js";

const SIDES = [["start", "Стартовый кадр"], ["end", "Финальный кадр"]];
const MAX_REFS = 14;
const open = new Set();      // раскрытые «Выбрать из библиотеки» / «Что уйдёт»: перерисовка их не закрывает
const picked = new Map();    // «шот:сторона:герой» -> выбранное состояние ("" — основное, иначе id состояния)
const extra = new Map();     // шот -> [id героев и локаций, добавленных из библиотеки сверх тех, что в шоте]

const STATUS = {
  done: ["Готов", "ok"], running: ["Рисуется…", "run"], queued: ["В очереди…", "run"],
  error: ["Ошибка", "err"], stub: ["Заглушка", "warn"],
};

// ---------- что можно выбрать в референсы ----------

/** Герои и локация шота (их версии из карточки) + добавленные вручную: [{key, asset, version}]. */
function groups(s) {
  const out = [];
  const add = (asset, versionId) => {
    if (!asset || out.some((g) => g.asset.id === asset.id)) return;
    const version = asset.versions.find((v) => v.id === versionId) || asset.versions.find((v) => v.id === asset.active_version_id) || asset.versions.at(-1);
    if (version) out.push({ key: String(asset.id), asset, version });
  };
  s.characters.forEach((c) => add(state.assets.find((a) => a.id === c.asset_id), c.version_id));
  if (s.location_mode !== "text" && s.location_version_id) {
    const asset = state.assets.find((a) => a.versions.some((v) => v.id === s.location_version_id));
    add(asset, s.location_version_id);
  }
  (extra.get(s.id) || []).forEach((id) => add(state.assets.find((a) => a.id === id)));
  return out;
}

/** Состояния версии как варианты выбора: «Основное» (фото самой версии) и добавленные состояния. */
function stateOptions(v) {
  const opts = [];
  if (v.images?.length) opts.push({ id: "", name: "Основное", images: v.images.map((p) => ({ path: p, name: (v.image_names || {})[p] || "" })) });
  (v.states || []).forEach((st) => opts.push({ id: String(st.id), name: st.name, images: st.images }));
  return opts;
}

const refName = (g, opt, img) => [g.asset.name + (g.version.label ? ` (${g.version.label})` : ""), opt.id ? opt.name : "", img.name].filter(Boolean).join(" · ");

function currentOption(s, side, g, refs) {
  const opts = stateOptions(g.version);
  const want = picked.get(`${s.id}:${side}:${g.key}`);
  return opts.find((o) => o.id === want) || opts.find((o) => o.images.some((i) => refs.some((r) => r.path === i.path))) || opts[0];
}

// ---------- разметка ----------

function refsStrip(f) {
  if (!f.refs.length) return `<div class="muted small">Картинок нет: кадр нарисуется только по промпту.</div>`;
  return `<div class="frefs">${f.refs.map((r, i) => `<figure class="fref" title="${esc(r.name)}">
      <a href="${media(r.path)}" target="_blank"><img src="${media(r.path)}"></a><b>Image ${i + 1}</b>
      <figcaption>${esc(r.name || "своя картинка")}</figcaption><button class="ghost small" data-frm="${i}" title="Убрать из референсов">✕</button></figure>`).join("")}</div>`;
}

function pickerHtml(s, side, f) {
  const gs = groups(s);
  const taken = new Set(gs.map((g) => g.asset.id));
  const more = state.assets.filter((a) => !taken.has(a.id));
  return gs.map((g) => {
    const opt = currentOption(s, side, g, f.refs);
    const opts = stateOptions(g.version);
    const head = `<div class="row fgroup-head"><b>${esc(g.asset.name)}</b><span class="muted small">${g.asset.kind === "location" ? "локация" : "персонаж"} · v${g.version.version_no} ${esc(g.version.label)}</span>
      ${opts.length > 1 ? `<select data-fstate="${g.key}" title="Состояние версии">${opts.map((o) => `<option value="${o.id}" ${o === opt ? "selected" : ""}>${esc(o.name)}</option>`).join("")}</select>` : ""}</div>`;
    if (!opt) return `<div class="fgroup">${head}<div class="muted small">В этой версии нет картинок. Добавьте их в «Библиотеке».</div></div>`;
    return `<div class="fgroup">${head}<div class="fthumbs">${opt.images.map((img) => {
      const on = f.refs.some((r) => r.path === img.path);
      return `<button type="button" class="fthumb ${on ? "on" : ""}" data-fpick="${esc(img.path)}" data-fname="${esc(refName(g, opt, img))}" data-fkind="${g.asset.kind}" title="${esc(img.name || opt.name)}">
        <img src="${media(img.path)}">${on ? `<i>✓</i>` : ""}<span>${esc(img.name || opt.name)}</span></button>`;
    }).join("")}</div></div>`;
  }).join("") + (more.length ? `<select data-fextra class="small"><option value="">＋ ещё персонаж или локация из библиотеки…</option>
      ${more.map((a) => `<option value="${a.id}">${a.kind === "location" ? "🏙 " : "👤 "}${esc(a.name)}</option>`).join("")}</select>` : "");
}

function frameHtml(s, side, title) {
  const f = s.frames[side];
  const [label, cls] = STATUS[f.status] || ["Не сделан", ""];
  const busy = ["queued", "running"].includes(f.status);
  const startReady = s.frames.start.ready;
  const img = f.ready ? `<a href="${media(f.path)}" target="_blank"><img src="${media(f.path)}" alt="${esc(title)}"></a>`
    : `<div class="frame-ph">${busy ? `<span class="spin"></span>Рисуется…` : "9:16"}</div>`;
  const k = `${s.id}:${side}`;
  return `<div class="frame ${f.ready ? "ready" : ""} ${f.status === "error" ? "bad" : ""}" data-frame="${side}">
    <div class="row"><b>${title}</b><span class="badge ${cls}">${label}</span><div class="spacer"></div>
      ${f.path && !busy ? `<button class="ghost small danger" data-freset="${side}" title="Убрать готовый кадр">Убрать кадр</button>` : ""}
      ${f.status && !f.path && !busy ? `<button class="ghost small" data-freset="${side}">Сбросить</button>` : ""}</div>
    <div class="frame-main">
      <div class="frame-img">${img}</div>
      <div class="frame-form">
        <label>Промпт для Nano Banana 2.1 <span class="muted small">— пишется руками; «@» открывает список референсов (Image 1, Image 2…)</span></label>
        <textarea data-fprompt rows="5" placeholder="${side === "start" ? "Image 1 в халате стоит у окна кухни Image 2, утро, мягкий свет, смотрит в камеру" : "Тот же кадр, но Image 1 улыбается и держит в руках телефон"}">${esc(f.prompt)}</textarea>
        <div class="row" style="margin-top:6px"><button class="primary" data-fgen ${busy ? "disabled" : ""}>${f.path ? "↻ Сделать заново" : "Сделать кадр"}</button>
          <span class="muted small">1K · 9:16 · Nano Banana 2.1</span></div>
        ${f.error ? `<div class="small ${f.status === "stub" ? "warn-t" : "err-t"}" style="margin-top:6px">${esc(f.error)}</div>` : ""}
      </div>
    </div>
    <label>Референсы ${f.refs.length}/${MAX_REFS} <span class="muted small">${f.auto_refs && f.refs.length ? "— по умолчанию: главные картинки героев и локации шота" : ""}</span></label>
    ${refsStrip(f)}
    <details data-fopen="${k}:lib" ${open.has(`${k}:lib`) ? "open" : ""}><summary class="small">Выбрать из библиотеки (версия и состояние)</summary>${pickerHtml(s, side, f)}</details>
    <div class="row" style="margin-top:6px">
      <div class="dropzone small-drop" data-fdrop tabindex="0">📷 Свои картинки: перетащите, вставьте (Ctrl+V) или нажмите
        <input type="file" multiple accept="image/png,image/jpeg,image/webp" hidden></div>
      ${side === "end" && startReady && !f.refs.some((r) => r.path === s.frames.start.path) ? `<button class="ghost small" data-fstartref title="Нарисованный стартовый кадр станет ещё одним референсом">＋ старт как референс</button>` : ""}</div>
    <details data-fopen="${k}:req" ${open.has(`${k}:req`) ? "open" : ""}><summary class="muted small" data-freq="${side}">Что уйдёт в Nano Banana 2.1</summary>
      <pre class="small" data-freq-out="${side}">нажмите, чтобы посмотреть</pre></details>
  </div>`;
}

export function framesHtml(s) {
  const f = s.frames;
  const ready = s.frames_ready;
  const running = ["queued", "running"].includes(s.gen_status);
  const last = s.takes.at(-1);
  return `<div class="frames-box ${ready ? "ready" : ""}" id="frames-${s.id}">
    <div class="row frames-head"><b>Кадры для Gemini Omni</b>
      <span class="muted small">сначала два кадра из Nano Banana 2.1, потом видео ${s.duration} с, 720p</span><div class="spacer"></div>
      ${ready ? `<span class="badge ok">✓ оба кадра готовы</span>` : `<span class="badge warn">${f.start.ready || f.end.ready ? "готов один кадр из двух" : "кадры не сделаны"}</span>`}
      ${ready ? `<button class="primary" data-fvideo ${running ? "disabled" : ""}>${running ? "Генерируется…" : last ? "▶ Сгенерировать ещё дубль" : "▶ Отправить в Gemini Omni"}</button>` : ""}</div>
    ${ready && !s.ready ? `<div class="small warn-t">Карточка шота заполнена не до конца: ${s.checks.filter((c) => !c.ok).map((c) => esc(c.label)).join("; ")}.</div>` : ""}
    <div class="frames-grid">${SIDES.map(([side, title]) => frameHtml(s, side, title)).join("")}</div>
  </div>`;
}

// ---------- действия ----------

/** hooks.update(shot) вносит ответ сервера в страницу и возвращает шот; hooks.full(shot) перерисовывает всю карточку. */
/** Подсказка по «@» в промпте: выпадающий список приложенных референсов, выбранный превращается в «Image N». */
function attachMentions(ta, getRefs) {
  const host = ta.parentElement;
  host.style.position = "relative";
  const pop = document.createElement("div");
  pop.className = "mention-pop";
  pop.hidden = true;
  host.appendChild(pop);
  let items = [], at = -1, sel = 0;
  const close = () => { pop.hidden = true; at = -1; };
  const pick = (i) => {
    const it = items[i];
    if (!it) return;
    const end = ta.selectionStart;
    ta.value = ta.value.slice(0, at) + it.token + " " + ta.value.slice(end);
    ta.selectionStart = ta.selectionEnd = at + it.token.length + 1;
    close();
    ta.focus();
  };
  const draw = () => {
    pop.innerHTML = items.length
      ? items.map((it, i) => `<div class="mention-item ${i === sel ? "on" : ""}" data-mi="${i}"><img src="${media(it.path)}"><b>${it.token}</b><span>${esc(it.name || "своя картинка")}</span></div>`).join("")
      : `<div class="mention-empty">Референсов нет: выберите их из библиотеки или добавьте свои ниже</div>`;
    pop.style.top = `${ta.offsetTop + ta.offsetHeight + 4}px`;
    pop.hidden = false;
    $$("[data-mi]", pop).forEach((el) => {
      el.onmousedown = (e) => { e.preventDefault(); pick(+el.dataset.mi); };
      el.onmouseenter = () => { sel = +el.dataset.mi; $$(".mention-item", pop).forEach((x, j) => x.classList.toggle("on", j === sel)); };
    });
  };
  ta.addEventListener("input", () => {
    const m = /@([^\s@]*)$/.exec(ta.value.slice(0, ta.selectionStart));
    if (!m) return close();
    at = ta.selectionStart - m[0].length;
    const q = m[1].toLowerCase();
    items = getRefs().map((r, i) => ({ ...r, token: `Image ${i + 1}` }))
      .filter((r) => !q || (r.name || "").toLowerCase().includes(q) || r.token.toLowerCase().replace(" ", "").startsWith(q.replace(" ", "")));
    sel = 0;
    draw();
  });
  ta.addEventListener("keydown", (e) => {
    if (pop.hidden || !items.length) return e.key === "Escape" && close();
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      sel = (sel + (e.key === "ArrowDown" ? 1 : items.length - 1)) % items.length;
      draw();
    } else if (e.key === "Enter" || e.key === "Tab") { e.preventDefault(); pick(sel); }
    else if (e.key === "Escape") { e.preventDefault(); close(); }
  });
  ta.addEventListener("blur", () => setTimeout(close, 120));
}

// Ctrl+V с картинкой в буфере: кладём её в тот кадр, над которым курсор или в котором стоит фокус (например, в его промпте).
const pasteImages = (e) => [...(e.clipboardData?.files || [])].filter((f) => /^image\/(png|jpeg|webp)$/.test(f.type));
document.addEventListener("paste", (e) => {
  const files = pasteImages(e);
  if (!files.length) return;
  const fr = document.activeElement?.closest?.("[data-frame]") || $("[data-frame]:hover");
  if (!fr?._upload) return;
  e.preventDefault();
  fr._upload(files);
});

export function bindFrames(s, hooks) {
  const box = $(`#frames-${s.id}`);
  if (!box) return;
  const cur = () => hooks.get(s.id) || s;
  const redraw = (shot) => {
    const sh = hooks.update(shot);
    const el = $(`#frames-${s.id}`);
    if (el) { el.outerHTML = framesHtml(sh); bindFrames(sh, hooks); }
    return sh;
  };
  const call = async (path, opts) => redraw(await api(`/api/shots/${s.id}/frames/${path}`, opts));
  $$("details[data-fopen]", box).forEach((d) => (d.ontoggle = () => (d.open ? open.add(d.dataset.fopen) : open.delete(d.dataset.fopen))));

  $$("[data-frame]", box).forEach((fr) => {
    const side = fr.dataset.frame;
    const f = () => cur().frames[side];
    const setRefs = (refs) => call(side, { method: "PUT", json: { refs } });
    const ta = $("[data-fprompt]", fr);
    attachMentions(ta, () => f().refs);
    // текст промпта сохраняем тихо, без перерисовки: иначе клик по соседней кнопке пропадает
    ta.onchange = async () => {
      const shot = await api(`/api/shots/${s.id}/frames/${side}`, { method: "PUT", json: { prompt: ta.value } });
      hooks.update(shot);
    };
    $("[data-fgen]", fr).onclick = () => call(`${side}/generate`, { json: { prompt: ta.value } }).then(() => toast(`${side === "start" ? "Стартовый" : "Финальный"} кадр отправлен в Nano Banana 2.1`));
    $$("[data-freset]", fr).forEach((b) => (b.onclick = () => call(side, { method: "DELETE" })));
    $$("[data-frm]", fr).forEach((b) => (b.onclick = () => setRefs(f().refs.filter((_, i) => i !== +b.dataset.frm))));
    $$("[data-fpick]", fr).forEach((b) => (b.onclick = () => {
      const refs = f().refs;
      const path = b.dataset.fpick;
      if (refs.some((r) => r.path === path)) return setRefs(refs.filter((r) => r.path !== path));
      if (refs.length >= MAX_REFS) return toast(`Nano Banana 2.1 принимает до ${MAX_REFS} картинок`, true);
      setRefs([...refs, { path, name: b.dataset.fname, kind: b.dataset.fkind }]);
    }));
    $$("[data-fstate]", fr).forEach((sel) => (sel.onchange = () => {
      picked.set(`${s.id}:${side}:${sel.dataset.fstate}`, sel.value);
      open.add(`${s.id}:${side}:lib`);
      redraw(cur());
    }));
    const ex = $("[data-fextra]", fr);
    if (ex) ex.onchange = () => {
      if (!ex.value) return;
      extra.set(s.id, [...(extra.get(s.id) || []), +ex.value]);
      open.add(`${s.id}:${side}:lib`);
      redraw(cur());
    };
    const sr = $("[data-fstartref]", fr);
    if (sr) sr.onclick = () => setRefs([...f().refs, { path: cur().frames.start.path, name: "стартовый кадр", kind: "frame" }].slice(0, MAX_REFS));
    const req = $("[data-freq]", fr);
    if (req) req.onclick = async () => {
      const out = $(`[data-freq-out="${side}"]`, fr);
      out.textContent = "…";
      out.textContent = JSON.stringify(await api(`/api/shots/${s.id}/frames/${side}/request`), null, 2);
    };
    const drop = $("[data-fdrop]", fr);
    const input = $("input", drop);
    const upload = async (files) => {
      const imgs = [...files].filter((x) => /^image\/(png|jpeg|webp)$/.test(x.type));
      if (!imgs.length) return toast("Подходят PNG, JPG и WEBP", true);
      const fd = new FormData();
      imgs.forEach((x) => fd.append("images", x));
      toast(`Загружаю картинок: ${imgs.length}…`);
      redraw(await api(`/api/shots/${s.id}/frames/${side}/upload`, { method: "POST", body: fd }));
    };
    fr._upload = upload; // Ctrl+V: см. слушатель ниже
    drop.onclick = () => input.click();
    input.onclick = (e) => e.stopPropagation();
    input.onchange = () => { upload(input.files); input.value = ""; };
    drop.addEventListener("dragover", (e) => { e.preventDefault(); drop.classList.add("drop-over"); });
    drop.addEventListener("dragleave", () => drop.classList.remove("drop-over"));
    drop.addEventListener("drop", (e) => { e.preventDefault(); drop.classList.remove("drop-over"); upload(e.dataTransfer.files); });
    drop.addEventListener("paste", (e) => { if (e.clipboardData.files.length) { e.preventDefault(); upload(e.clipboardData.files); } });
  });

  const video = $("[data-fvideo]", box);
  if (video) video.onclick = async () => {
    video.disabled = true;
    try {
      hooks.full(await api(`/api/shots/${s.id}/generate`, { json: {} }));
      toast(`Шот ${s.label} отправлен в Gemini Omni`);
    } catch { video.disabled = false; }
  };
}

/** Кадры ещё рисуются (нужно ли опрашивать сервер). */
export const framesBusy = (s) => s.frames && Object.values(s.frames).some((f) => ["queued", "running"].includes(f.status));
/** Для подписи состояния страницы: перерисовывать, когда кадр поменял статус. */
export const framesSig = (s) => (s.frames ? `${s.frames.start.status}/${s.frames.end.status}` : "");
