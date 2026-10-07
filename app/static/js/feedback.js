// «Правка»: corner button for notes about the studio itself (text + screenshots).
// Notes are GitHub issues, so everyone working on the code sees them; Claude picks them up on «Запусти правки».
import { $, $$, api, can, esc, fmtDateTime, toast } from "./core.js";

const STATUS = { new: ["ждёт", "warn"], work: ["в работе", "work"], done: ["сделано", "ok"], closed: ["снята", ""] };
let notes = [];
let notesError = "";
let files = [];
let draft = "";
let tab = "new";
let ready = false;

export function initFeedback() {
  if (ready) return;
  ready = true;
  const box = document.createElement("div");
  box.id = "fb";
  box.innerHTML = `<div class="fb-panel" hidden></div><button class="fb-btn">Правка</button>`;
  document.body.append(box);
  $(".fb-btn", box).onclick = () => toggle();
  window.addEventListener("paste", onPaste);
  load();
}

export function stopFeedback() {
  $("#fb")?.remove();
  window.removeEventListener("paste", onPaste);
  ready = false;
}

const panel = () => $("#fb .fb-panel");
const canWrite = () => can.write() || can.gen();

async function load() {
  try { notes = await api("/api/feedback", { quiet: true }); } catch (e) { notes = null; notesError = e.message; }
  const open = (notes || []).filter((n) => n.status === "new" || n.status === "work").length;
  $("#fb .fb-btn").textContent = open ? `Правка · ${open}` : "Правка";
  if (!panel().hidden && tab === "list") draw();
}

function toggle(force) {
  const p = panel();
  p.hidden = force === undefined ? !p.hidden : !force;
  if (!p.hidden) { tab = canWrite() ? "new" : "list"; draw(); load(); }
}

function draw() {
  const p = panel();
  p.innerHTML = `<div class="fb-tabs">
      ${canWrite() ? `<button data-tab="new" class="${tab === "new" ? "on" : ""}">Новая правка</button>` : ""}
      <button data-tab="list" class="${tab === "list" ? "on" : ""}">Все правки${notes ? ` (${notes.length})` : ""}</button>
      <div class="spacer"></div><button class="ghost fb-x" title="Закрыть">×</button></div>
    <div class="fb-body">${tab === "new" ? newForm() : list()}</div>`;
  $(".fb-x", p).onclick = () => toggle(false);
  $$("[data-tab]", p).forEach((b) => (b.onclick = () => { tab = b.dataset.tab; draw(); }));
  tab === "new" ? bindForm() : bindList();
}

function newForm() {
  return `<textarea id="fb-text" rows="5" placeholder="Что не так и как должно быть?">${esc(draft)}</textarea>
    <label class="fb-drop">Скриншот: ⌘V, перетащить сюда или нажать, чтобы выбрать файл
      <input type="file" accept="image/*" multiple hidden></label>
    <div class="fb-thumbs">${files.map((f, i) => `<span><img src="${URL.createObjectURL(f)}"><button class="ghost small" data-unfile="${i}">×</button></span>`).join("")}</div>
    <p class="muted small">Страница и размер окна запишутся сами. Правку увидят все, кто работает над студией на GitHub.</p>
    <div class="row"><button class="primary" id="fb-save">Отправить правку</button><span class="muted small">⌘↵</span></div>`;
}

function bindForm() {
  const p = panel();
  const area = $("#fb-text", p);
  area.focus();
  area.oninput = () => (draft = area.value);
  area.onkeydown = (e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) save(); };
  const drop = $(".fb-drop", p);
  drop.ondragover = (e) => e.preventDefault();
  drop.ondrop = (e) => { e.preventDefault(); addFiles(e.dataTransfer.files); };
  $("input[type=file]", drop).onchange = (e) => addFiles(e.target.files);
  $$("[data-unfile]", p).forEach((b) => (b.onclick = () => { files.splice(+b.dataset.unfile, 1); keepText(); }));
  $("#fb-save", p).onclick = save;
}

/** Redraw the form after adding/removing a screenshot (the typed text lives in `draft`). */
function keepText() {
  draw();
}

function addFiles(list) {
  files.push(...[...list].filter((f) => f.type.startsWith("image/")));
  keepText();
}

function onPaste(e) {
  if (panel()?.hidden || tab !== "new") return;
  const imgs = [...(e.clipboardData?.files || [])].filter((f) => f.type.startsWith("image/"));
  if (imgs.length) { e.preventDefault(); addFiles(imgs); }
}

async function save() {
  const btn = $("#fb-save");
  const fd = new FormData();
  fd.set("text", $("#fb-text").value);
  fd.set("page", location.hash || "#/");
  fd.set("viewport", `${innerWidth}×${innerHeight}`);
  files.forEach((f) => fd.append("images", f));
  btn.disabled = true;
  btn.textContent = "Отправляю…";
  try {
    await api("/api/feedback", { method: "POST", body: fd });
  } catch {
    btn.disabled = false;
    btn.textContent = "Отправить правку";
    return;
  }
  files = [];
  draft = "";
  toast("Правка отправлена");
  tab = "list";
  draw();
  load();
}

function list() {
  if (!notes) return `<p>${esc(notesError || "Загружаю…")}</p>`;
  if (!notes.length) return `<p class="muted">Правок пока нет.</p>`;
  return notes.map((n) => {
    const [label, cls] = STATUS[n.status];
    return `<div class="fb-note ${n.status === "done" || n.status === "closed" ? "past" : ""}" data-n="${n.number}">
      <div class="row"><span class="fb-st ${cls}">${label}</span><span class="muted small">${esc(n.author)} · ${fmtDateTime(n.created_at.replace("Z", ""))}</span>
        <div class="spacer"></div><a href="${esc(n.url)}" target="_blank" class="small">#${n.number}</a></div>
      <p>${esc(n.text || "Без текста")}</p>
      ${n.page ? `<a class="small" href="${esc(n.page)}">${esc(n.page)}</a>` : ""}
      ${n.shots.length ? `<div class="fb-thumbs">${n.shots.map((s) => `<a href="/api/feedback-shot?path=${encodeURIComponent(s)}" target="_blank"><img src="/api/feedback-shot?path=${encodeURIComponent(s)}" loading="lazy"></a>`).join("")}</div>` : ""}
      <div class="row small">
        <button class="ghost small" data-thread="${n.number}">Обсуждение${n.comments ? ` (${n.comments})` : ""}</button>
        ${canWrite() && (n.status === "new" || n.status === "work") ? `<button class="ghost small danger" data-withdraw="${n.number}">Снять</button>` : ""}
      </div>
      <div class="fb-thread" hidden></div>
    </div>`;
  }).join("");
}

function bindList() {
  $$("[data-thread]").forEach((b) => (b.onclick = () => openThread(+b.dataset.thread)));
  $$("[data-withdraw]").forEach((b) => (b.onclick = async () => {
    if (!confirm("Снять эту правку? Её не будут вносить.")) return;
    await api(`/api/feedback/${b.dataset.withdraw}/withdraw`, { json: {} });
    load();
  }));
}

async function openThread(number) {
  const box = $(`.fb-note[data-n="${number}"] .fb-thread`);
  if (!box.hidden) { box.hidden = true; return; }
  box.hidden = false;
  box.innerHTML = `<p class="muted small">Загружаю…</p>`;
  const comments = await api(`/api/feedback/${number}/comments`);
  box.innerHTML = (comments.length
    ? comments.map((c) => `<div class="fb-comment"><span class="muted small">${esc(c.author)} · ${fmtDateTime(c.created_at.replace("Z", ""))}</span><div>${esc(c.body)}</div></div>`).join("")
    : `<p class="muted small">Пока без ответов.</p>`)
    + (canWrite() ? `<textarea rows="2" placeholder="Ответить или уточнить"></textarea>
      <div class="row"><div class="spacer"></div><button class="small" data-reply>Отправить</button></div>` : "");
  const reply = $("[data-reply]", box);
  if (reply) reply.onclick = async () => {
    const text = $("textarea", box).value;
    await api(`/api/feedback/${number}/comments`, { json: { text } });
    box.hidden = true;
    openThread(number);
    load();
  };
}
