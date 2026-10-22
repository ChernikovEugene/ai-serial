// Episode card: script editor with live analysis, shots, review & comments, history.
import { deleteEpisode } from "./trash.js";
import {
  POST_NETS, $, $$, api, assetColor, can, closeModal, engineName, esc, fitMeter, fmtDate, fmtDateTime, fmtDur, goBack, loadAssets, media, modal, replaceHash, state, statusSelect, STATUS_COLORS, toast, view,
} from "./core.js";
import { bindFrames, framesBusy, framesHtml, framesSig } from "./frames.js";
import { attachMentions } from "./mention.js";
import { attachHighlight } from "./highlight.js";
import { drawReview, reviewPlaying, stopReview } from "./review.js";
import { seenEpisode } from "./notify.js";

export const E = { id: null, ep: null, tab: "script", sel: null, poll: null, sig: "", gap: null };
export const TAKE_STATUS = { queued: ["в очереди", "run"], running: ["генерация…", "run"], done: ["готово", "ok"],
  stub: ["заглушка", "warn"], error: ["ошибка", "err"], cancelled: ["отменено", ""], idle: ["не создано", ""] };

/** Видео больше лимита сервера не отправляем: сразу говорим, а не ждём долгую загрузку. */
export function videoTooBig(f) {
  const mb = state.meta.upload_limits?.video_mb || 300;
  if (f.size <= mb * 1024 * 1024) return false;
  toast(`Видео ${Math.round(f.size / 1048576)} МБ, а можно до ${mb} МБ. Сожмите его или загрузите короче`, true);
  return true;
}

/** Режим звука шота: «речь и звук» / «только естественный фон» / «без звука». */
export const soundMode = (s) => s.sound_mode || (s.sound_off ? "off" : "full");

/** Отмена генерации дубля, который ещё в очереди или генерируется. */
export async function cancelTake(s) {
  const t = s.takes.at(-1);
  if (!t || !["queued", "running"].includes(t.status)) return;
  if (!confirm(`Отменить генерацию шота ${s.label}? Если нейросеть уже начала работу, кредиты могут списаться.`)) return;
  await api(`/api/takes/${t.id}/cancel`, { json: {} });
  toast(`Шот ${s.label}: генерация отменена`);
  renderEpisode(E.id);
}

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
  if (E.id !== id) { E.sel = null; E.gap = null; }
  const again = E.id === id && (!tab || tab === E.tab) && !!$("#ep-tab"); // тот же экран: набранное не должно пропасть
  E.id = id;
  if (tab) E.tab = ["script", "shots", "review", "main", "history"].includes(tab) ? tab : "script";
  const [ep] = await Promise.all([api(`/api/episodes/${id}`), loadAssets()]);
  E.ep = ep;
  seenEpisode(id); // уведомление «серия сгенерировалась» прочитано
  if (!E.sel || !ep.shots.some((s) => s.id === E.sel)) E.sel = ep.shots[0]?.id ?? null;
  E.sig = signature(ep);
  if (again) await redrawKeepingEdits(draw); else draw();
  schedulePoll();
}

// Перерисовка не должна стирать то, что человек сейчас печатает: текст в полях записывается на «change» (когда он
// уходит из поля), а страница обновляется сама, пока идёт генерация.
const TEXT_TYPES = new Set(["", "text", "number", "url", "search", "tel", "email"]);
const isText = (el) => el.tagName === "TEXTAREA" || (el.tagName === "INPUT" && TEXT_TYPES.has(el.getAttribute("type") || ""));
const isDirty = (el) => isText(el) && !el.readOnly && !el.disabled && el.value !== el.defaultValue;
function fieldKey(el) {
  if (!(el.id || el.dataset.f || el.dataset.d)) return "";
  return [el.closest("[id^='shot-']")?.id || "", el.id, el.dataset.f || "", el.dataset.d || "", el.closest(".dlg-line")?.dataset.line || ""].join("|");
}
const detailsKey = (d) => {
  const t = d.querySelector(":scope > summary")?.textContent.trim().slice(0, 40);
  return t ? [d.closest("[id^='shot-']")?.id || "", d.id, t].join("|") : "";
};
const typingNow = () => { const a = document.activeElement; return !!a && view().contains(a) && isDirty(a); };
const busyNow = () => typingNow() || (E.tab === "review" && reviewPlaying()); // печатают или смотрят серию: перерисовку откладываем

async function redrawKeepingEdits(redraw) {
  const root = view();
  const typed = new Map();
  root.querySelectorAll("textarea, input").forEach((el) => { const k = fieldKey(el); if (k && isDirty(el)) typed.set(k, el.value); });
  const a = document.activeElement;
  const focus = a && root.contains(a) && fieldKey(a) ? { key: fieldKey(a), from: a.selectionStart, to: a.selectionEnd } : null;
  const open = new Map();
  root.querySelectorAll("details").forEach((d) => { const k = detailsKey(d); if (k) open.set(k, d.open); });
  const y = window.scrollY;
  await redraw(); // вкладка «Просмотр» дорисовывается после запроса: восстанавливаем поля, когда она готова
  root.querySelectorAll("details").forEach((d) => { const k = detailsKey(d); if (open.has(k)) d.open = open.get(k); });
  root.querySelectorAll("textarea, input").forEach((el) => {
    const k = fieldKey(el);
    if (k && typed.has(k)) { el.value = typed.get(k); el.dispatchEvent(new Event("input", { bubbles: true })); } // input: подсветка героев обновится
    if (focus && k === focus.key) {
      el.focus();
      try { el.setSelectionRange(focus.from, focus.to); } catch { /* у поля нет выделения */ }
    }
  });
  window.scrollTo(0, y);
}

function signature(ep) { return ep.shots.map((s) => `${s.id}:${s.takes.map((t) => t.status).join(",")}:${s.selected_take_id}:${framesSig(s)}`).join("|") + ep.status; }

function patchFarm(s) {
  const prog = $(`#farm-prog-${s.id}`);
  if (prog) prog.innerHTML = farmProgress(s);
  const rev = $(`#farm-prog-r-${s.id}`);
  if (rev) rev.innerHTML = farmProgress(s);
  $$(`[data-farm-badge="${s.id}"]`).forEach((b) => (b.outerHTML = takeBadge(s)));
  const dr = $(`#farm-draft-${s.id}`);
  if (dr) {
    const t = farmTake(s);
    const want = t && t.draft_path && t.status !== "done" ? t.draft_path : "";
    if ((dr.querySelector("video")?.dataset.src || "") !== want) dr.innerHTML = farmDraft(s); // не перезапускаем играющий черновик
  }
}

function schedulePoll() {
  clearTimeout(E.poll); // только таймер опроса: просмотр серии не трогаем, иначе он останавливался после каждой перерисовки
  const busy = E.ep.shots.some((s) => ["queued", "running"].includes(s.gen_status) || framesBusy(s)) || E.sig !== signature(E.ep); // второе: перерисовка отложена, пока человек печатает
  if (!busy) return;
  E.poll = setTimeout(async () => {
    if (!location.hash.startsWith(`#/episodes/${E.id}`)) return;
    const ep = await api(`/api/episodes/${E.id}`);
    const sig = signature(ep);
    E.ep = ep;
    if (sig !== E.sig && ["script", "main"].includes(E.tab)) E.sig = sig; // never wipe the text being edited
    else if (sig !== E.sig && !busyNow()) {
      E.sig = sig;
      await redrawKeepingEdits(() => { drawHeader(); return drawTab(); });
    } else ep.shots.forEach(patchFarm); // процент и черновик фермы меняются чаще статуса: обновляем на месте, поля не трогаем
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
  return drawTab();
}

// Длины клипа и подсказка для нейросети шота (список приходит с сервера в /api/meta).
const engineInfo = (key) => state.meta.engines.find((e) => e.key === key) || state.meta.engines.find((e) => e.key === "other");

// Подпись картинки-референса так, как на неё ссылается промпт выбранной нейросети: @Image1, Image 1 или просто номер.
/** Для ручной генерации (Magnific в браузере и другие сервисы): промпт, где картинки названы латинскими метками
 * (@altushka), и картинки, которые скачиваются с этими же именами. Загрузил файлы, вставил промпт — связи по именам. */
function manualBlock(s) {
  if (!s.prompt_manual) return "";
  const ext = (p) => (p.match(/\.\w+$/) || [".png"])[0];
  return `<details class="manual-prompt"><summary class="muted small">Для ручной генерации: промпт с @метками</summary>
    <div class="muted small">Скачайте картинки (имена файлов совпадают с метками), загрузите их в сервис и вставьте этот промпт.</div>
    <textarea rows="6" readonly>${esc(s.prompt_manual)}</textarea>
    <div class="row" style="margin-top:6px">${s.manual_refs.map((r) => `<a class="btn ghost small" href="${media(r.path)}" download="${esc(r.tag + ext(r.path))}"
        title="${r.kind === "composition" ? "Композиция" : esc(r.name)}">⬇ @${esc(r.tag)}</a>`).join("")}
      <div class="spacer"></div><button class="small" data-act="copymanual">Скопировать промпт</button></div></details>`;
}

/** Состояние шота на ферме (Пульт): подпись и процент по таблице из документации. Генерация 0–50 %, доводка 50–100 %. */
function farmTake(s) {
  const t = s.takes.at(-1);
  return t && t.provider === "renderfarm" ? t : null;
}

export function farmProgress(s) {
  const t = farmTake(s);
  if (!t || !["queued", "running"].includes(t.status)) return "";
  const pct = Math.round((t.progress || 0) * 100);
  let label, hint = "";
  if (t.status === "queued" && !t.remote_id) { // Пульт шот ещё не принял
    label = t.farm_ready ? "Отправлено, ждём ферму" : "Готовим шот к отправке";
    if (t.farm_ready) hint = "Пульт забирает шоты раз в 10 секунд. Если ферма выключена или нет связи, шот подождёт и уйдёт сам.";
  } else if (t.farm_err) label = "Ролик ещё не загрузился на сайт, Пульт догрузит";
  else if (t.stage === "finish") label = t.position > 0 ? "Черновик готов, ждёт доводки" : pct >= 100 ? "Доводка закончена, ждём финальный ролик от Пульта" : `Доводка до 1080p — ${pct}%`;
  else if (t.status === "queued") label = t.position > 0 ? `В очереди фермы: №${t.position}` : /рендер|занят/i.test(t.farm_msg || "") ? "Видеокарта занята рендером, ждём" : "Принято фермой, ждёт видеокарту";
  else label = `Генерация видео — ${pct}%`;
  const gen = t.stage === "finish" ? 100 : Math.min(100, pct * 2);
  const fin = t.stage === "finish" ? Math.max(0, Math.min(100, (pct - 50) * 2)) : 0;
  return `<div class="farm-prog" title="Генерация 0–50 %, доводка до 1080p 50–100 %">
    <div class="farm-label small">${esc(label)}${t.status === "running" && pct > 0 && pct < 100 ? ` <span class="muted">· идёт…</span>` : ""}</div>
    <div class="farm-bar"><span style="width:${gen / 2}%"></span><span class="fin" style="width:${fin / 2}%"></span></div>
    <div class="farm-parts muted"><span>генерация</span><span>доводка</span></div>
    ${t.farm_msg ? `<div class="muted small">${esc(t.farm_msg)}</div>` : ""}${hint ? `<div class="muted small">${esc(hint)}</div>` : ""}</div>`;
}

function farmDraft(s) {
  const t = farmTake(s);
  if (!t || !t.draft_path || t.status === "done") return "";
  return `<div class="small muted">Черновик (быстрее и мыльнее финала, уже со звуком)</div><video src="${media(t.draft_path)}" data-src="${esc(t.draft_path)}" controls preload="metadata"></video>`;
}

function farmPanel(s) {
  const ro = can.gen() ? "" : "disabled";
  const sel = (f, opts) => `<select data-f="${f}" ${ro}>${opts.map(([v, n]) => `<option value="${v}" ${(s[f] || "") === v ? "selected" : ""}>${n}</option>`).join("")}</select>`;
  return `<div class="farm-box">
    <div class="row"><label style="margin:0">Ферма: Пульт, MiniMax H3</label><div class="spacer"></div><span class="badge">промпт пишет ферма</span></div>
    <div class="muted small">Ферма сама собирает промпт по картинкам героя и локации, реплике и действию. Пишите «Действие», «Камеру» и интонацию как обычно, на любом языке:
      русский текст ферма переведёт сама. Шот забирает Пульт: «Отменить» в студии снимает шот с очереди, а уже принятый фермой шот остановить можно только в Пульте.</div>
    <details class="manual-prompt"><summary class="muted small">Своё описание по-английски (не обязательно)</summary>
      <div class="muted small">Если вписать английский текст, на ферму уйдёт он, а не русский.</div>
      <label>Действие по-английски</label>
      <textarea data-f="farm_action" rows="2" ${ro} placeholder="she sits at her computer with her headphones and looks into the camera">${esc(s.farm_action || "")}</textarea>
      <div class="farm-grid">
        <div><label>Голос по-английски</label><input data-f="farm_voice" value="${esc(s.farm_voice || "")}" ${ro} placeholder="normal calm voice, whisper, excited fast voice"></div>
        <div><label>Камера по-английски</label><input data-f="farm_camera" value="${esc(s.farm_camera || "")}" ${ro} placeholder="static camera, slow push-in"></div>
      </div></details>
    <div class="farm-grid">
      <div><label>Крупность</label>${sel("farm_shot_size", [["", "по умолчанию (medium shot)"], ["medium shot", "средний план"], ["close-up", "крупный план"], ["wide shot", "общий план"]])}</div>
      <div><label>Качество</label>${sel("farm_quality", [["", "Финал: 1080p, в ролик"], ["draft", "Черновик: пара минут, без доводки"]])}</div>
      <div><label>Длина клипа</label>${sel("farm_len", [["", "ферма считает по реплике (рекомендуется)"], ["shot", "как в поле «Длина клипа»"]])}</div>
    </div>
    <details class="manual-prompt"><summary class="muted small" data-farmreq>Что уйдёт на ферму</summary>
      <pre class="small" data-farmreq-out>нажмите, чтобы посмотреть</pre></details>
  </div>`;
}

function refTag(s, r, i) {
  const ex = engineInfo(s.engine).ref_example;
  if (s.composition_mode === "first_frame" && r.kind === "composition") return "первый кадр";
  return ex && ex !== "reference image 1" ? esc(ex.replace("1", String(i + 1))) : String(i + 1);
}

// Под выбором нейросети: как она принимает героев и локации и как под неё писать (правила из документации Magnific).
function engineHelp(s) {
  const e = engineInfo(s.engine);
  const facts = [
    e.api ? (e.max_refs ? `референсов до ${e.max_refs}${e.ref_example ? `, в промпте «${e.ref_example}»` : ""}` : "референсы не принимает") : "вручную, не через API",
    e.audio ? "звук делает сама" : "звук отдельно (ElevenLabs)",
    e.prompt_max ? `промпт до ${e.prompt_max} знаков` : "",
  ].filter(Boolean);
  return `<div class="muted small">${esc(facts.join(" · "))}</div>
    ${e.tips.length ? `<details class="small"><summary class="muted">как заполнять для ${esc(e.name)}</summary>
      <ul style="margin:4px 0 0 16px;padding:0">${e.tips.map((t) => `<li>${esc(t)}</li>`).join("")}</ul></details>` : ""}`;
}

// The writer cannot touch a script once the producer has approved it.
// Править серию (не админу) можно, пока ТЗ не готово: те же этапы, что проверяет сервер (auth.WRITER_STATUSES).
const EDITABLE_STATUSES = ["synopsis", "synopsis_review", "synopsis_ok", "dev", "review"];
const writerLocked = () => state.meta.user.role !== "admin" && !EDITABLE_STATUSES.includes(E.ep.status);
const canWrite = () => can.write() && !writerLocked();
const canEp = () => can.episodes() && !writerLocked(); // название и синопсис серии

const canPost = () => can.episodes(); // описание поста — на любом этапе

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

/** Кнопка архива согласованной версии: появляется только после «Согласовать» на вкладке «Просмотр и правки». */
export function archiveButton(ep) {
  if (!ep.approved_version) return "";
  const v = `v${ep.approved_version}`;
  if (ep.export_status === "building") return `<span class="btn ghost" title="Шоты перекодируются в MP4 FHD 1080x1920, 60 кадров/с">⏳ Архив ${v} собирается…</span>`;
  if (ep.export_status === "error") return `<span class="btn ghost danger" title="${esc(ep.export_error)}">Архив ${v} не собрался: ${esc(ep.export_error)}</span>`;
  return `<a class="btn primary" href="/api/episodes/${ep.id}/download" title="Шоты отдельными файлами MP4 FHD 1080x1920, 60 кадров/с: папки «номер серии» / «название серии», файлы «название_номер шота»">⬇ Скачать архив ${v}</a>`;
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
        ${archiveButton(ep)}
        ${can.gen() ? `<select id="gen-mode" style="width:auto">
            <option value="missing">шоты без видео</option><option value="redo">шоты с правками</option><option value="all">все шоты заново</option></select>
          <button class="primary" id="gen" title="Через Magnific API уходят шоты всех нейросетей, кроме «Другой»: её монтажёр присылает готовым файлом.">▶ Отправить в генерацию</button>` : ""}
      </div>
      <div class="muted small" style="margin:6px 0 0 2px">${ep.arc_id ? `Арка «${esc(ep.arc_title)}» · ` : ""}${ep.date ? `Выход: ${fmtDate(ep.date)}${ep.pinned ? " 📌 дата закреплена" : ""}` : "Черновик — в очередь не идёт"}
            ${can.schedule() ? `· <a href="#" id="move-ep">${ep.number != null ? "перенести" : "назначить дату"}</a>` : ""}</div>
      <div class="summary">
        <span><b>${ep.shots.length}</b> шотов</span>
        <span>хронометраж <b>${fmtDur(ep.total_seconds)}</b> <span class="muted">(по тексту ~${fmtDur(ep.est_seconds)})</span></span>
        ${ep.over_limit ? `<span class="warn-t">⚠ не влезают в клип: <b>${ep.over_limit}</b></span>` : `<span class="ok-t">✓ все шоты влезают в клип</span>`}
        ${ep.shots.length ? (ep.incomplete
          ? `<span class="warn-t" title="Согласовать сценарий можно, когда у каждого шота выбрана локация, заполнены реплики и текст влезает в лимит">заполнено <b>${filled}/${ep.shots.length}</b> шотов</span>`
          : `<span class="ok-t">✓ все шоты заполнены</span>`) : ""}
        <span>видео <b>${withVideo}/${ep.shots.length}</b></span>
        ${fixes ? `<span class="warn-t">открытых правок: <b>${fixes}</b></span>` : ""}
        <span class="muted">${state.meta.gen_mode === "live" ? "генерация через Magnific" : "режим заглушки"}</span>
      </div>
      <div class="timeline">${ep.shots.map((s) => `<span data-jump="${s.id}" title="Шот ${esc(s.label)}: ${s.duration} с, по тексту ${s.est_seconds} с"
        class="${s.over_limit ? "over" : ""} ${s.missing.length ? "incomplete" : ""} ${s.selected_take_id ? "has-video" : ""}" style="flex:${s.duration}">${esc(s.label)}</span>`).join("")}</div>
    </div>`;
  bindTitleRow(ep);
  if (can.gen()) {
    $("#gen").onclick = async () => {
      const mode = $("#gen-mode").value;
      if (mode === "all" && !confirm("Сгенерировать заново все шоты? Каждый шот получит новый дубль (это платно при работе через API).")) return;
      let r = await api(`/api/episodes/${E.id}/generate`, { json: { mode } });
      if (r.unready?.length) {
        const go = await unreadyDialog(r.unready, r.started);
        if (go) {
          const f = await api(`/api/episodes/${E.id}/generate`, { json: { mode, force: true } });
          r = { ...f, started: r.started + f.started };
        }
      }
      const rest = (r.skipped ? ` Ещё ${r.skipped} шотов делаются в других нейросетях: загрузите готовые видео в карточках шотов.` : "")
        + (r.uploaded ? ` Шотов с загруженным видео пропущено: ${r.uploaded}.` : "");
      toast(r.started ? `Отправлено в генерацию: ${r.started} шотов.${rest}` : `Через API нечего отправлять.${rest || " У всех шотов уже есть видео."}`);
      renderEpisode(E.id);
    };
  }
  $("#move-ep") && ($("#move-ep").onclick = (e) => { e.preventDefault(); moveDialog(); });
  $$("[data-jump]").forEach((el) => (el.onclick = () => {
    E.sel = +el.dataset.jump;
    drawTab();
    $(`#shot-${E.sel}`)?.scrollIntoView({ behavior: "smooth", block: "start" });
  }));
}

/** Защита от пустых шотов: что не заполнено. Продюсер может отправить и их — «под свою ответственность». */
function unreadyDialog(list, started) {
  return new Promise((done) => {
    const m = modal(`<h1>Не все шоты заполнены</h1>
      <p>${started ? `Заполненные шоты (${started}) ушли в генерацию. ` : ""}Эти шоты не отправлены: заполните пункты ниже.
        Если пункт не нужен, закройте его явно: в действии или локации текстом поставьте «-», в персонажах выберите «без персонажей».</p>
      <ul class="unready">${list.map((s) => `<li><a href="#" data-goshot="${s.id}"><b>Шот ${esc(s.label)}</b></a>: ${s.items.map(esc).join("; ")}</li>`).join("")}</ul>
      <div class="row" style="margin-top:14px">${can.admin() ? `<button class="ghost danger" id="ur-force">Отправить всё равно (под мою ответственность)</button>` : ""}
        <div class="spacer"></div><button class="primary" id="ur-ok">Понятно</button></div>`);
    $("#ur-ok", m).onclick = () => { closeModal(); done(false); };
    $("#ur-force", m) && ($("#ur-force", m).onclick = () => { closeModal(); done(true); });
    $$("[data-goshot]", m).forEach((a) => (a.onclick = (e) => {
      e.preventDefault(); closeModal(); done(false);
      E.sel = +a.dataset.goshot; E.tab = "shots"; draw();
      setTimeout(() => $(`#shot-${E.sel}`)?.scrollIntoView({ behavior: "smooth", block: "start" }), 50);
    }));
  });
}

/** Назад, номер, название и статус серии: общая строка обеих шапок. */
function titleRow(ep) {
  return `<button class="ghost" id="ep-back" title="Назад">←</button>
    <div class="ep-badge" style="--c:${STATUS_COLORS[ep.status]}">${ep.number != null ? `Серия ${ep.arc_number ?? ep.number}` : "черновик"}</div>
    <div style="flex:1;min-width:220px">
      <input id="ep-title" class="title-input" value="${esc(ep.title)}" placeholder="Название серии" ${canEp() ? "" : "disabled"}>
    </div>
    ${can.episodes() ? `<button class="ghost urgent-btn ${ep.urgent ? "on" : ""}" id="ep-urgent" title="${ep.urgent ? "Серия срочная. Нажмите, чтобы снять отметку" : "Срочная серия: на выбранный день, наверх в «Продакшне» и значок в меню"}">🔥 ${ep.urgent ? "Срочно" : "Сделать срочной"}</button>`
      : ep.urgent ? `<span class="badge urgent-badge">🔥 Срочно</span>` : ""}
    ${statusSelect(ep.status, 'id="ep-status"')}
    ${can.episodes() ? `<button class="ghost danger" id="ep-del" title="Удалить серию (30 дней её можно вернуть из корзины)" aria-label="Удалить серию">🗑</button>` : ""}`;
}

function bindTitleRow(ep) {
  $("#ep-back").onclick = () => goBack(`#/series/${ep.arc_id ?? "none"}`);
  $("#ep-del") && ($("#ep-del").onclick = () => deleteEpisode(ep, () => (location.hash = `#/series/${ep.arc_id ?? "none"}`)));
  $("#ep-urgent") && ($("#ep-urgent").onclick = async () => {
    if (E.ep.urgent) {
      await api(`/api/episodes/${E.id}`, { method: "PUT", json: { urgent: false } });
      toast("Отметка «срочно» снята");
      return renderEpisode(E.id);
    }
    urgentDialog();
  });
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
  if (canEp()) {
    $("#ep-title").onchange = async (e) => { await api(`/api/episodes/${E.id}`, { method: "PUT", json: { title: e.target.value } }); toast("Название сохранено"); };
  }
}

function drawScriptHeader() {
  const ep = E.ep;
  $("#ep-header").innerHTML = `
    <div class="ep-head ep-head-min">
      <div class="row">${titleRow(ep)}</div>
      <label>Синопсис <span class="muted small">— коротко о чём серия; из него пишется ТЗ</span></label>
      <textarea id="ep-synopsis" rows="3" placeholder="О чём серия…" ${canEp() ? "" : "readonly"}>${esc(ep.synopsis || "")}</textarea>
    </div>`;
  bindTitleRow(ep);
  $("#ep-synopsis").onchange = async (ev) => {
    await api(`/api/episodes/${E.id}?reparse=false`, { method: "PUT", json: { synopsis: ev.target.value } });
    ep.synopsis = ev.target.value;
    toast("Синопсис сохранён");
  };
}

const canResult = () => can.episodes() || can.gen(); // итог и ссылку на ролик пишет любой из команды, кроме зрителя

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

/** Отметить серию срочной и поставить на конкретный день: она закрепляется, остальные сдвигаются (1 серия в день). */
function urgentDialog() {
  const ep = E.ep;
  const m = modal(`<h1>🔥 Срочная серия</h1>
    <label>День выхода</label><input id="ur-date" type="date" value="${ep.date || state.meta.today}" style="width:200px">
    <p class="muted small">Серия встанет на этот день и закрепится 📌. Серия, которая стояла на нём, и следующие сдвинутся на день вперёд. Закреплённые 📌 серии на других днях не двигаются.</p>
    <label class="check"><input type="checkbox" id="ur-keep" ${can.schedule() ? "" : "checked disabled"}> день не менять, только отметить срочной${can.schedule() ? "" : " <span class=\"muted small\">(менять даты вам не открыто)</span>"}</label>
    <div class="row" style="margin-top:14px"><div class="spacer"></div><button class="ghost" id="ur-cancel">Отмена</button>
      <button class="primary" id="ur-go">Отметить срочной</button></div>`);
  $("#ur-keep", m).onchange = (e) => { $("#ur-date", m).disabled = e.target.checked; };
  if (!can.schedule()) $("#ur-date", m).disabled = true;
  $("#ur-cancel", m).onclick = closeModal;
  $("#ur-go", m).onclick = async () => {
    const day = $("#ur-date", m).value;
    const keep = $("#ur-keep", m).checked;
    if (!keep && !day) return toast("Выберите день", true);
    await api(`/api/episodes/${E.id}`, { method: "PUT", json: { urgent: true } });
    if (!keep) {
      await api(`/api/episodes/${E.id}/move`, { json: { date: day, pin: true } });
    }
    closeModal();
    toast(keep ? "Серия отмечена как срочная" : `Срочная серия стоит на ${fmtDate(day)}, остальные сдвинуты`);
    renderEpisode(E.id);
  };
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
        <summary>Описание для поста <span class="muted small" id="post-count"></span></summary>
        <label class="post-same"><input type="checkbox" id="post-same" ${ep.post_same === 0 ? "" : "checked"} ${canPost() ? "" : "disabled"}> Одинаковое описание везде</label>
        <div id="post-blocks"></div>
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
  drawPostBlocks(ep);
  bindResult(ep);
  if (canPost()) $("#post-same").onchange = async (ev) => {
    const same = ev.target.checked;
    const patch = { post_same: same };
    // Включили отдельные описания впервые: каждая соцсеть стартует с общего текста, ничего не теряется
    if (!same && POST_NETS.every(([k]) => !ep[`post_text_${k}`])) POST_NETS.forEach(([k]) => (patch[`post_text_${k}`] = ep.post_text || ""));
    await api(`/api/episodes/${E.id}?reparse=false`, { method: "PUT", json: patch });
    ep.post_same = same ? 1 : 0;
    Object.entries(patch).forEach(([k, v]) => { if (k.startsWith("post_text_")) ep[k] = v; });
    drawPostBlocks(ep);
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
  const addHint = !canAddShot() ? ""
    : ep.marked ? `Не хватает шота по режиссуре? Нажмите «＋ Добавить шот» между карточками: он получит номер вроде «2а», остальные шоты не сдвинутся. Это можно и после согласования ТЗ.`
      : `Вставить шот между готовыми можно, когда в сценарии стоят пометки «Шот 1», «Шот 2»…: без них номера шотов идут по порядку и сдвинулись бы.`;
  el.innerHTML = ep.shots.length
    ? `${writerLocked() ? `<div class="help warn-card" style="margin-bottom:12px">🔒 Сценарий согласован, править шоты может только продюсер. Чтобы внести правки, попросите вернуть серию в «В разработке».${canAddShot() && ep.marked ? " Новый шот между готовыми добавить можно." : ""}</div>` : ""}
       <div class="help" style="margin-bottom:12px">${can.write()
         ? `Шоты строятся из сценария. Здесь выбирают персонажей, локацию и тип озвучки реплик. Правки полей перезапишутся, если поменять текст этого шота в сценарии;
            референс композиции, дубли, комментарии и вручную изменённый промпт сохраняются.`
         : `Сценарий этой серии согласован. Содержание шотов смотрите слева, а промпты и генерацию настраивайте справа.`}${addHint ? `<br>${addHint}` : ""}</div>
       <div id="shots">${ep.shots.map((s, i) => shotHtml(s) + gapHtml(s, ep.shots[i + 1])).join("")}</div>`
    : `<div class="card"><p>Шотов пока нет.</p><p class="muted">Напишите сценарий на вкладке «Сценарий» и нажмите «Сохранить и разбить на шоты».</p></div>`;
  ep.shots.forEach(bindShot);
  ep.shots.forEach((s) => bindGap(s.id));
}

// ---------- промежуточный шот ----------
// Между карточками шотов: «＋ шот между 2 и 3». Текст нового шота уходит в сценарий блоком «Шот 2а», номера остальных не сдвигаются.
const canAddShot = () => can.write() || can.gen(); // дописать шот можно и после согласования ТЗ: существующие шоты не меняются
function gapHtml(s, next) {
  if (!canAddShot() || !E.ep.marked) return "";
  const where = next ? `между ${esc(s.label)} и ${esc(next.label)}` : `в конец, после ${esc(s.label)}`;
  return `<div class="shot-gap ${E.gap === s.id ? "open" : ""}" id="gap-${s.id}">${E.gap === s.id
    ? `<div class="card gap-form">
        <b>Новый шот ${where}</b>
        <textarea id="gap-text" rows="4" placeholder="Что в кадре. Реплики как в сценарии: МАША: Привет! Камера: КАМЕРА: крупный план. Героев можно выбрать через @"></textarea>
        <div class="row"><button class="primary small" data-gap-add>Добавить шот</button>
          <button class="ghost small" data-gap-cancel>Отмена</button>
          <span class="muted small">Шот встанет в сценарий с номером «${esc(s.label)}а» (или следующей буквой), номера остальных шотов не изменятся.</span></div>
      </div>`
    : `<button class="ghost small" data-gap-open>＋ Добавить шот ${where}</button>`}</div>`;
}

function refreshGap(sid) {
  const box = $(`#gap-${sid}`);
  const i = E.ep.shots.findIndex((s) => s.id === sid);
  if (!box || i < 0) return;
  box.outerHTML = gapHtml(E.ep.shots[i], E.ep.shots[i + 1]);
  bindGap(sid);
}

function bindGap(sid) {
  const box = $(`#gap-${sid}`);
  if (!box) return;
  const ta = $("#gap-text", box);
  if (ta) attachMentions(ta);
  $("[data-gap-open]", box)?.addEventListener("click", () => {
    const prev = E.gap;
    E.gap = sid;
    if (prev && prev !== sid) refreshGap(prev);
    refreshGap(sid);
    $("#gap-text")?.focus();
  });
  $("[data-gap-cancel]", box)?.addEventListener("click", () => { E.gap = null; refreshGap(sid); });
  const add = async () => {
    const text = ta.value.trim();
    if (!text) { toast("Опишите, что происходит в новом шоте", true); return; }
    const btn = $("[data-gap-add]", box);
    btn.disabled = true;
    try {
      const res = await api(`/api/episodes/${E.id}/shots`, { json: { after: sid, text } });
      const made = res.shots.find((x) => x.id === res.inserted_shot_id);
      E.gap = null; E.ep = res; E.sig = signature(res);
      drawHeader();
      drawShots($("#ep-tab"));
      toast(`Шот ${made?.label || ""} добавлен`);
      const card = $(`#shot-${res.inserted_shot_id}`);
      if (card) { card.scrollIntoView({ block: "center", behavior: "smooth" }); card.classList.add("just-added"); setTimeout(() => card.classList.remove("just-added"), 2500); }
    } catch { btn.disabled = false; }
  };
  $("[data-gap-add]", box)?.addEventListener("click", add);
  ta?.addEventListener("keydown", (e) => { if ((e.ctrlKey || e.metaKey) && e.key === "Enter") { e.preventDefault(); add(); } });
}

export function takeBadge(s) {
  const [label, cls] = TAKE_STATUS[s.gen_status] || [s.gen_status, ""];
  const t = farmTake(s);
  const pct = t && t.status === "running" && !t.farm_err ? Math.round((t.progress || 0) * 100) : 0;  // процент фермы виден и без открытой карточки
  return `<span class="badge ${cls}" data-farm-badge="${s.id}">${label}${pct > 0 ? ` · ${pct}%` : ""}</span>`;
}

// варианты камеры: подпись в списке -> текст, который уходит в промпт; первый вариант стоит по умолчанию
const CAMERA_PRESETS = [
  ["статичная", "Static camera, locked-off, no camera movement"],
  ["ручная, лёгкая тряска", "Handheld camera, slight natural shake"],
  ["медленный наезд", "Slow push-in"],
  ["медленный отъезд", "Slow pull-back"],
  ["панорама", "Slow pan"],
  ["слежение за героем", "Tracking shot following the character"],
];
const customCam = new Set(); // шоты, где выбран «свой вариант» камеры, но текст ещё не введён

const VOICE = { direct: "в кадре", voiceover: "закадр" };

function shotHtml(s) {
  const max = state.meta.max_shot_seconds;
  const chars = state.assets.filter((a) => a.kind === "character");
  const locAssets = state.assets.filter((a) => a.kind === "location");
  const locs = locAssets.flatMap((a) => a.versions.map((v) => ({ ...v, asset: a })));
  const camCustom = customCam.has(s.id) || !CAMERA_PRESETS.some(([, v]) => v === s.camera);
  const locText = s.location_mode === "text"; // одноразовая локация: описание вместо выбора из библиотеки
  const curLoc = locs.find((v) => v.id === s.location_version_id);
  const roW = canWrite() ? "" : "disabled"; // content: the writer's part
  const roG = can.gen() ? "" : "disabled";  // generation: the editor's part
  const sel = s.selected_take;
  const omni = !!engineInfo(s.engine).frames; // Gemini Omni: на вход старт- и финальный кадры (блок «Кадры для Gemini Omni»), композиция и референсы к видео не нужны
  return `
  <div class="card shot ${can.gen() ? "" : "shot-w"} ${s.missing.length ? "is-incomplete" : ""} ${s.ready ? "is-ready" : ""}" id="shot-${s.id}">
    <div class="side">
      <div class="shot-no">Шот ${esc(s.label)}</div>
      <div>${fitMeter(s.est_seconds, s.max_s)}
        <div class="small ${s.over_limit ? "warn-t" : "muted"}">нужно ~${s.est_seconds} с${s.speech_seconds ? ` · речь ${s.speech_seconds} с · ${s.words} сл.` : ""}</div></div>
      ${s.missing.length ? `<ul class="missing">${s.missing.map((m) => `<li>${esc(m)}</li>`).join("")}</ul>` : ""}
      <ul class="checks ${s.ready ? "all" : ""}" title="Когда все пункты отмечены, рамка шота становится зелёной">${s.checks.map((c) => `<li class="${c.ok ? "ok" : ""}">${c.ok ? "✓" : "○"} ${esc(c.label)}</li>`).join("")}</ul>
      ${can.gen() ? `<div><label>Нейросеть</label><select data-f="engine">${state.meta.engines.filter((e) => !e.legacy).map((e) => `<option value="${e.key}" ${e.key === s.engine ? "selected" : ""}>${esc(e.name)}</option>`).join("")}</select>
        ${engineHelp(s)}</div>` : ""}
      <div><label>Длина клипа</label><select data-f="duration" ${roG} ${s.forced_8 ? "disabled" : ""}>${engineInfo(s.engine).durations.map((d) => `<option ${d === s.duration ? "selected" : ""} value="${d}">${d} с</option>`).join("")}</select>
        <div class="muted small">${s.forced_8 ? "8 с: с референсами Veo 3.1 делает только 8 с" : esc(engineInfo(s.engine).duration_note)}</div></div>
      <div>${takeBadge(s)} ${can.gen() && ["queued", "running"].includes(s.gen_status) ? `<button class="ghost small danger" data-act="cancel">Отменить</button>` : ""}
        ${s.takes.length ? `<span class="muted small">дублей: ${s.takes.length}${s.revisions ? ` · правок: ${s.revisions}` : ""}</span>` : ""}
        ${s.needs_redo ? `<span class="badge warn">нужна перегенерация</span>` : ""}</div>
      ${s.takes.at(-1)?.error ? `<div class="small err-t">${esc(s.takes.at(-1).error)}</div>` : ""}
      <div id="farm-prog-${s.id}">${farmProgress(s)}</div><div id="farm-draft-${s.id}">${farmDraft(s)}</div>
      ${sel?.video_path ? `<video src="${media(sel.video_path)}" controls preload="metadata"></video>` : ""}
      ${can.gen() ? `${engineInfo(s.engine).api ? "" : `<div class="small muted">Шот делается в «${esc(engineName(s.engine))}»: через API он не уходит. Сделайте видео там и загрузите файл.</div>`}
           <button data-act="copyprompt">Скопировать промпт</button>
           <label class="btn primary up-label">Загрузить готовое видео<input type="file" accept="video/mp4,video/quicktime,video/webm" data-upload hidden></label>` : ""}
    </div>
    <div>
      <div class="row loc-head"><label style="margin:0">Локация</label><div class="spacer"></div>
        <label class="check small" title="Для одноразовой локации: вместо выбора из библиотеки просто опишите, где происходит действие">
          <input type="checkbox" data-locmode ${locText ? "checked" : ""} ${roW}> текстом</label></div>
      ${locText
        ? `<textarea data-f="location_text" rows="2" ${roW} class="${s.location_text.trim() ? "" : "need"}" placeholder="Где происходит действие: например, пустая парковка ночью, под дождём">${esc(s.location_text)}</textarea>`
        : `<div class="loc-pick">
            <input data-locname list="loc-names-${s.id}" value="${esc(curLoc ? curLoc.asset.name : "")}" placeholder="— не выбрана: начните вводить название —" ${roW} class="${curLoc ? "" : "need"}">
            <datalist id="loc-names-${s.id}">${locAssets.map((a) => `<option value="${esc(a.name)}">`).join("")}</datalist>
            ${curLoc ? `<select data-locver ${roW} title="Версия локации">${curLoc.asset.versions.map((v) => `<option value="${v.id}" ${v.id === s.location_version_id ? "selected" : ""}>v${v.version_no} ${esc(v.label)}</option>`).join("")}</select>` : ""}
          </div>`}
      <label>Персонажи в кадре</label>
      <div class="chips">${s.characters.map((c, i) => {
        const a = chars.find((x) => x.id === c.asset_id);
        return `<span class="chip" style="--c:${a ? assetColor(a) : "var(--line)"}">${esc(c.name)}
          <select data-char="${i}" ${roW}>${(a ? a.versions : []).map((v) => `<option value="${v.id}" ${v.id === c.version_id ? "selected" : ""}>v${v.version_no} ${esc(v.label)}</option>`).join("")}</select>
          ${canWrite() ? `<button data-rmchar="${i}" title="Убрать">✕</button>` : ""}</span>`;
      }).join("") || (s.no_characters ? `<span class="chip">без персонажей ${canWrite() ? `<button data-nochar="0" title="Убрать">✕</button>` : ""}</span>` : `<span class="muted small need-t">не выбраны</span>`)}
        ${canWrite() ? `<select data-addchar style="width:auto"><option value="">+ добавить</option>
          ${s.characters.length || s.no_characters ? "" : `<option value="none">— без персонажей (пейзаж, макро)</option>`}
          ${chars.filter((a) => !s.characters.some((c) => c.asset_id === a.id)).map((a) => `<option value="${a.id}">${esc(a.name)}</option>`).join("")}</select>` : ""}
      </div>
      <label>Действие</label><textarea data-f="action" rows="3" ${roW}>${esc(s.action)}</textarea>
      <label>Камера</label>
      <div class="cam-pick">
        <select data-camsel ${roW}>${CAMERA_PRESETS.map(([n, v]) => `<option value="${esc(v)}" ${!camCustom && s.camera === v ? "selected" : ""}>${n}</option>`).join("")}
          <option value="" ${camCustom ? "selected" : ""}>свой вариант…</option></select>
        ${camCustom ? `<input data-f="camera" value="${esc(CAMERA_PRESETS.some(([, v]) => v === s.camera) ? "" : s.camera)}" placeholder="Опишите камеру: например, медленный облёт вокруг героини" ${roW}>` : ""}
      </div>
      <label>Звук</label>
      <div class="seg sound-mode">${Object.entries(state.meta.sound_modes).map(([k, n]) => `<button data-sound="${k}" class="${soundMode(s) === k ? "on" : ""}" ${canWrite() || can.gen() ? "" : "disabled"}>${n}</button>`).join("")}</div>
      <fieldset class="sound-zone" ${soundMode(s) === "full" ? "" : "disabled"}>
        <legend>Речь</legend>
        ${engineInfo(s.engine).audio
          ? `<div class="muted small">${esc(engineName(s.engine))} сразу генерирует звук вместе с видео: речь и шумы.</div>`
          : `<label class="check"><input type="checkbox" data-f="voiceover" ${s.voiceover ? "checked" : ""}> Озвучить реплики в ElevenLabs</label>`}
      <label>Реплики <span class="muted small">— берутся из кавычек в сценарии; красным подсвечено то, что не удалось определить. «в кадре»: персонаж говорит, идёт в промпт нейросети; «закадр»: голос за кадром, озвучивается отдельно</span></label>
      <div class="dialogue">${s.dialogue.map((d, i) => `
        <div class="dlg-line ${d.voice === "voiceover" ? "vo" : ""} ${d.auto ? "auto" : ""}" data-line="${i}">
          <input data-d="speaker" class="${!d.speaker || (d.voice !== "voiceover" && !d.asset_id) ? "need-red" : ""}" value="${esc(d.speaker)}" placeholder="Кто говорит" list="char-names-${s.id}" ${roW}>
          <select data-d="voice" ${roW} title="Как звучит реплика">${Object.entries(VOICE).map(([k, n]) => `<option value="${k}" ${(d.voice || "direct") === k ? "selected" : ""}>${n}</option>`).join("")}</select>
          <input data-d="parenthetical" class="${d.auto && !d.parenthetical ? "need-red" : ""}" value="${esc(d.parenthetical)}" placeholder="как (тихо)" ${roW}>
          <input data-d="text" value="${esc(d.text)}" placeholder="Текст реплики" ${roW}>
          ${canWrite() ? `<button class="ghost" data-rmline="${i}" title="Удалить реплику">✕</button>` : ""}
          ${voiceHtml(s, i, d)}</div>`).join("") || `<div class="muted small">без реплик</div>`}</div>
      ${canVoice() && s.dialogue.length ? `<div class="muted small">🎙 «Записать голос»: на ферме запись становится речью шота, остальные нейросети её не берут. Запись можно прослушать, увидеть её длину, оставить или удалить.</div>` : ""}
      ${canWrite() ? `<button class="ghost small" data-act="addline">+ реплика</button>` : ""}
      </fieldset>
      <datalist id="char-names-${s.id}">${[...s.characters.map((c) => c.name), ...chars.map((a) => a.name)].filter((n, i, arr) => arr.indexOf(n) === i).map((n) => `<option value="${esc(n)}">`).join("")}</datalist>
      ${s.warnings.length ? `<ul class="warnings">${s.warnings.map((w) => `<li>${esc(w)}</li>`).join("")}</ul>` : ""}
    </div>
    ${can.gen() ? `<div>
      ${omni ? "" : `<label style="margin-top:0">Референс композиции</label>
      <div class="comp ${s.composition_image ? "has" : ""}" data-drop="${s.id}">
        ${s.composition_image
          ? `<img src="${media(s.composition_image)}"><div class="comp-tools">
              <select data-f="composition_mode" ${roG}><option value="reference" ${s.composition_mode !== "first_frame" ? "selected" : ""}>как референс композиции</option>
                <option value="first_frame" ${s.composition_mode === "first_frame" ? "selected" : ""}>как первый кадр видео</option></select>
              <button class="ghost small danger" data-act="rmcomp">Убрать</button></div>`
          : `<label class="comp-empty">Перетащите картинку или нажмите — раскадровка, скетч, кадр-образец<input type="file" accept="image/png,image/jpeg,image/webp" data-comp hidden></label>`}
      </div>
      <input data-f="composition_note" value="${esc(s.composition_note)}" placeholder="Пояснение к композиции (по желанию): героиня слева, окно справа…" ${roG} style="margin-top:6px">`}
      ${s.engine === "renderfarm" ? farmPanel(s) : `      <div class="row" style="margin-top:12px"><label style="margin:0">Промпт для «${esc(engineName(s.engine))}» ${s.prompt_locked ? `<span class="badge warn">изменён вручную</span>` : `<span class="badge">авто</span>`}</label>
        <div class="spacer"></div><button class="ghost small" data-act="rebuild" title="Собрать заново из полей шота">↻ пересобрать</button></div>
      <textarea class="prompt" data-f="prompt" rows="9" style="margin-top:6px" ${roG}>${esc(s.prompt)}</textarea>`}
      ${omni ? `<div class="muted small" style="margin-top:8px">${esc(engineInfo(s.engine).refs_note)}</div>` : `<label>Картинки-референсы</label><div class="muted small">${esc(engineInfo(s.engine).refs_note)}</div>
      <div class="refs">${s.references.map((r, i) => `<figure><img src="${media(r.path)}"><figcaption>${refTag(s, r, i)} · ${r.kind === "composition" ? "композиция" : esc(r.name)}</figcaption></figure>`).join("") || `<span class="muted small">нет изображений</span>`}</div>
      ${manualBlock(s)}`}
    </div>` : ""}
    ${omni && can.gen() ? framesHtml(s) : ""}
  </div>`;
}

function replaceShot(s) {
  const i = E.ep.shots.findIndex((x) => x.id === s.id);
  if (i >= 0) E.ep.shots[i] = { ...E.ep.shots[i], ...s };
  const el = $(`#shot-${s.id}`);
  if (el) { el.outerHTML = shotHtml(E.ep.shots[i]); bindShot(E.ep.shots[i]); }
  if (s.episode_status && s.episode_status !== E.ep.status) { // все шоты заполнены — сервер сам перевёл серию в «ТЗ готово»
    E.ep.status = s.episode_status;
    E.ep.incomplete = E.ep.shots.filter((x) => x.missing.length).length;
    drawHeader();
    if (writerLocked()) drawTab(); // сценарист после «ТЗ готово» уже не правит шоты — перерисовать карточки только для чтения
    if (s.episode_status === "approved") toast("Все шоты заполнены — серия переведена в «ТЗ готово», монтажёр её увидит");
  }
}

async function uploadComposition(s, file) {
  const fd = new FormData(); fd.append("image", file);
  replaceShot(await api(`/api/shots/${s.id}/composition`, { method: "POST", body: fd }));
  toast("Референс композиции добавлен — он попадёт в промпт и в запрос к нейросети");
}

// ---------- запись голоса к реплике ----------
// Как в мессенджере: нажали «Записать голос», говорите, «Стоп», прослушали, увидели длину и решили: оставить или удалить.
// Оставленная запись хранится у реплики и уходит на ферму вместе с описанием голоса как образец для голосовой модели.
const MAX_VOICE_S = 60;
const voiceDraft = new Map(); // «шот:реплика» -> записано, но ещё не оставлено: {url, blob, seconds}
let rec = null;               // идёт запись: {key, sid, i, recorder, stream, chunks, t0, timer, cancelled}
const vkey = (sid, i) => `${sid}:${i}`;
const canVoice = () => can.write() || can.gen();
const secText = (x) => `${(+x).toFixed(1).replace(".", ",")} с`;
const clock = (ms) => { const t = Math.floor(ms / 1000); return `${Math.floor(t / 60)}:${String(t % 60).padStart(2, "0")}`; };
const curShot = (sid) => E.ep?.shots.find((x) => x.id === sid);

function dropVoiceDrafts(sid) {
  [...voiceDraft.keys()].filter((k) => k.startsWith(`${sid}:`)).forEach((k) => { URL.revokeObjectURL(voiceDraft.get(k).url); voiceDraft.delete(k); });
}

function voiceNote(s) {
  return s.engine === "renderfarm"
    ? "Уйдёт на ферму как сама речь: слова, голос и темп берутся из записи, губы идут по ней, длину шота ферма считает по записи (запись + около 2 с, не меньше 5 и не больше 10 с). Говорите реплику так, как она должна звучать."
    : `«${esc(engineName(s.engine))}» образец голоса не принимает: запись сохранена, её можно скачать и использовать вручную.`;
}

function voiceHtml(s, i, d) {
  const key = vkey(s.id, i);
  const ref = d.voice_ref;
  let body;
  if (rec?.key === key) {
    body = `<span class="rec-dot"></span> <b data-rec-time>${clock(performance.now() - rec.t0)}</b>
      <button class="small primary" data-vstop>■ Стоп</button> <button class="ghost small" data-vcancel>Отмена</button>
      <span class="muted small">идёт запись, до ${MAX_VOICE_S} с</span>`;
  } else if (voiceDraft.has(key)) {
    const v = voiceDraft.get(key);
    body = `<audio controls src="${v.url}"></audio> <b>${secText(v.seconds)}</b>
      <button class="small primary" data-vkeep>Оставить</button> <button class="ghost small" data-vdiscard>Удалить</button>
      <span class="muted small">пока не сохранено</span>`;
  } else if (ref) {
    body = `<audio controls preload="none" src="${media(ref.path)}"></audio> <b>${secText(ref.seconds)}</b>
      ${canVoice() ? `<button class="ghost small" data-vrec>Записать заново</button> <button class="ghost small danger" data-vdel>Удалить</button>` : ""}
      <a class="btn ghost small" href="${media(ref.path)}" download title="Скачать запись">⬇</a>
      <span class="muted small">${s.dialogue.length === 1 && s.speech_seconds ? `по тексту ~${secText(s.speech_seconds)} · ` : ""}${voiceNote(s)}</span>`;
  } else if (canVoice()) {
    body = `<button class="ghost small" data-vrec>🎙 Записать голос</button>`;
  } else return "";
  return `<div class="dlg-voice ${ref || voiceDraft.has(key) || rec?.key === key ? "" : "empty"} ${rec?.key === key ? "rec" : ""}" id="voice-${s.id}-${i}">${body}</div>`;
}

function refreshVoice(sid, i) {
  const s = curShot(sid), box = $(`#voice-${sid}-${i}`);
  if (!s || !box || !s.dialogue[i]) return;
  box.outerHTML = voiceHtml(s, i, s.dialogue[i]);
  bindVoiceLine(s, i);
}

function bindVoiceLine(s, i) {
  const box = $(`#voice-${s.id}-${i}`);
  if (!box) return;
  const key = vkey(s.id, i);
  $("[data-vrec]", box)?.addEventListener("click", () => startVoice(s.id, i));
  $("[data-vstop]", box)?.addEventListener("click", () => rec?.recorder.stop());
  $("[data-vcancel]", box)?.addEventListener("click", () => { if (rec) { rec.cancelled = true; rec.recorder.stop(); } });
  $("[data-vdiscard]", box)?.addEventListener("click", () => { dropVoiceDrafts(s.id); refreshVoice(s.id, i); });
  $("[data-vkeep]", box)?.addEventListener("click", async (ev) => {
    const v = voiceDraft.get(key);
    if (!v) return;
    ev.target.disabled = true;
    const fd = new FormData();
    fd.append("line", i); fd.append("seconds", v.seconds); fd.append("file", v.blob, v.blob.type === "audio/wav" ? "voice.wav" : "voice.webm");
    try {
      const res = await api(`/api/shots/${s.id}/voice`, { method: "POST", body: fd });
      dropVoiceDrafts(s.id);
      toast(`Голос к реплике сохранён (${secText(v.seconds)})`);
      replaceShot(res);
    } catch { ev.target.disabled = false; }
  });
  $("[data-vdel]", box)?.addEventListener("click", async () => replaceShot(await api(`/api/shots/${s.id}/voice?line=${i}`, { method: "DELETE" })));
}

function bindVoice(s, el) { s.dialogue.forEach((_, i) => bindVoiceLine(s, i)); }

async function startVoice(sid, i) {
  if (rec) return toast("Сначала остановите текущую запись", true);
  if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) return toast("Этот браузер не записывает звук: откройте студию в Chrome, Edge, Firefox или Safari", true);
  let stream;
  try { stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } }); }
  catch { return toast("Нет доступа к микрофону: разрешите его для сайта в браузере и нажмите ещё раз", true); }
  const recorder = new MediaRecorder(stream);
  const r = { key: vkey(sid, i), sid, i, recorder, stream, chunks: [], t0: performance.now(), cancelled: false, timer: null };
  recorder.ondataavailable = (e) => e.data.size && r.chunks.push(e.data);
  recorder.onstop = () => finishVoice(r);
  dropVoiceDrafts(sid);
  rec = r;
  recorder.start();
  r.timer = setInterval(() => {
    const t = $(`#voice-${sid}-${i} [data-rec-time]`);
    if (t) t.textContent = clock(performance.now() - r.t0);
    if (!location.hash.startsWith("#/episodes/")) { r.cancelled = true; recorder.stop(); }       // ушли со страницы: микрофон не держим
    else if (performance.now() - r.t0 >= MAX_VOICE_S * 1000 && recorder.state === "recording") recorder.stop();
  }, 200);
  refreshVoice(sid, i);
}

async function finishVoice(r) {
  clearInterval(r.timer);
  r.stream.getTracks().forEach((t) => t.stop());
  if (rec === r) rec = null;
  const elapsed = (performance.now() - r.t0) / 1000;
  if (r.cancelled) return refreshVoice(r.sid, r.i);
  const raw = new Blob(r.chunks, { type: r.recorder.mimeType || "audio/webm" });
  let out = { blob: raw, seconds: elapsed };
  try { out = await toWav(raw); } catch { /* браузер не раскодировал: отправим как записано, длина по таймеру */ }
  if (out.seconds < 0.3) { toast("Слишком короткая запись: держите кнопку записи подольше", true); return refreshVoice(r.sid, r.i); }
  voiceDraft.set(r.key, { ...out, url: URL.createObjectURL(out.blob) });
  refreshVoice(r.sid, r.i);
}

/** Запись из браузера (webm/mp4) в обычный WAV: его принимает любая голосовая модель, а длина берётся из самой записи. */
async function toWav(blob) {
  const ctx = new (window.AudioContext || window.webkitAudioContext)();
  try {
    const buf = await ctx.decodeAudioData(await blob.arrayBuffer());
    const n = buf.length, mono = new Float32Array(n);
    for (let c = 0; c < buf.numberOfChannels; c++) { const ch = buf.getChannelData(c); for (let k = 0; k < n; k++) mono[k] += ch[k] / buf.numberOfChannels; }
    const dv = new DataView(new ArrayBuffer(44 + n * 2));
    const text = (o, t) => [...t].forEach((c, k) => dv.setUint8(o + k, c.charCodeAt(0)));
    text(0, "RIFF"); dv.setUint32(4, 36 + n * 2, true); text(8, "WAVEfmt "); dv.setUint32(16, 16, true); dv.setUint16(20, 1, true);
    dv.setUint16(22, 1, true); dv.setUint32(24, buf.sampleRate, true); dv.setUint32(28, buf.sampleRate * 2, true); dv.setUint16(32, 2, true);
    dv.setUint16(34, 16, true); text(36, "data"); dv.setUint32(40, n * 2, true);
    for (let k = 0; k < n; k++) dv.setInt16(44 + k * 2, Math.max(-1, Math.min(1, mono[k])) * 0x7fff, true);
    return { blob: new Blob([dv], { type: "audio/wav" }), seconds: buf.duration };
  } finally { ctx.close(); }
}

function bindShot(s) {
  const el = $(`#shot-${s.id}`);
  if (!el) return;
  const save = async (patch) => replaceShot(await api(`/api/shots/${s.id}`, { method: "PUT", json: patch }));
  // «@» в текстовых полях шота подсказывает персонажей и локации из библиотеки (как в сценарии); выбор сразу сохраняется
  $$("[data-f=action], [data-f=camera]", el).forEach(attachHighlight); // герои из библиотеки подсвечены цветом
  if (canWrite()) {
    $$("[data-f=action], [data-f=camera], [data-d=text], [data-d=parenthetical]", el)
      .forEach((inp) => attachMentions(inp, () => inp.dispatchEvent(new Event("change"))));
  }
  const up = $("[data-upload]", el);
  if (up) up.onchange = async () => {
    const f = up.files[0]; if (!f || videoTooBig(f)) return;
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
    if (inp.type === "checkbox") v = inp.checked;
    if (f === "prompt") return save({ prompt: v, prompt_locked: true });
    save({ [f]: v });
  }));
  const camSel = $("[data-camsel]", el);
  if (camSel) camSel.onchange = () => {
    if (camSel.value) { customCam.delete(s.id); return save({ camera: camSel.value }); }
    customCam.add(s.id);
    replaceShot(s);
    $("[data-f=camera]", $(`#shot-${s.id}`))?.focus();
  };
  const locMode = $("[data-locmode]", el);
  if (locMode) locMode.onchange = () => save({ location_mode: locMode.checked ? "text" : "library" });
  const locName = $("[data-locname]", el);
  if (locName) locName.onchange = () => {
    const name = locName.value.trim().toLowerCase();
    if (!name) return save({ clear_location: true });
    const a = state.assets.find((x) => x.kind === "location" && x.name.toLowerCase() === name);
    if (!a) { toast("Такой локации нет в библиотеке. Выберите из списка или включите «текстом»"); return replaceShot(s); }
    save({ location_version_id: E.ep.cast[a.id] || a.active_version_id || a.versions[0]?.id });
  };
  const locVer = $("[data-locver]", el);
  if (locVer) locVer.onchange = () => save({ location_version_id: +locVer.value });
  $$("[data-char]", el).forEach((sel) => (sel.onchange = () => save({ characters: s.characters.map((c, i) => (i === +sel.dataset.char ? { ...c, version_id: +sel.value } : c)) })));
  $$("[data-rmchar]", el).forEach((b) => (b.onclick = () => save({ characters: s.characters.filter((_, i) => i !== +b.dataset.rmchar) })));
  $$("[data-sound]", el).forEach((b) => (b.onclick = () => save({ sound_mode: b.dataset.sound })));
  $("[data-nochar]", el) && ($("[data-nochar]", el).onclick = () => save({ no_characters: false }));
  const add = $("[data-addchar]", el);
  if (add) add.onchange = () => {
    if (add.value === "none") return save({ no_characters: true });
    const a = state.assets.find((x) => x.id === +add.value);
    if (a) save({ characters: [...s.characters, { asset_id: a.id, name: a.name, version_id: E.ep.cast[a.id] || a.active_version_id }] });
  };
  const readLines = (edited = -1) => $$(".dlg-line", el).map((row, i) => {
    const speaker = $("[data-d=speaker]", row).value.trim();
    const a = state.assets.find((x) => x.kind === "character" && x.name.toLowerCase() === speaker.toLowerCase());
    const line = { speaker: a ? a.name : speaker, asset_id: a ? a.id : null, voice: $("[data-d=voice]", row).value,
      parenthetical: $("[data-d=parenthetical]", row).value, text: $("[data-d=text]", row).value,
      voice_ref: s.dialogue[i]?.voice_ref || null };
    if (s.dialogue[i]?.auto && i !== edited) line.auto = true; // правка строки значит «проверено», красная подсветка уходит
    return line;
  });
  $$(".dlg-line input, .dlg-line select", el).forEach((inp) => (inp.onchange = () => save({ dialogue: readLines(+inp.closest(".dlg-line").dataset.line) })));
  $$("[data-rmline]", el).forEach((b) => (b.onclick = () => { dropVoiceDrafts(s.id); save({ dialogue: readLines().filter((_, i) => i !== +b.dataset.rmline) }); }));
  bindVoice(s, el);
  if (engineInfo(s.engine).frames && can.gen()) {
    bindFrames(s, {
      get: (id) => E.ep.shots.find((x) => x.id === id),
      update: (shot) => { // ответ сервера вносим в страницу, перерисовывается только блок кадров
        const i = E.ep.shots.findIndex((x) => x.id === shot.id);
        if (i >= 0) E.ep.shots[i] = { ...E.ep.shots[i], ...shot };
        E.sig = signature(E.ep);
        return E.ep.shots[i] || shot;
      },
      full: (shot) => replaceShot(shot),
    });
  }

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
    if (act === "copyprompt") return copyText(s.prompt);
    if (act === "copymanual") return copyText(s.prompt_manual);
    if (act === "cancel") return cancelTake(s);
  }));
  const fr = $("[data-farmreq]", el);
  if (fr) fr.onclick = async () => {
    const out = $("[data-farmreq-out]", el);
    out.textContent = "…";
    const q = await api(`/api/shots/${s.id}/request`);
    out.textContent = JSON.stringify(q, null, 2);
  };
}

// ---------------- history tab ----------------
async function drawHistory(el) {
  const evs = await api(`/api/episodes/${E.id}/events`);
  el.innerHTML = `<div class="card">${evs.map((e) => `<div class="event"><span class="muted small">${fmtDateTime(e.created_at)}</span>
      <b>${esc(e.user_name)}</b> ${esc(e.text)}</div>`).join("") || `<span class="muted">Пока пусто</span>`}</div>`;
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
    <label class="check urgent-check"><input type="checkbox" id="ne-urgent"> 🔥 Срочная серия <span class="muted small">— сразу наверх в «Продакшне» и значок в меню</span></label>
    <div class="muted small" id="ne-uhint" hidden>Срочная встанет в «Дату выхода» выше (закрепится 📌), серия на этом дне и следующие сдвинутся на день вперёд.</div>
    <label>Сценарий — вставьте текст или загрузите файл (можно и позже)</label>
    <div class="editor-wrap"><textarea id="ne-script" rows="12" placeholder="Шот 1&#10;..."></textarea></div>
    <input id="ne-file" type="file" accept=".txt,.md,.fountain,text/plain" style="margin-top:6px">
    <div class="row" style="margin-top:14px"><div class="spacer"></div>
      <button class="ghost" id="cancel">Отмена</button><button class="primary" id="create">Создать</button></div>`, true);
  attachMentions($("#ne-script", m));
  api("/api/arcs").then((arcs) => { $("#ne-arc", m).innerHTML += arcs.map((a) => `<option value="${a.id}" ${a.id === opts.arcId ? "selected" : ""}>${esc(a.title)}</option>`).join(""); });
  $("#cancel", m).onclick = closeModal;
  $("#ne-urgent", m).onchange = (e) => {
    $("#ne-uhint", m).hidden = !e.target.checked;
    if (e.target.checked && !$("#ne-date", m).value) $("#ne-date", m).value = state.meta.today;
    if (e.target.checked) $("#ne-backlog", m).checked = false;
  };
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
      date: $("#ne-backlog", m).checked ? null : $("#ne-date", m).value || null, backlog: $("#ne-backlog", m).checked, urgent: $("#ne-urgent", m).checked } });
    closeModal();
    location.hash = `#/episodes/${ep.id}/script`;
  };
  setTimeout(() => $("#ne-title", m).focus(), 50);
}

/** Блоки описания: один общий или по одному на соцсеть. */
function drawPostBlocks(ep) {
  const same = ep.post_same !== 0;
  const blocks = same ? [["post_text", "", "Текст под роликом: подпись, хэштеги, призыв…"]]
    : POST_NETS.map(([k, n]) => [`post_text_${k}`, n, `Описание для ${n}: подпись, хэштеги, призыв…`]);
  const len = blocks.reduce((n, [f]) => n + (ep[f] || "").length, 0);
  $("#post-count").textContent = len ? `· ${len} симв.` : "· пока не написано";
  $("#post-blocks").innerHTML = blocks.map(([f, name, ph]) => `<div class="post-block">
      ${name ? `<label>${name}</label>` : ""}
      <textarea data-post="${f}" rows="4" placeholder="${ph}" ${canPost() ? "" : "readonly"}>${esc(ep[f] || "")}</textarea>
      <div class="row"><button class="ghost small" data-copy-post="${f}">📋 Скопировать${name ? ` для ${name}` : ""}</button>
        ${name === "" ? `<span class="muted small">${canPost() ? "Пишет сценарист; сохраняется само." : "Пишет сценарист."}</span>` : ""}</div></div>`).join("");
  $$("[data-copy-post]").forEach((b) => (b.onclick = () => copyText(ep[b.dataset.copyPost] || "")));
  if (canPost()) $$("[data-post]").forEach((ta) => (ta.onchange = async () => {
    await api(`/api/episodes/${E.id}?reparse=false`, { method: "PUT", json: { [ta.dataset.post]: ta.value } });
    ep[ta.dataset.post] = ta.value;
    drawPostBlocks(ep);
    toast("Описание для поста сохранено");
  }));
}
