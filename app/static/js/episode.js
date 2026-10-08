// Episode card: script editor with live analysis, shots, review & comments, history.
import {
  $, $$, api, assetColor, can, closeModal, engineName, esc, fitMeter, fmtDate, fmtDateTime, fmtDur, goBack, loadAssets, media, modal, replaceHash, state,
  statusPill, statusSelect, STATUS_COLORS, toast, view,
} from "./core.js";
import { attachMentions } from "./mention.js";
import { attachHighlight } from "./highlight.js";
import { drawReview, stopReview } from "./review.js";

export const E = { id: null, ep: null, tab: "script", sel: null, poll: null, sig: "" };
export const TAKE_STATUS = { queued: ["в очереди", "run"], running: ["генерация…", "run"], done: ["готово", "ok"],
  stub: ["заглушка", "warn"], error: ["ошибка", "err"], idle: ["не создано", ""] };

export const SCRIPT_HELP = `<details class="help script-help" open><summary>Формат сценария</summary>
  <code>Шот 1</code>, <code>Шот 2</code>… — каждый шот отдельным блоком. Всё, что ниже пометки, относится к этому шоту.<br>
  <code>ИНТ. КАФЕ — ДЕНЬ</code> или <code>ЛОКАЦИЯ: @Кафе</code> — сцена/локация для следующих шотов.<br>
  <code>МАША: Привет!</code>, <code>Маша (шёпотом): Привет!</code>, <code>— Привет, — сказала Маша.</code> — реплики, по ним считается хронометраж.<br>
  Реплики можно писать прямо в тексте действия в кавычках: <code>Маша тихо говорит: «Привет!»</code> — кавычки сами уйдут в реплики шота, говорящий и манера определятся по контексту (не определились — поля подсвечены красным).<br>
  <code>КАМЕРА: крупный план</code> или <code>[наезд]</code> — камера. <code>@</code> — выбрать персонажа/локацию из библиотеки, <code>@Маша:Пижама</code> — конкретная версия.<br>
  Без пометок «Шот» приложение само разобьёт текст: каждый абзац действия — шот, длинные реплики делятся по 8 с.</details>`;

export function stopEpisodePolling() { clearTimeout(E.poll); stopReview(); }

export async function renderEpisode(id, tab) {
  stopEpisodePolling();
  if (E.id !== id) { E.sel = null; }
  E.id = id;
  if (tab) E.tab = ["script", "shots", "review", "main", "history"].includes(tab) ? tab : "script";
  const [ep] = await Promise.all([api(`/api/episodes/${id}`), loadAssets()]);
  E.ep = ep;
  if (!E.sel || !ep.shots.some((s) => s.id === E.sel)) E.sel = ep.shots[0]?.id ?? null;
  E.sig = signature(ep);
  draw();
  schedulePoll();
}

function signature(ep) { return ep.shots.map((s) => `${s.id}:${s.takes.map((t) => t.status).join(",")}:${s.selected_take_id}`).join("|") + ep.status; }

function schedulePoll() {
  stopEpisodePolling();
  const busy = E.ep.shots.some((s) => ["queued", "running"].includes(s.gen_status));
  if (!busy) return;
  E.poll = setTimeout(async () => {
    if (!location.hash.startsWith(`#/episodes/${E.id}`)) return;
    const ep = await api(`/api/episodes/${E.id}`);
    const sig = signature(ep);
    if (sig !== E.sig) {
      E.ep = ep; E.sig = sig;
      if (!["script", "main"].includes(E.tab)) { drawTab(); drawHeader(); } // never wipe the text being edited
    }
    schedulePoll();
  }, 4000);
}

function draw() {
  view().innerHTML = `<div id="ep-header"></div>
    <div class="tabs">
      ${[["script", "Сценарий"], ["shots", "Шоты"], ["review", "Просмотр и правки"], ["main", "Информация"], ["history", "История"]]
        .map(([k, n]) => `<button class="tab ${E.tab === k ? "on" : ""}" data-tab="${k}">${n}</button>`).join("")}
    </div>
    <div id="ep-tab"></div>`;
  $$("[data-tab]").forEach((b) => (b.onclick = () => replaceHash(`#/episodes/${E.id}/${b.dataset.tab}`)));
  drawHeader();
  drawTab();
}

// The writer cannot touch a script once the producer has approved it.
const writerLocked = () => state.meta.user.role === "writer" && !["dev", "review"].includes(E.ep.status);
const canWrite = () => can.write() && !writerLocked();

const canPost = () => can.write(); // описание поста — сценарист и продюсер, на любом этапе

export async function copyText(text) {
  if (!text.trim()) return toast("Описание для поста пока пустое", true);
  // Сначала через скрытое поле (работает сразу по клику, без разрешений), иначе — Clipboard API
  const ta = Object.assign(document.createElement("textarea"), { value: text, readOnly: true });
  ta.style.cssText = "position:fixed;opacity:0;top:0;left:0";
  document.body.append(ta);
  ta.select();
  let ok = false;
  try { ok = document.execCommand("copy"); } catch {}
  ta.remove();
  if (!ok) {
    try { await navigator.clipboard.writeText(text); ok = true; } catch {}
  }
  toast(ok ? "Скопировано" : "Не удалось скопировать: выделите текст и нажмите ⌘C", !ok);
}

export function drawHeader() {
  // Пока автор пишет сценарий, шапка короткая: номер, название, синопсис. Всё остальное — на других вкладках.
  if (E.tab === "script") return drawScriptHeader();
  const ep = E.ep, max = state.meta.max_shot_seconds;
  const withVideo = ep.shots.filter((s) => s.selected_take_id).length;
  const fixes = ep.shots.reduce((n, s) => n + (s.open_fixes || 0), 0);
  const filled = ep.shots.length - ep.incomplete;
  $("#ep-header").innerHTML = `
    <div class="ep-head">
      <div class="row">
        ${titleRow(ep)}
        <a class="btn" href="/api/episodes/${ep.id}/export" title="Выгрузить серию в JSON">JSON</a>
        ${can.gen() ? `<select id="gen-mode" style="width:auto">
            <option value="missing">шоты без видео</option><option value="redo">шоты с правками</option><option value="all">все шоты заново</option></select>
          <button class="primary" id="gen" title="Через API уходят шоты, которые делаются в Veo. Остальные монтажёр присылает готовым файлом.">▶ Отправить в генерацию</button>` : ""}
      </div>
      <div class="muted small" style="margin:6px 0 0 2px">${ep.arc_id ? `Арка «${esc(ep.arc_title)}» · ` : ""}${ep.date ? `Выход: ${fmtDate(ep.date)}${ep.pinned ? " 📌 дата закреплена" : ""}` : "Черновик — в очередь не идёт"}
            · <a href="#" id="move-ep">${ep.number != null ? "перенести" : "назначить дату"}</a></div>
      <div class="summary">
        <span><b>${ep.shots.length}</b> шотов</span>
        <span>хронометраж <b>${fmtDur(ep.total_seconds)}</b> <span class="muted">(по тексту ~${fmtDur(ep.est_seconds)})</span></span>
        ${ep.over_limit ? `<span class="warn-t">⚠ не влезают в ${max} с: <b>${ep.over_limit}</b></span>` : `<span class="ok-t">✓ все шоты влезают в клип</span>`}
        ${ep.shots.length ? (ep.incomplete
          ? `<span class="warn-t" title="Согласовать сценарий можно, когда у каждого шота выбрана локация, заполнены реплики и текст влезает в лимит">заполнено <b>${filled}/${ep.shots.length}</b> шотов</span>`
          : `<span class="ok-t">✓ все шоты заполнены</span>`) : ""}
        <span>видео <b>${withVideo}/${ep.shots.length}</b></span>
        ${fixes ? `<span class="warn-t">открытых правок: <b>${fixes}</b></span>` : ""}
        <span class="muted">${state.meta.veo_provider === "gemini" ? "Veo API" : "режим заглушки"}</span>
      </div>
      <div class="timeline">${ep.shots.map((s) => `<span data-jump="${s.id}" title="Шот ${esc(s.label)}: ${s.duration} с, по тексту ${s.est_seconds} с"
        class="${s.over_limit ? "over" : ""} ${s.missing.length ? "incomplete" : ""} ${s.selected_take_id ? "has-video" : ""}" style="flex:${s.duration}">${esc(s.label)}</span>`).join("")}</div>
    </div>`;
  bindTitleRow(ep);
  if (can.gen()) {
    $("#gen").onclick = async () => {
      const mode = $("#gen-mode").value;
      if (mode === "all" && !confirm("Сгенерировать заново все шоты? Каждый шот получит новый дубль (это платно при работе через API).")) return;
      const r = await api(`/api/episodes/${E.id}/generate`, { json: { mode } });
      const rest = r.skipped ? ` Ещё ${r.skipped} шотов делаются в других нейросетях: загрузите готовые видео в карточках шотов.` : "";
      toast(r.started ? `Отправлено в Veo: ${r.started} шотов.${rest}` : `Через API нечего отправлять.${rest || " У всех шотов Veo уже есть видео."}`);
      renderEpisode(E.id);
    };
  }
  $("#move-ep").onclick = (e) => { e.preventDefault(); moveDialog(); };
  $$("[data-jump]").forEach((el) => (el.onclick = () => {
    E.sel = +el.dataset.jump;
    drawTab();
    $(`#shot-${E.sel}`)?.scrollIntoView({ behavior: "smooth", block: "start" });
  }));
}

/** Назад, номер, название и статус серии: общая строка обеих шапок. */
function titleRow(ep) {
  return `<button class="ghost" id="ep-back" title="Назад">←</button>
    <div class="ep-badge" style="--c:${STATUS_COLORS[ep.status]}">${ep.number != null ? `Серия ${ep.arc_number ?? ep.number}` : "черновик"}</div>
    <div style="flex:1;min-width:220px">
      <input id="ep-title" class="title-input" value="${esc(ep.title)}" placeholder="Название серии" ${canWrite() ? "" : "disabled"}>
    </div>
    ${statusSelect(ep.status, 'id="ep-status"')}`;
}

function bindTitleRow(ep) {
  $("#ep-back").onclick = () => goBack(`#/series/${ep.arc_id ?? "none"}`);
  $("#ep-status").onchange = async (e) => {
    const status = e.target.value;
    try {
      await api(`/api/episodes/${E.id}/status`, { json: { status }, quiet: true });
    } catch (err) {
      // 409: the script is not fully filled in. The producer may approve it anyway.
      if (err.status === 409 && can.admin() && confirm(`${err.message}\n\nСогласовать всё равно?`)) {
        await api(`/api/episodes/${E.id}/status`, { json: { status, force: true } });
      } else {
        if (err.message !== "auth") toast(err.message, true);
        e.target.value = E.ep.status;
        return;
      }
    }
    await renderEpisode(E.id);
    toast("Статус изменён");
  };
  if (canWrite()) {
    $("#ep-title").onchange = async (e) => { await api(`/api/episodes/${E.id}`, { method: "PUT", json: { title: e.target.value } }); toast("Название сохранено"); };
  }
}

function drawScriptHeader() {
  const ep = E.ep;
  $("#ep-header").innerHTML = `
    <div class="ep-head ep-head-min">
      <div class="row">${titleRow(ep)}</div>
      <label>Синопсис <span class="muted small">— коротко о чём серия; из него пишется ТЗ</span></label>
      <textarea id="ep-synopsis" rows="3" placeholder="О чём серия…" ${canWrite() ? "" : "readonly"}>${esc(ep.synopsis || "")}</textarea>
    </div>`;
  bindTitleRow(ep);
  $("#ep-synopsis").onchange = async (ev) => {
    await api(`/api/episodes/${E.id}?reparse=false`, { method: "PUT", json: { synopsis: ev.target.value } });
    ep.synopsis = ev.target.value;
    toast("Синопсис сохранён");
  };
}

const canResult = () => can.write() || can.gen(); // итог и ссылку на ролик пишет любой из команды, кроме зрителя

/** Превью по ссылке: ролик со ссылки Dropbox (?raw=1) или прямой ссылки на файл — как видео; иначе просто ссылка. */
function resultPreview(url) {
  if (!url) return `<div class="res-empty muted small">Когда ролик будет готов, вставьте ссылку: здесь появится превью.</div>`;
  let u;
  try { u = new URL(url); } catch { return `<div class="res-empty muted small">Не похоже на ссылку.</div>`; }
  const link = `<a href="${esc(u.href)}" target="_blank" rel="noopener">Открыть ролик ↗</a>`;
  let src = "";
  if (/(^|\.)dropbox\.com$/.test(u.hostname)) {
    const d = new URL(u.href);
    d.searchParams.delete("dl");
    d.searchParams.set("raw", "1");
    src = d.href;
  } else if (/\.(mp4|mov|m4v|webm)$/i.test(u.pathname)) src = u.href;
  if (!src) return `<div class="res-empty muted small">Превью для этой ссылки не получить, но ролик откроется по ссылке.<br>${link}</div>`;
  return `<video class="res-video" src="${esc(src)}#t=0.1" controls preload="metadata" playsinline></video>
    <div class="small res-link">${link}</div>`;
}

function bindResult(ep) {
  const save = async () => {
    await api(`/api/episodes/${E.id}/result`, { json: { note: $("#res-note").value, url: $("#res-url").value } });
    ep.result_note = $("#res-note").value; ep.result_url = $("#res-url").value.trim();
    $("#res-preview").innerHTML = resultPreview(ep.result_url);
    bindVideoFallback();
    toast("Итог сохранён");
  };
  const bindVideoFallback = () => {
    const v = $("#res-preview video");
    if (v) v.onerror = () => { $("#res-preview").innerHTML = `<div class="res-empty muted small">Не удалось показать превью (ссылка закрыта или это не видеофайл).<br><a href="${esc(ep.result_url)}" target="_blank" rel="noopener">Открыть ролик ↗</a></div>`; };
  };
  bindVideoFallback();
  if (canResult()) { $("#res-note").onchange = save; $("#res-url").onchange = save; }
}

function moveDialog() {
  const ep = E.ep;
  const m = modal(`<h1>Дата выхода</h1>
    <label>Дата выхода</label><input id="mv-date" type="date" value="${ep.date ?? ""}" style="width:200px">
    <label class="check"><input type="checkbox" id="mv-pin" ${ep.pinned ? "checked" : ""}> закрепить дату (серия не сдвинется при перестановках — для праздников и инфоповодов)</label>
    <div class="row" style="margin-top:16px"><button class="ghost" id="backlog">Убрать в черновики</button><div class="spacer"></div>
      <button class="ghost" id="cancel">Отмена</button><button class="primary" id="save">Сохранить</button></div>`);
  $("#cancel", m).onclick = closeModal;
  $("#backlog", m).onclick = async () => { await api(`/api/episodes/${E.id}/move`, { json: { number: null } }); closeModal(); renderEpisode(E.id); };
  $("#save", m).onclick = async () => {
    const d = $("#mv-date", m).value;
    if (d !== (ep.date ?? "")) await api(`/api/episodes/${E.id}/move`, { json: d ? { date: d } : { number: null } });
    await api(`/api/episodes/${E.id}/pin`, { json: { pinned: $("#mv-pin", m).checked } });
    closeModal(); renderEpisode(E.id);
  };
}

function drawTab() {
  const el = $("#ep-tab");
  if (E.tab === "script") return drawScript(el);
  if (E.tab === "shots") return drawShots(el);
  if (E.tab === "review") return drawReview(el);
  if (E.tab === "main") return drawMain(el);
  if (E.tab === "history") return drawHistory(el);
}

/** Вкладка «Информация»: описание для поста, итог и готовый ролик. */
function drawMain(el) {
  const ep = E.ep;
  el.innerHTML = `<div class="ep-main">
      <details class="post-text" open>
        <summary>Описание для поста ${ep.post_text ? `<span class="muted small">· ${ep.post_text.length} симв.</span>` : `<span class="muted small">· пока не написано</span>`}</summary>
        <textarea id="post-text" rows="4" placeholder="Текст под роликом: подпись, хэштеги, призыв…" ${canPost() ? "" : "readonly"}>${esc(ep.post_text || "")}</textarea>
        <div class="row"><button class="ghost small" id="copy-post">📋 Скопировать</button>
          <span class="muted small">${canPost() ? "Пишет сценарист; сохраняется само." : "Пишет сценарист."}</span></div>
      </details>
      <details class="post-text result-box" open>
        <summary>Итог и готовый ролик ${ep.result_url ? `<span class="muted small">· ссылка есть</span>` : `<span class="muted small">· пока не заполнено</span>`}</summary>
        <div class="result-grid">
          <div>
            <label>Итог: что получилось, что важно помнить</label>
            <textarea id="res-note" rows="3" placeholder="Например: вышло с первого дубля, пришлось перегенерировать шот 3…" ${canResult() ? "" : "readonly"}>${esc(ep.result_note || "")}</textarea>
            <label>Где лежит готовый ролик (ссылка)</label>
            <input id="res-url" type="url" placeholder="https://www.dropbox.com/…" value="${esc(ep.result_url || "")}" ${canResult() ? "" : "readonly"}>
            <div class="muted small" style="margin-top:4px">Вставьте ссылку на Dropbox или прямую ссылку на файл — ниже появится превью. Сохраняется само.</div>
          </div>
          <div id="res-preview">${resultPreview(ep.result_url)}</div>
        </div>
      </details>
  </div>`;
  $("#copy-post").onclick = () => copyText($("#post-text").value);
  bindResult(ep);
  if (canPost()) $("#post-text").onchange = async (ev) => {
    await api(`/api/episodes/${E.id}?reparse=false`, { method: "PUT", json: { post_text: ev.target.value } });
    ep.post_text = ev.target.value;
    toast("Описание для поста сохранено");
  };
}

// ---------------- script tab ----------------
let analyzeTimer = null;

function drawScript(el) {
  const ep = E.ep;
  el.innerHTML = `
    <div class="script-layout">
      <div class="card">
        <div class="row"><b>Сценарий и ТЗ серии</b><div class="spacer"></div>
          ${canWrite() ? `<label class="btn ghost small" style="margin:0">Загрузить .txt<input type="file" id="load-file" accept=".txt,.md,.fountain,text/plain" hidden></label>
          <button class="primary" id="save-script">Сохранить и разбить на шоты</button>` : ""}</div>
        <div class="editor-wrap"><textarea id="script" spellcheck="true" ${canWrite() ? "" : "readonly"}
          placeholder="Шот 1&#10;@Маша сидит на кровати...&#10;&#10;Шот 2&#10;МАША: Привет!">${esc(ep.script)}</textarea></div>
        <div class="muted small" id="save-state">Ctrl+S — сохранить. Введите @, чтобы вставить персонажа или локацию.</div>
        ${ep.parse_notes?.length ? `<ul class="warnings">${ep.parse_notes.map((n) => `<li>${esc(n)}</li>`).join("")}</ul>` : ""}
      </div>
      <div>
        <div class="card" id="analysis"><span class="muted">Анализ…</span></div>
        <div class="card" id="cast" style="margin-top:12px"></div>
      </div>
    </div>
    ${SCRIPT_HELP}`;
  const ta = $("#script");
  attachMentions(ta);
  let dirty = false;
  const save = async () => {
    const res = await api(`/api/episodes/${E.id}`, { method: "PUT", json: { script: ta.value } });
    dirty = false;
    E.ep = res; E.sig = signature(res);
    drawHeader();
    $("#save-state").textContent = `Сохранено и разбито: ${res.shots.length} шотов`;
    toast(`Сохранено: ${res.shots.length} шотов`);
    drawCast(res.cast, lastAnalysis);
  };
  window.__scriptDirty = () => dirty;
  ta.addEventListener("input", () => {
    dirty = true;
    $("#save-state").textContent = "Есть несохранённые изменения";
    clearTimeout(analyzeTimer);
    analyzeTimer = setTimeout(() => analyze(ta.value), 500);
  });
  ta.addEventListener("keydown", (e) => { if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") { e.preventDefault(); save(); } });
  window.onbeforeunload = () => (dirty ? true : undefined);
  if (canWrite()) {
    $("#save-script").onclick = save;
    $("#load-file").onchange = async (e) => {
      const f = e.target.files[0];
      if (!f) return;
      if (ta.value.trim() && !confirm("Заменить текущий текст сценария содержимым файла?")) return;
      const fd = new FormData(); fd.append("file", f);
      const r = await api("/api/read-text", { method: "POST", body: fd });
      ta.value = r.text; ta.dispatchEvent(new Event("input"));
      if (!E.ep.title) { $("#ep-title").value = r.name; $("#ep-title").dispatchEvent(new Event("change")); }
    };
  }
  analyze(ta.value);
}

let lastAnalysis = null;
async function analyze(text) {
  const a = await api("/api/analyze", { json: { script: text, episode_id: E.id } });
  lastAnalysis = a;
  const max = state.meta.max_shot_seconds;
  const box = $("#analysis");
  if (!box) return;
  box.innerHTML = `
    <div class="row"><b>Анализ под Veo</b><div class="spacer"></div>
      <span class="badge">${a.marked ? "по пометкам «Шот N»" : "авторазбивка"}</span></div>
    <div class="summary" style="margin:8px 0">
      <span><b>${a.shots.length}</b> шотов</span><span>хронометраж <b>${fmtDur(a.total_seconds)}</b></span>
      <span class="muted">по тексту ~${fmtDur(a.est_seconds)}</span>
      ${a.shots.filter((s) => s.over).length ? `<span class="warn-t">⚠ не влезают: ${a.shots.filter((s) => s.over).length}</span>` : a.shots.length ? `<span class="ok-t">✓ всё влезает</span>` : ""}
    </div>
    <table class="atable"><thead><tr><th>Шот</th><th>Клип</th><th>Нужно</th><th>Речь</th><th></th></tr></thead><tbody>
    ${a.shots.map((s) => `<tr class="${s.over ? "over" : ""}" title="${esc(s.warnings.join("\n"))}">
      <td>${esc(s.label)}</td><td>${s.duration} с</td><td>${s.est_seconds} с</td>
      <td>${s.speech_seconds ? `${s.speech_seconds} с · ${s.words} сл.` : "—"}</td><td style="width:90px">${fitMeter(s.est_seconds, max)}</td></tr>`).join("")}
    </tbody></table>
    ${a.notes.length ? `<ul class="warnings">${a.notes.map((n) => `<li>${esc(n)}</li>`).join("")}</ul>` : ""}
    <div class="muted small" style="margin-top:6px">Речь ≈ ${state.meta.words_per_second} слова/с (меняется в настройках). Клип Veo — 4, 6 или 8 с.</div>`;
  drawCast(E.ep.cast, a);
}

function drawCast(cast, a) {
  const box = $("#cast");
  if (!box) return;
  const ids = new Set([...(a?.used || []).map((u) => u.asset_id), ...Object.keys(cast || {}).map(Number)]);
  const assets = state.assets.filter((x) => ids.has(x.id));
  box.innerHTML = `<b>Версии для этой серии</b>
    <div class="muted small">Какой костюм/вариант использовать во всей серии. Пометка <code>@Имя:Версия</code> в шоте важнее.</div>
    ${assets.length ? assets.map((x) => {
      const active = x.versions.find((v) => v.id === x.active_version_id);
      const img = (x.versions.find((v) => v.id === cast?.[x.id]) || active)?.images?.[0];
      return `<div class="cast-row">
        <span class="mi-thumb" style="${img ? `background-image:url('${media(img)}')` : ""}">${img ? "" : x.kind === "character" ? "👤" : "🏙️"}</span>
        <span style="flex:1"><b>${esc(x.name)}</b> <span class="muted small">${x.kind === "character" ? "персонаж" : "локация"}</span></span>
        <select data-cast="${x.id}" style="width:auto" ${canWrite() ? "" : "disabled"}>
          <option value="">активная (v${active?.version_no} ${esc(active?.label || "")})</option>
          ${x.versions.map((v) => `<option value="${v.id}" ${cast?.[x.id] === v.id ? "selected" : ""}>v${v.version_no} ${esc(v.label)}</option>`).join("")}
        </select></div>`;
    }).join("") : `<div class="muted small" style="margin-top:6px">В сценарии пока нет персонажей и локаций из библиотеки.</div>`}`;
  $$("[data-cast]", box).forEach((sel) => (sel.onchange = async () => {
    const res = await api(`/api/episodes/${E.id}/cast`, { method: "PUT", json: { cast: [{ asset_id: +sel.dataset.cast, version_id: sel.value ? +sel.value : null }] } });
    E.ep = res; drawHeader(); toast("Версия для серии сохранена, шоты обновлены");
  }));
}

// ---------------- shots tab ----------------
function drawShots(el) {
  const ep = E.ep;
  el.innerHTML = ep.shots.length
    ? `${writerLocked() ? `<div class="help warn-card" style="margin-bottom:12px">🔒 Сценарий согласован, править шоты может только продюсер. Чтобы внести правки, попросите вернуть серию в «В разработке».</div>` : ""}
       <div class="help" style="margin-bottom:12px">${can.write()
         ? `Шоты строятся из сценария. Здесь выбирают персонажей, локацию и тип озвучки реплик. Правки полей перезапишутся, если поменять текст этого шота в сценарии;
            референс композиции, дубли, комментарии и вручную изменённый промпт сохраняются.`
         : `Сценарий этой серии согласован. Содержание шотов смотрите слева, а промпты и генерацию настраивайте справа.`}</div>
       <div id="shots">${ep.shots.map(shotHtml).join("")}</div>`
    : `<div class="card"><p>Шотов пока нет.</p><p class="muted">Напишите сценарий на вкладке «Сценарий» и нажмите «Сохранить и разбить на шоты».</p></div>`;
  ep.shots.forEach(bindShot);
}

export function takeBadge(s) {
  const [label, cls] = TAKE_STATUS[s.gen_status] || [s.gen_status, ""];
  return `<span class="badge ${cls}">${label}</span>`;
}

const VOICE = { direct: "в кадре", voiceover: "закадр" };

function shotHtml(s) {
  const max = state.meta.max_shot_seconds;
  const chars = state.assets.filter((a) => a.kind === "character");
  const locs = state.assets.filter((a) => a.kind === "location").flatMap((a) => a.versions.map((v) => ({ ...v, asset: a })));
  const roW = canWrite() ? "" : "disabled"; // content: the writer's part
  const roG = can.gen() ? "" : "disabled";  // generation: the editor's part
  const sel = s.selected_take;
  return `
  <div class="card shot ${can.gen() ? "" : "shot-w"} ${s.missing.length ? "is-incomplete" : ""}" id="shot-${s.id}">
    <div class="side">
      <div class="shot-no">Шот ${esc(s.label)}</div>
      <div>${fitMeter(s.est_seconds, max)}
        <div class="small ${s.over_limit ? "warn-t" : "muted"}">нужно ~${s.est_seconds} с${s.speech_seconds ? ` · речь ${s.speech_seconds} с · ${s.words} сл.` : ""}</div></div>
      ${s.missing.length
        ? `<ul class="missing">${s.missing.map((m) => `<li>${esc(m)}</li>`).join("")}</ul>`
        : `<div class="ok-t small">✓ заполнен</div>`}
      ${can.gen() ? `<div><label>Нейросеть</label><select data-f="engine">${state.meta.engines.map((e) => `<option value="${e.key}" ${e.key === s.engine ? "selected" : ""}>${esc(e.name)}</option>`).join("")}</select></div>` : ""}
      <div><label>Длина клипа</label><select data-f="duration" ${roG} ${s.forced_8 ? "disabled" : ""}>${[4, 6, 8].map((d) => `<option ${d === s.duration ? "selected" : ""} value="${d}">${d} с</option>`).join("")}</select>
        ${s.forced_8 ? `<div class="muted small">8 с: с референсами Veo 3.1 делает только 8 с</div>` : ""}</div>
      <div>${takeBadge(s)} ${s.takes.length ? `<span class="muted small">дублей: ${s.takes.length}${s.revisions ? ` · правок: ${s.revisions}` : ""}</span>` : ""}
        ${s.needs_redo ? `<span class="badge warn">нужна перегенерация</span>` : ""}</div>
      ${s.takes.at(-1)?.error ? `<div class="small err-t">${esc(s.takes.at(-1).error)}</div>` : ""}
      ${sel?.video_path ? `<video src="${media(sel.video_path)}" controls preload="metadata"></video>` : ""}
      ${can.gen() ? (s.engine === "veo"
        ? `<button class="primary" data-act="gen">Сгенерировать дубль</button><button data-act="req">Запрос к Veo</button>
           <label class="btn ghost small up-label">Или загрузить готовое видео<input type="file" accept="video/mp4,video/quicktime,video/webm" data-upload hidden></label>`
        : `<div class="small muted">Шот делается в «${esc(engineName(s.engine))}»: через API он не уходит. Сделайте видео там и загрузите файл.</div>
           <label class="btn primary up-label">Загрузить готовое видео<input type="file" accept="video/mp4,video/quicktime,video/webm" data-upload hidden></label>
           <button data-act="copyprompt">Скопировать промпт</button>`) : ""}
    </div>
    <div>
      <label>Сцена</label><input data-f="scene" value="${esc(s.scene)}" ${roW}>
      <label>Локация</label>
      <select data-f="location_version_id" ${roW} class="${s.location_version_id ? "" : "need"}"><option value="">— не выбрана —</option>
        ${locs.map((v) => `<option value="${v.id}" ${v.id === s.location_version_id ? "selected" : ""}>${esc(v.asset.name)} · v${v.version_no} ${esc(v.label)}</option>`).join("")}</select>
      <label>Персонажи в кадре</label>
      <div class="chips">${s.characters.map((c, i) => {
        const a = chars.find((x) => x.id === c.asset_id);
        return `<span class="chip" style="--c:${a ? assetColor(a) : "var(--line)"}">${esc(c.name)}
          <select data-char="${i}" ${roW}>${(a ? a.versions : []).map((v) => `<option value="${v.id}" ${v.id === c.version_id ? "selected" : ""}>v${v.version_no} ${esc(v.label)}</option>`).join("")}</select>
          ${canWrite() ? `<button data-rmchar="${i}" title="Убрать">✕</button>` : ""}</span>`;
      }).join("") || `<span class="muted small">никого (например, макро или пейзаж)</span>`}
        ${canWrite() ? `<select data-addchar style="width:auto"><option value="">+ добавить</option>
          ${chars.filter((a) => !s.characters.some((c) => c.asset_id === a.id)).map((a) => `<option value="${a.id}">${esc(a.name)}</option>`).join("")}</select>` : ""}
      </div>
      <label>Действие</label><textarea data-f="action" rows="3" ${roW}>${esc(s.action)}</textarea>
      <label>Камера</label><input data-f="camera" value="${esc(s.camera)}" ${roW}>
      <label>Реплики <span class="muted small">— берутся из кавычек в сценарии; красным подсвечено то, что не удалось определить. «в кадре»: персонаж говорит, идёт в Veo; «закадр»: голос за кадром, озвучивается отдельно</span></label>
      <div class="dialogue">${s.dialogue.map((d, i) => `
        <div class="dlg-line ${d.voice === "voiceover" ? "vo" : ""} ${d.auto ? "auto" : ""}" data-line="${i}">
          <input data-d="speaker" class="${!d.speaker || (d.voice !== "voiceover" && !d.asset_id) ? "need-red" : ""}" value="${esc(d.speaker)}" placeholder="Кто говорит" list="char-names-${s.id}" ${roW}>
          <select data-d="voice" ${roW} title="Как звучит реплика">${Object.entries(VOICE).map(([k, n]) => `<option value="${k}" ${(d.voice || "direct") === k ? "selected" : ""}>${n}</option>`).join("")}</select>
          <input data-d="parenthetical" class="${d.auto && !d.parenthetical ? "need-red" : ""}" value="${esc(d.parenthetical)}" placeholder="как (тихо)" ${roW}>
          <input data-d="text" value="${esc(d.text)}" placeholder="Текст реплики" ${roW}>
          ${canWrite() ? `<button class="ghost" data-rmline="${i}" title="Удалить реплику">✕</button>` : ""}</div>`).join("") || `<div class="muted small">без реплик</div>`}</div>
      ${canWrite() ? `<button class="ghost small" data-act="addline">+ реплика</button>` : ""}
      <datalist id="char-names-${s.id}">${[...s.characters.map((c) => c.name), ...chars.map((a) => a.name)].filter((n, i, arr) => arr.indexOf(n) === i).map((n) => `<option value="${esc(n)}">`).join("")}</datalist>
      ${s.warnings.length ? `<ul class="warnings">${s.warnings.map((w) => `<li>${esc(w)}</li>`).join("")}</ul>` : ""}
    </div>
    ${can.gen() ? `<div>
      <label style="margin-top:0">Референс композиции</label>
      <div class="comp ${s.composition_image ? "has" : ""}" data-drop="${s.id}">
        ${s.composition_image
          ? `<img src="${media(s.composition_image)}"><div class="comp-tools">
              <select data-f="composition_mode" ${roG}><option value="reference" ${s.composition_mode !== "first_frame" ? "selected" : ""}>как референс композиции</option>
                <option value="first_frame" ${s.composition_mode === "first_frame" ? "selected" : ""}>как первый кадр видео</option></select>
              <button class="ghost small danger" data-act="rmcomp">Убрать</button></div>`
          : `<label class="comp-empty">Перетащите картинку или нажмите — раскадровка, скетч, кадр-образец<input type="file" accept="image/png,image/jpeg,image/webp" data-comp hidden></label>`}
      </div>
      <input data-f="composition_note" value="${esc(s.composition_note)}" placeholder="Пояснение к композиции (по желанию): героиня слева, окно справа…" ${roG} style="margin-top:6px">
      <div class="row" style="margin-top:12px"><label style="margin:0">Промпт для «${esc(engineName(s.engine))}» ${s.prompt_locked ? `<span class="badge warn">изменён вручную</span>` : `<span class="badge">авто</span>`}</label>
        <div class="spacer"></div><button class="ghost small" data-act="rebuild" title="Собрать заново из полей шота">↻ пересобрать</button></div>
      <textarea class="prompt" data-f="prompt" rows="9" style="margin-top:6px" ${roG}>${esc(s.prompt)}</textarea>
      <label>Негативный промпт</label><input data-f="negative_prompt" value="${esc(s.negative_prompt)}" ${roG}>
      <label>${s.engine === "veo" ? "Уйдёт в Veo как изображения (до 3); в промпте они называются «reference image 1, 2…»" : "Картинки-референсы: в промпте они названы «reference image 1, 2…», прикладывайте в этом порядке"}</label>
      <div class="refs">${s.references.map((r, i) => `<figure><img src="${media(r.path)}"><figcaption>${i + 1} · ${r.kind === "composition" ? "композиция" : esc(r.name)}</figcaption></figure>`).join("") || `<span class="muted small">нет изображений</span>`}</div>
    </div>` : ""}
  </div>`;
}

function replaceShot(s) {
  const i = E.ep.shots.findIndex((x) => x.id === s.id);
  if (i >= 0) E.ep.shots[i] = { ...E.ep.shots[i], ...s };
  const el = $(`#shot-${s.id}`);
  if (el) { el.outerHTML = shotHtml(E.ep.shots[i]); bindShot(E.ep.shots[i]); }
}

async function uploadComposition(s, file) {
  const fd = new FormData(); fd.append("image", file);
  replaceShot(await api(`/api/shots/${s.id}/composition`, { method: "POST", body: fd }));
  toast("Референс композиции добавлен — он попадёт в промпт и в запрос к Veo");
}

function bindShot(s) {
  const el = $(`#shot-${s.id}`);
  if (!el) return;
  const save = async (patch) => replaceShot(await api(`/api/shots/${s.id}`, { method: "PUT", json: patch }));
  // «@» в текстовых полях шота подсказывает персонажей и локации из библиотеки (как в сценарии); выбор сразу сохраняется
  $$("[data-f=scene], [data-f=action], [data-f=camera]", el).forEach(attachHighlight); // герои из библиотеки подсвечены цветом
  if (canWrite()) {
    $$("[data-f=scene], [data-f=action], [data-f=camera], [data-d=text], [data-d=parenthetical]", el)
      .forEach((inp) => attachMentions(inp, () => inp.dispatchEvent(new Event("change"))));
  }
  const up = $("[data-upload]", el);
  if (up) up.onchange = async () => {
    const f = up.files[0]; if (!f) return;
    const fd = new FormData(); fd.append("video", f);
    toast("Загружаю видео…");
    await api(`/api/shots/${s.id}/upload-take`, { method: "POST", body: fd });
    toast(`Видео для шота ${s.label} загружено`);
    renderEpisode(E.id);
  };
  $$("[data-f]", el).forEach((inp) => (inp.onchange = () => {
    const f = inp.dataset.f;
    let v = inp.value;
    if (f === "duration") v = +v;
    if (f === "location_version_id") return save(v ? { location_version_id: +v } : { clear_location: true });
    if (f === "prompt") return save({ prompt: v, prompt_locked: true });
    save({ [f]: v });
  }));
  $$("[data-char]", el).forEach((sel) => (sel.onchange = () => save({ characters: s.characters.map((c, i) => (i === +sel.dataset.char ? { ...c, version_id: +sel.value } : c)) })));
  $$("[data-rmchar]", el).forEach((b) => (b.onclick = () => save({ characters: s.characters.filter((_, i) => i !== +b.dataset.rmchar) })));
  const add = $("[data-addchar]", el);
  if (add) add.onchange = () => {
    const a = state.assets.find((x) => x.id === +add.value);
    if (a) save({ characters: [...s.characters, { asset_id: a.id, name: a.name, version_id: E.ep.cast[a.id] || a.active_version_id }] });
  };
  const readLines = (edited = -1) => $$(".dlg-line", el).map((row, i) => {
    const speaker = $("[data-d=speaker]", row).value.trim();
    const a = state.assets.find((x) => x.kind === "character" && x.name.toLowerCase() === speaker.toLowerCase());
    const line = { speaker: a ? a.name : speaker, asset_id: a ? a.id : null, voice: $("[data-d=voice]", row).value,
      parenthetical: $("[data-d=parenthetical]", row).value, text: $("[data-d=text]", row).value };
    if (s.dialogue[i]?.auto && i !== edited) line.auto = true; // правка строки значит «проверено», красная подсветка уходит
    return line;
  });
  $$(".dlg-line input, .dlg-line select", el).forEach((inp) => (inp.onchange = () => save({ dialogue: readLines(+inp.closest(".dlg-line").dataset.line) })));
  $$("[data-rmline]", el).forEach((b) => (b.onclick = () => save({ dialogue: readLines().filter((_, i) => i !== +b.dataset.rmline) })));

  const comp = $("[data-comp]", el);
  if (comp) comp.onchange = () => comp.files[0] && uploadComposition(s, comp.files[0]);
  const drop = $("[data-drop]", el);
  if (drop && can.gen()) {
    drop.addEventListener("dragover", (e) => { e.preventDefault(); drop.classList.add("drop-over"); });
    drop.addEventListener("dragleave", () => drop.classList.remove("drop-over"));
    drop.addEventListener("drop", (e) => {
      e.preventDefault(); drop.classList.remove("drop-over");
      const f = e.dataTransfer.files[0];
      if (f) uploadComposition(s, f);
    });
  }

  $$("[data-act]", el).forEach((b) => (b.onclick = async () => {
    const act = b.dataset.act;
    if (act === "addline") return save({ dialogue: [...readLines(), { speaker: s.characters[0]?.name || "", asset_id: s.characters[0]?.asset_id ?? null, voice: "direct", parenthetical: "", text: "" }] });
    if (act === "rebuild") return replaceShot(await api(`/api/shots/${s.id}/rebuild-prompt`, { json: {} }));
    if (act === "rmcomp") return replaceShot(await api(`/api/shots/${s.id}/composition`, { method: "DELETE" }));
    if (act === "gen") {
      replaceShot(await api(`/api/shots/${s.id}/generate`, { json: {} }));
      toast(`Шот ${s.label} отправлен в Veo`); return renderEpisode(E.id);
    }
    if (act === "req") return showRequest(s);
    if (act === "copyprompt") return copyText(s.prompt);
  }));
}

async function showRequest(s) {
  const req = await api(`/api/shots/${s.id}/request`);
  const m = modal(`<h1>Запрос к Veo — шот ${esc(s.label)}</h1>
    <p class="muted small">Так выглядит запрос, который уйдёт в Veo. Изображения показаны путями, при отправке они кодируются в base64.</p>
    <pre class="code">${esc(JSON.stringify(req, null, 2))}</pre>
    <div class="row"><div class="spacer"></div><button id="copy">Скопировать промпт</button><button id="close">Закрыть</button></div>`, true);
  $("#close", m).onclick = closeModal;
  $("#copy", m).onclick = () => { navigator.clipboard.writeText(req.instances[0].prompt); toast("Промпт скопирован"); };
}

// ---------------- history tab ----------------
async function drawHistory(el) {
  const evs = await api(`/api/episodes/${E.id}/events`);
  el.innerHTML = `<div class="card">${evs.map((e) => `<div class="event"><span class="muted small">${fmtDateTime(e.created_at)}</span>
      <b>${esc(e.user_name)}</b> ${esc(e.text)}</div>`).join("") || `<span class="muted">Пока пусто</span>`}
    ${can.admin() ? `<div style="margin-top:16px"><button class="danger ghost" id="del-ep">Удалить серию</button></div>` : ""}</div>`;
  $("#del-ep") && ($("#del-ep").onclick = async () => {
    if (!confirm("Удалить серию со всеми шотами, видео-дублями и комментариями?")) return;
    await api(`/api/episodes/${E.id}`, { method: "DELETE" });
    location.hash = "#/series";
  });
}

// ---------------- new episode ----------------
export function newEpisodeDialog(opts = {}) {
  loadAssets();
  const m = modal(`
    <h1>Новая серия</h1>
    <label>Название</label><input id="ne-title" placeholder="например: Месть за видео">
    <div class="row">
      <div style="flex:1"><label>Дата выхода</label><input id="ne-date" type="date" value="${opts.date || ""}"></div>
      <div style="flex:2;padding-top:22px" class="muted small">${opts.date ? "Дата будет закреплена." : "Пусто — ближайший свободный день в очереди."}</div>
    </div>
    <label>Арка</label><select id="ne-arc"><option value="">— без арки —</option></select>
    <label class="check"><input type="checkbox" id="ne-backlog"> пока без даты (в черновики)</label>
    <label>Сценарий — вставьте текст или загрузите файл (можно и позже)</label>
    <div class="editor-wrap"><textarea id="ne-script" rows="12" placeholder="Шот 1&#10;..."></textarea></div>
    <input id="ne-file" type="file" accept=".txt,.md,.fountain,text/plain" style="margin-top:6px">
    <div class="row" style="margin-top:14px"><div class="spacer"></div>
      <button class="ghost" id="cancel">Отмена</button><button class="primary" id="create">Создать</button></div>`, true);
  attachMentions($("#ne-script", m));
  api("/api/arcs").then((arcs) => { $("#ne-arc", m).innerHTML += arcs.map((a) => `<option value="${a.id}" ${a.id === opts.arcId ? "selected" : ""}>${esc(a.title)}</option>`).join(""); });
  $("#cancel", m).onclick = closeModal;
  $("#ne-file", m).onchange = async (e) => {
    const f = e.target.files[0]; if (!f) return;
    const fd = new FormData(); fd.append("file", f);
    const r = await api("/api/read-text", { method: "POST", body: fd });
    $("#ne-script", m).value = r.text;
    if (!$("#ne-title", m).value) $("#ne-title", m).value = r.name;
  };
  $("#create", m).onclick = async () => {
    const ep = await api("/api/episodes", { json: {
      title: $("#ne-title", m).value.trim(), script: $("#ne-script", m).value, arc_id: +$("#ne-arc", m).value || null,
      date: $("#ne-backlog", m).checked ? null : $("#ne-date", m).value || null, backlog: $("#ne-backlog", m).checked } });
    closeModal();
    location.hash = `#/episodes/${ep.id}/script`;
  };
  setTimeout(() => $("#ne-title", m).focus(), 50);
}
