// «Сообщить о проблеме» (кнопка внизу меню) и страница «Заявки» для админа.
// Заявка хранится на сайте; снимок экрана видит только админ. Нужные заявки админ отправляет в правки:
// тогда они становятся задачей на GitHub с меткой «правка», и её подхватывает Claude.
import { $, $$, api, closeModal, esc, fmtDateTime, modal, state, toast, view } from "./core.js";

const KINDS = [
  ["bug", "Что-то сломалось", "Не сохраняется, не открывается, ошибка",
    `<path d="M12 9v4"/><path d="M12 17h.01"/><path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/>`],
  ["ux", "Неудобно или криво", "Вёрстка поехала, не туда нажимается",
    `<path d="M4 6h10"/><path d="M18 6h2"/><circle cx="16" cy="6" r="2"/><path d="M4 18h4"/><path d="M12 18h8"/><circle cx="10" cy="18" r="2"/>`],
  ["idea", "Есть идея", "Как сделать лучше",
    `<path d="M9 18h6"/><path d="M10 22h4"/><path d="M12 2a7 7 0 0 0-4 12.7V16h8v-1.3A7 7 0 0 0 12 2z"/>`],
];
const icon = (path, cls = "") => `<svg class="${cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${path}</svg>`;
const FLAG = `<path d="M4 22V4"/><path d="M4 4h12l-2 4 2 4H4"/>`;

let shot = null; // Blob снимка
let kind = "bug";

async function capture() {
  try {
    const { default: html2canvas } = await import("https://cdn.jsdelivr.net/npm/html2canvas-pro@1/+esm");
    const canvas = await html2canvas(document.body, {
      x: scrollX, y: scrollY, width: innerWidth, height: innerHeight, scale: Math.min(1, 1600 / innerWidth),
      backgroundColor: getComputedStyle(document.body).backgroundColor, logging: false,
      ignoreElements: (el) => el.id === "modal" || el.id === "toast",
    });
    return await new Promise((ok) => canvas.toBlob(ok, "image/jpeg", 0.85));
  } catch (e) {
    console.warn("снимок не получился", e);
    return null;
  }
}

export async function openReport() {
  toast("Делаю снимок страницы…");
  shot = await capture();
  kind = "bug";
  const m = modal(`<div class="rp">
    <div class="rp-head">${icon(FLAG, "rp-flag")}<h1>Сообщить о проблеме</h1><div class="spacer"></div>
      <button class="ghost rp-x" id="rp-cancel-x" title="Закрыть">✕</button></div>
    <div class="rp-kinds">${KINDS.map(([k, n, d, p]) => `<button class="rp-kind ${k === kind ? "on" : ""}" data-kind="${k}">
      <span class="rp-ico">${icon(p)}</span><b>${n}</b><span class="muted">${d}</span></button>`).join("")}</div>
    <label class="rp-label">Что случилось?</label>
    <textarea id="rp-text" rows="5" placeholder="Что вы делали и что пошло не так — по шагам, как было"></textarea>
    <label class="rp-label">Снимок экрана 🔒</label>
    <div class="rp-shot" id="rp-shot"></div>
    <p class="muted small rp-note">🔒 Снимок увидит <b>только админ студии</b> и Claude, если админ передаст заявку в правки.
      На нём весь экран — если видно что-то лишнее, уберите снимок или приложите свой.</p>
    <div class="rp-foot"><span class="muted small">Приложим адрес страницы и версию сайта</span><div class="spacer"></div>
      <button class="ghost" id="rp-cancel">Отмена</button><button class="primary" id="rp-send">Отправить</button></div>
  </div>`, true);
  drawShot();
  $$("[data-kind]", m).forEach((b) => (b.onclick = () => {
    kind = b.dataset.kind;
    $$("[data-kind]", m).forEach((x) => x.classList.toggle("on", x === b));
    $("#rp-text").placeholder = kind === "idea" ? "Что хочется улучшить и как это видите" : "Что вы делали и что пошло не так — по шагам, как было";
  }));
  $("#rp-cancel").onclick = $("#rp-cancel-x").onclick = closeModal;
  $("#rp-send").onclick = send;
  $("#modal").addEventListener("close", () => window.removeEventListener("paste", onPaste), { once: true });
  window.addEventListener("paste", onPaste);
  setTimeout(() => $("#rp-text")?.focus(), 50);
}

function drawShot() {
  const box = $("#rp-shot");
  if (!box) return;
  box.innerHTML = `${shot ? `<img src="${URL.createObjectURL(shot)}" alt="Снимок">` : `<div class="rp-noshot muted">Без снимка</div>`}
    <div><b>${shot ? "Снимок этой страницы" : "Снимка нет"}</b>
      <div class="muted small">${shot ? "Сделан сам, когда вы открыли окно" : "Можно приложить свой файл"}</div>
      <div class="row" style="margin-top:10px">${shot ? `<button class="ghost small" id="rp-drop">✕ Убрать снимок</button>` : ""}
        <label class="btn ghost small" style="margin:0">📎 Приложить свой<input type="file" accept="image/*" id="rp-file" hidden></label></div>
      <div class="muted small" style="margin-top:6px">или ⌘V</div></div>`;
  $("#rp-drop") && ($("#rp-drop").onclick = () => { shot = null; drawShot(); });
  $("#rp-file").onchange = (e) => { if (e.target.files[0]) { shot = e.target.files[0]; drawShot(); } };
}

function onPaste(e) {
  const img = [...(e.clipboardData?.files || [])].find((f) => f.type.startsWith("image/"));
  if (img) { e.preventDefault(); shot = img; drawShot(); }
}

async function send() {
  const text = $("#rp-text").value.trim();
  if (!text) { $("#rp-text").focus(); return toast("Напишите, что случилось", true); }
  const fd = new FormData();
  fd.set("kind", kind);
  fd.set("text", text);
  fd.set("page", location.hash || "#/");
  fd.set("viewport", `${innerWidth}×${innerHeight}`);
  if (shot) fd.append("image", shot, shot.name || "snimok.jpg");
  const btn = $("#rp-send");
  btn.disabled = true;
  btn.textContent = "Отправляю…";
  try {
    await api("/api/reports", { method: "POST", body: fd });
  } catch {
    btn.disabled = false;
    btn.textContent = "Отправить";
    return;
  }
  closeModal();
  shot = null;
  toast("Спасибо! Заявка ушла админу студии");
}

// ---------- страница «Заявки» (только админ) ----------

const ST_CLS = { new: "warn", work: "run", done: "ok", rejected: "" };

export async function renderReports() {
  const { reports, statuses } = await api("/api/reports");
  const open = reports.filter((r) => r.status === "new" || r.status === "work");
  const past = reports.filter((r) => !(r.status === "new" || r.status === "work"));
  view().innerHTML = `<div class="row"><h1>Заявки</h1><div class="spacer"></div>
      <span class="muted small">Из кнопки «Сообщить о проблеме». Нужное отправьте в правки — их внесёт Claude.</span></div>
    ${open.length ? open.map((r) => card(r, statuses)).join("") : `<div class="card muted">Новых заявок нет.</div>`}
    ${past.length ? `<details class="rp-past"><summary class="muted">Сделанные и отклонённые: ${past.length}</summary>${past.map((r) => card(r, statuses)).join("")}</details>` : ""}`;
  $$("[data-rp-status]").forEach((s) => (s.onchange = async () => {
    await api(`/api/reports/${s.dataset.rpStatus}`, { method: "PUT", json: { status: s.value } });
    toast("Статус заявки изменён");
    renderReports();
  }));
  $$("[data-rp-forward]").forEach((b) => (b.onclick = async () => {
    const id = b.dataset.rpForward;
    const withShot = $(`[data-rp-withshot="${id}"]`)?.checked || false;
    b.disabled = true;
    try {
      const r = await api(`/api/reports/${id}/forward`, { json: { with_shot: withShot } });
      toast(`Отправлено в правки: задача #${r.number}. Claude подхватит её сам`);
    } catch { b.disabled = false; return; }
    renderReports();
  }));
}

function card(r, statuses) {
  const k = KINDS.find(([key]) => key === r.kind) || KINDS[0];
  return `<div class="card rp-card rp-${r.kind}">
    <div class="row"><span class="rp-ico small">${icon(k[3])}</span><b>${esc(k[1])}</b>
      <span class="muted small">${esc(r.author)} · ${fmtDateTime(r.created_at)}${r.version ? ` · версия ${esc(r.version)}` : ""}</span>
      <div class="spacer"></div>
      <select data-rp-status="${r.id}" style="width:auto" class="rp-st ${ST_CLS[r.status]}">${Object.entries(statuses).map(([s, n]) => `<option value="${s}" ${s === r.status ? "selected" : ""}>${n}</option>`).join("")}</select></div>
    <p class="rp-text">${esc(r.text)}</p>
    <div class="row small">${r.page ? `<a href="${esc(r.page)}">${esc(r.page)}</a>` : ""}<span class="muted">${esc(r.viewport)}</span></div>
    ${r.has_shot ? `<a href="/api/reports/${r.id}/shot" target="_blank" class="rp-thumb"><img src="/api/reports/${r.id}/shot" loading="lazy" alt="Снимок"></a>` : ""}
    <div class="row" style="margin-top:8px">${r.issue_number
      ? `<span class="badge run">в правках: задача #${r.issue_number}</span>`
      : `${r.has_shot ? `<label class="check small" title="Репозиторий на GitHub публичный: снимок там увидят все"><input type="checkbox" data-rp-withshot="${r.id}"> приложить снимок (он станет публичным)</label>` : ""}
         <div class="spacer"></div><button class="primary small" data-rp-forward="${r.id}">Отправить в правки Claude</button>`}</div>
  </div>`;
}

export const reportsAllowed = () => state.meta?.user?.role === "admin";
