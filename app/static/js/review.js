// «Просмотр и правки»: вся серия одним роликом в рамке соцсети, указатель шота на таймлайне, правки по точкам времени.
import { $, $$, api, can, esc, fmtDateTime, media, state, toast } from "./core.js";
import { E, TAKE_STATUS, drawHeader, renderEpisode, takeBadge } from "./episode.js";

const FEEDS = [["none", "Без ленты"], ["tiktok", "TikTok"], ["reels", "Reels"], ["shorts", "Shorts"]];
const FEED_KEY = "reviewFeed";
const R = { feed: "tiktok", safe: false, version: "cur", i: 0, local: 0, playing: false, timer: 0, dur: [], comments: [], root: null };
try { R.feed = localStorage.getItem(FEED_KEY) || "tiktok"; } catch { /* без памяти браузера тоже работает */ }

const mmss = (s) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
const shots = () => E.ep.shots;
const offsets = () => { let t = 0; return R.dur.map((d) => { const o = t; t += d; return o; }); };
const total = () => R.dur.reduce((a, b) => a + b, 0) || 1;
const globalTime = () => (offsets()[R.i] || 0) + R.local;

/** Какой дубль шота показываем: «текущий» (выбранный в монтаже) или версия серии vN (дубль с номером N, а если его нет, ближайший прежний). */
function takeFor(s, v) {
  const withVideo = s.takes.filter((t) => t.video_path);
  if (v === "cur") return s.selected_take?.video_path ? s.selected_take : null;
  return withVideo.filter((t) => t.take_no <= +v).at(-1) || withVideo[0] || null;
}
const versionCount = () => Math.max(1, ...shots().flatMap((s) => s.takes.filter((t) => t.video_path).map((t) => t.take_no)));
const versionLabel = (v) => (v === "cur" ? "Текущий монтаж" : `v${v}`);

export function stopReview() {
  R.playing = false;
  clearInterval(R.timer);
  $("video", R.root || document)?.pause?.();
}

export async function drawReview(el) {
  stopReview();
  R.root = el;
  const ep = E.ep;
  if (!ep.shots.length) { el.innerHTML = `<div class="card muted">Шотов нет — сначала разбейте сценарий.</div>`; return; }
  R.comments = await api(`/api/episodes/${E.id}/comments`);
  R.dur = ep.shots.map((s) => s.duration);
  R.i = Math.max(0, ep.shots.findIndex((s) => s.id === E.sel));
  R.local = 0;
  if (R.version !== "cur" && +R.version > versionCount()) R.version = "cur";
  const redo = ep.shots.filter((x) => x.needs_redo || x.open_fixes).length;
  const vs = versionCount();
  el.innerHTML = `
    <div class="review">
      <div class="review-left">${phone()}</div>
      <div class="review-right">
        <div class="row" style="gap:8px;flex-wrap:wrap">
          <div class="seg" id="versions">${["cur", ...Array.from({ length: vs }, (_, i) => String(i + 1))]
            .map((v) => `<button data-ver="${v}" class="${R.version === v ? "on" : ""}">${v === "cur" ? "Текущий" : `v${v}`}</button>`).join("")}</div>
        </div>
        <div class="seg" id="feeds" style="margin-top:10px">${FEEDS.map(([k, n]) => `<button data-feed="${k}" class="${R.feed === k ? "on" : ""}">${n}</button>`).join("")}
          <button data-safe title="Показать безопасные зоны: где интерфейс соцсети закрывает картинку" class="${R.safe ? "on" : ""}">◎</button></div>
        <div class="tl" id="tl"></div>
        <div class="row tl-controls"><button class="round" id="p-play" title="Смотреть серию целиком">▶</button>
          <span class="mono" id="p-time"></span><span class="muted small" id="p-shot"></span><div class="spacer"></div>
          <button class="ghost small" id="p-prev" title="Предыдущий шот">⏮</button><button class="ghost small" id="p-next" title="Следующий шот">⏭</button></div>
        ${can.gen() && redo ? `<div class="card warn-card row" style="margin-top:12px">Шотов с правками: <b>${redo}</b><div class="spacer"></div>
          <button class="primary" id="redo-all">Перегенерировать их с текущими промптами</button></div>` : ""}
        <div class="fb-head row"><h2 id="fb-title" style="margin:0"></h2><div class="spacer"></div>
          <button class="ghost" id="add-note"></button></div>
        <div class="comment-form" id="note-form" hidden>
          <textarea id="c-text" rows="2" placeholder="Что поправить? (Ctrl+Enter — отправить)"></textarea>
          <div class="row">
            ${can.gen() ? `<label class="check"><input type="checkbox" id="c-fix"> пометить шот на перегенерацию</label>` : ""}
            <label class="check"><input type="checkbox" id="c-ep"> ко всей серии</label>
            <div class="spacer"></div><button class="ghost" id="c-cancel">Отмена</button><button class="primary" id="c-send">Отправить</button></div>
        </div>
        <div class="comments fb-list" id="comments"></div>
        <details class="card shot-tools" id="shot-tools"></details>
      </div>
    </div>`;
  bindChrome();
  mount(R.i, 0, false);
  drawTrack();
  drawComments();
  drawShotTools();
}

// ---------------- рамка телефона ----------------

function phone() {
  const ep = E.ep;
  const series = state.meta?.series_title || "Сериал";
  const user = "@" + series.toLowerCase().replace(/[^a-zа-яё0-9]+/gi, "_").replace(/^_|_$/g, "");
  const caption = esc((ep.title || "Серия") + (ep.post_text ? " · " + ep.post_text.replace(/\s+/g, " ").slice(0, 90) : ""));
  const rail = {
    tiktok: [["♥", "12,4K"], ["💬", "318"], ["🔖", "1 204"], ["➦", "87"]],
    reels: [["♥", "12,4K"], ["💬", "318"], ["➤", "87"], ["⋯", ""]],
    shorts: [["👍", "12K"], ["👎", "Не нравится"], ["💬", "318"], ["➦", "Поделиться"]],
  }[R.feed];
  const top = {
    tiktok: `<span class="dim">Подписки</span><b>Для вас</b><span class="srch">⌕</span>`,
    reels: `<b>Reels</b><span class="srch">◎</span>`,
    shorts: `<b>Shorts</b><span class="srch">⌕</span>`,
  }[R.feed];
  const nav = {
    tiktok: ["⌂", "👥", "＋", "✉", "☺"], reels: ["⌂", "▶", "➤", "⌕", "☺"], shorts: ["⌂", "▶", "＋", "☰", "☺"],
  }[R.feed];
  const ui = R.feed === "none" ? "" : `<div class="ph-ui ph-${R.feed}">
      <div class="ph-top">${top}</div>
      <div class="ph-rail"><span class="av"></span>${rail.map(([i, n]) => `<span class="ic">${i}<small>${n}</small></span>`).join("")}</div>
      <div class="ph-bottom"><b>${esc(user)}</b><span>${caption}</span><small>оригинальный звук</small></div>
      <div class="ph-nav">${nav.map((i) => `<span>${i}</span>`).join("")}</div></div>`;
  const safe = R.safe && R.feed !== "none" ? `<div class="ph-safe"><i class="t"></i><i class="b"></i><i class="r"></i></div>` : "";
  return `<div class="phone"><div class="ph-screen"><div class="ph-media" id="ph-media"></div>${ui}${safe}</div></div>`;
}

// ---------------- воспроизведение одним роликом ----------------

function placeholder(s, t, broken) {
  const why = broken ? `Браузер не смог показать видео. <a href="${media(t.video_path)}" target="_blank" rel="noopener">Открыть файл ↗</a>`
    : t?.status === "stub" ? "Заглушка: видео не генерировалось" : "Видео ещё нет";
  return `<div class="placeholder"><div class="ph-label">Шот ${esc(s.label)} · ${s.duration} с</div><div class="ph-why">${why}</div>
    <div class="ph-text">${esc(s.action)}${s.dialogue.map((d) => `<br><b>${esc(d.speaker)}:</b> «${esc(d.text)}»`).join("")}</div></div>`;
}

/** Показывает шот i, встаёт на секунду local внутри него; autoplay продолжает воспроизведение. */
function mount(i, local, autoplay) {
  clearInterval(R.timer);
  R.i = i;
  R.local = local;
  const s = shots()[i];
  E.sel = s.id;
  const box = $("#ph-media");
  if (!box) return;
  const t = takeFor(s, R.version);
  if (t) {
    box.innerHTML = `<video playsinline preload="auto" src="${media(t.video_path)}"></video>`;
    const v = $("video", box);
    v.onloadedmetadata = () => {
      if (Number.isFinite(v.duration) && Math.abs(v.duration - R.dur[i]) > 0.05) { R.dur[i] = v.duration; drawTrack(); }
      v.currentTime = Math.min(R.local, Math.max(0, v.duration - 0.05));
      if (autoplay) v.play().catch(() => {});
    };
    v.ontimeupdate = () => { R.local = v.currentTime; paint(); };
    v.onended = () => next();
    v.onerror = () => { box.innerHTML = placeholder(s, t, true); if (R.playing) tick(i); };
  } else {
    box.innerHTML = placeholder(s, t);
    if (autoplay) tick(i);
  }
  paint();
  if ($("#shot-tools")) drawShotTools();
}

/** Без видео шот «проигрывается» по таймеру его длительности, чтобы серия шла целиком. */
function tick(i) {
  clearInterval(R.timer);
  R.timer = setInterval(() => {
    R.local += 0.1;
    if (R.local >= R.dur[i]) { clearInterval(R.timer); next(); } else paint();
  }, 100);
}

function next() {
  if (!R.playing) return;
  if (R.i + 1 < shots().length) mount(R.i + 1, 0, true);
  else { R.playing = false; setPlayBtn(); }
}

function setPlayBtn() { const b = $("#p-play"); if (b) b.textContent = R.playing ? "⏸" : "▶"; }

function play() {
  R.playing = true;
  setPlayBtn();
  if (R.i === shots().length - 1 && R.local >= R.dur[R.i] - 0.2) return mount(0, 0, true);
  const v = $("#ph-media video");
  if (v) v.play().catch(() => {});
  else mount(R.i, R.local, true);
}
function pause() { R.playing = false; clearInterval(R.timer); $("#ph-media video")?.pause(); setPlayBtn(); }

function seek(gt) {
  const offs = offsets();
  gt = Math.min(Math.max(0, gt), total() - 0.01);
  let i = offs.findLastIndex((o) => o <= gt);
  if (i < 0) i = 0;
  const local = gt - offs[i];
  const was = R.playing;
  if (i === R.i && $("#ph-media video")) {
    R.local = local;
    $("#ph-media video").currentTime = local;
    paint();
  } else mount(i, local, was);
}

// ---------------- таймлайн ----------------

function drawTrack() {
  const tl = $("#tl");
  if (!tl) return;
  const offs = offsets(), T = total();
  const pins = R.comments.filter((c) => c.shot_id && c.timecode != null && visible(c)).map((c) => {
    const i = shots().findIndex((s) => s.id === c.shot_id);
    return i < 0 ? "" : `<i class="pin ${c.is_fix ? "fix" : ""} ${c.resolved ? "done" : ""}" style="left:${((offs[i] + c.timecode) / T) * 100}%" data-pin="${c.id}" title="${esc(c.text)}"></i>`;
  }).join("");
  tl.innerHTML = `<div class="tl-track"><div class="tl-fill" id="tl-fill"></div>
    ${shots().map((s, i) => `<b class="tl-mark" data-shot="${i}" style="left:${(offs[i] / T) * 100}%" title="Шот ${esc(s.label)}">${esc(s.label)}</b>`).join("")}
    ${pins}<div class="tl-head" id="tl-head"></div></div>`;
  const track = $(".tl-track", tl);
  const at = (e) => { const r = track.getBoundingClientRect(); return Math.min(1, Math.max(0, (e.clientX - r.left) / r.width)) * T; };
  track.onpointerdown = (e) => {
    if (e.target.closest("[data-shot]")) { return seek(offs[+e.target.closest("[data-shot]").dataset.shot]); }
    if (e.target.closest("[data-pin]")) { const c = R.comments.find((x) => x.id === +e.target.closest("[data-pin]").dataset.pin); return seekComment(c); }
    track.setPointerCapture(e.pointerId);
    seek(at(e));
    track.onpointermove = (ev) => seek(at(ev));
    track.onpointerup = () => { track.onpointermove = null; track.onpointerup = null; };
  };
  paint();
}

/** Обновляет бегунок, время и «Шот N из M» (указатель, на каком шоте сейчас менеджер). */
function paint() {
  const T = total(), gt = globalTime();
  const head = $("#tl-head"), fill = $("#tl-fill");
  if (head) head.style.left = `${(gt / T) * 100}%`;
  if (fill) fill.style.width = `${(gt / T) * 100}%`;
  $$(".tl-mark").forEach((m) => m.classList.toggle("on", +m.dataset.shot === R.i));
  const t = $("#p-time"), sh = $("#p-shot"), btn = $("#add-note");
  if (t) t.textContent = `${mmss(gt)} / ${mmss(T)}`;
  const s = shots()[R.i];
  if (sh && s) sh.textContent = `Шот ${s.label} из ${shots().length}`;
  if (btn) btn.textContent = `+ Правка на ${mmss(gt)}`;
}

// ---------------- правки ----------------

const visible = (c) => R.version === "cur" || !c.take_no || c.take_no === +R.version;
const initials = (n) => (n || "?").split(/\s+/).map((w) => w[0]).join("").slice(0, 2).toUpperCase();

function seekComment(c) {
  if (!c) return;
  const i = shots().findIndex((s) => s.id === c.shot_id);
  if (i < 0) return;
  pause();
  seek((offsets()[i] || 0) + (c.timecode || 0));
}

function drawComments() {
  const box = $("#comments");
  if (!box) return;
  const offs = offsets();
  const gtOf = (c) => { const i = shots().findIndex((s) => s.id === c.shot_id); return i < 0 ? Infinity : offs[i] + (c.timecode || 0); };
  const list = R.comments.filter(visible).sort((a, b) => gtOf(a) - gtOf(b));
  $("#fb-title").textContent = `Правки · ${versionLabel(R.version)}`;
  box.innerHTML = list.map((c) => `
    <div class="comment fb ${c.is_fix ? "fix" : ""} ${c.resolved ? "resolved" : ""}" data-c="${c.id}">
      <span class="tc" title="${c.shot_id ? "Перейти к этому моменту" : "К серии целиком"}">${c.shot_id && c.timecode != null ? mmss(gtOf(c)) : c.shot_id ? `шот ${esc(c.shot_label)}` : "серия"}</span>
      <div class="fb-body">
        <div class="fb-text">${esc(c.text).replace(/\n/g, "<br>")}</div>
        <div class="muted small">${esc(c.user_name)} · ${fmtDateTime(c.created_at)}${c.shot_label ? ` · шот ${esc(c.shot_label)}` : ""}${c.take_no ? ` · дубль ${c.take_no}` : ""}${c.is_fix ? ` · <b class="warn-t">правка</b>` : ""}
          ${c.resolved && c.resolved_by_name ? ` · исправлено: ${esc(c.resolved_by_name)}` : ""}</div></div>
      <span class="avatar" title="${esc(c.user_name)}">${esc(initials(c.user_name))}</span>
      ${c.is_fix && can.gen() ? `<label class="check" style="margin:0" title="Исправлено"><input type="checkbox" data-resolve="${c.id}" ${c.resolved ? "checked" : ""}></label>` : ""}
      ${c.user_id === state.meta.user.id || can.admin() ? `<button class="ghost small" data-delc="${c.id}" title="Удалить">✕</button>` : ""}
    </div>`).join("") || `<div class="muted small">Правок пока нет. Поставьте воспроизведение на нужный момент и нажмите «+ Правка».</div>`;
  const reload = (l) => { R.comments = l; drawComments(); drawTrack(); };
  $$("[data-resolve]", box).forEach((cb) => (cb.onchange = async () => reload(await api(`/api/comments/${cb.dataset.resolve}/resolve`, { json: { resolved: cb.checked } }))));
  $$("[data-delc]", box).forEach((b) => (b.onclick = async (e) => { e.stopPropagation(); if (confirm("Удалить комментарий?")) reload(await api(`/api/comments/${b.dataset.delc}`, { method: "DELETE" })); }));
  $$(".comment", box).forEach((row) => (row.onclick = (e) => {
    if (e.target.closest("input,button,label")) return;
    const c = R.comments.find((x) => x.id === +row.dataset.c);
    if (c?.shot_id) seekComment(c);
  }));
}

function bindChrome() {
  const el = R.root;
  $("#p-play").onclick = () => (R.playing ? pause() : play());
  $("#ph-media").onclick = () => (R.playing ? pause() : play());
  $("#p-prev").onclick = () => { const o = offsets(); seek(R.local > 1 ? o[R.i] : o[Math.max(0, R.i - 1)]); };
  $("#p-next").onclick = () => seek((offsets()[R.i + 1] ?? total() - 0.02));
  $$("[data-ver]", el).forEach((b) => (b.onclick = () => { R.version = b.dataset.ver; drawReview(el); }));
  $$("[data-feed]", el).forEach((b) => (b.onclick = () => {
    R.feed = b.dataset.feed;
    try { localStorage.setItem(FEED_KEY, R.feed); } catch { /* ничего страшного */ }
    drawReview(el);
  }));
  $("[data-safe]", el).onclick = () => { R.safe = !R.safe; drawReview(el); };
  $("#redo-all") && ($("#redo-all").onclick = async () => {
    const r = await api(`/api/episodes/${E.id}/generate`, { json: { mode: "redo" } });
    toast(`Отправлено на перегенерацию: ${r.started}`); renderEpisode(E.id);
  });

  const form = $("#note-form");
  $("#add-note").onclick = () => { pause(); form.hidden = !form.hidden; if (!form.hidden) $("#c-text").focus(); };
  $("#c-cancel").onclick = () => { form.hidden = true; };
  const send = async () => {
    const text = $("#c-text").value.trim();
    if (!text) return;
    const whole = $("#c-ep").checked;
    const s = shots()[R.i];
    const fix = !!$("#c-fix")?.checked;
    const list = await api(`/api/episodes/${E.id}/comments`, { json: {
      text, is_fix: fix, shot_id: whole ? null : s.id, take_id: whole ? null : takeFor(s, R.version)?.id ?? null,
      timecode: whole ? null : Math.round(R.local * 10) / 10 } });
    $("#c-text").value = "";
    form.hidden = true;
    if (fix) { E.ep = await api(`/api/episodes/${E.id}`); drawHeader(); return drawReview(el); }
    R.comments = list;
    drawComments();
    drawTrack();
  };
  $("#c-send").onclick = send;
  $("#c-text").addEventListener("keydown", (e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) send(); });
}

// ---------------- дубли и перегенерация выбранного шота (монтажёр) ----------------

function drawShotTools() {
  const box = $("#shot-tools");
  if (!box) return;
  const s = shots()[R.i];
  const open = box.open;
  box.innerHTML = `<summary><b>Шот ${esc(s.label)}: дубли${can.gen() ? " и перегенерация" : ""}</b> ${takeBadge(s)}
      <span class="muted small">дублей: ${s.takes.length}${s.revisions ? ` · правок: ${s.revisions}` : ""}</span></summary>
    <div class="row" style="margin:8px 0">${can.gen() ? `<label class="btn" style="margin:0">Загрузить видео<input type="file" id="up-take" accept="video/mp4,video/quicktime,video/webm" hidden></label>` : ""}</div>
    <div class="muted small" style="margin:6px 0">${esc(s.action || "")} ${s.dialogue.map((d) => `<br><b>${esc(d.speaker)}</b>${d.voice === "voiceover" ? " <i>(за кадром)</i>" : ""}: ${esc(d.text)}`).join("")}</div>
    ${can.gen() && s.engine === "veo" ? `<details class="regen" id="regen" ${s.needs_redo || s.open_fixes ? "open" : ""}>
      <summary><b>${s.takes.length ? "Перегенерировать шот" : "Сгенерировать шот"}</b>
        <span class="muted small">— можно улучшить промпт перед запуском; новый дубль не удаляет старые</span></summary>
      <label>Промпт для нового дубля</label>
      <textarea id="r-prompt" rows="8" class="prompt">${esc(s.prompt)}</textarea>
      <label>Что не так с прошлым дублем <span class="muted small">(запишется в историю шота)</span></label>
      <input id="r-reason" placeholder="например: лицо поплыло, камера слишком далеко">
      <div class="row" style="margin-top:10px"><button class="ghost small" id="r-reset" title="Вернуть промпт из карточки шота">↻ как в карточке</button>
        <div class="spacer"></div><button class="primary" id="r-go" ${["queued", "running"].includes(s.gen_status) ? "disabled" : ""}>▶ Сгенерировать дубль ${s.takes.length + 1}</button></div>
    </details>` : can.gen() ? `<div class="muted small">Шот делается не в Veo: сделайте новое видео в своей нейросети и загрузите файлом.</div>` : ""}
    <div class="takes">${s.takes.length ? [...s.takes].reverse().map((t) => {
      const [lab, cls] = TAKE_STATUS[t.status] || [t.status, ""];
      return `<div class="take ${t.id === s.selected_take_id ? "on" : ""}">
        <b>Дубль ${t.take_no}</b> <span class="badge ${cls}">${lab}</span>
        <span class="muted small">${fmtDateTime(t.created_at)}${t.source === "upload" ? " · загружен вручную" : ""}</span>
        <div class="spacer"></div>
        ${t.request_path ? `<a class="small" href="${media(t.request_path)}" target="_blank">запрос</a>` : ""}
        ${t.id === s.selected_take_id ? `<span class="badge ok">в монтаже</span>` : can.gen() && ["done", "stub"].includes(t.status) ? `<button class="small" data-take="${t.id}">Выбрать</button>` : ""}
        ${t.reason ? `<div class="small" style="width:100%">Почему перегенерировали: ${esc(t.reason)}</div>` : ""}
        ${t.error && t.status === "error" ? `<div class="small err-t" style="width:100%">${esc(t.error)}</div>` : ""}
        ${t.prompt ? `<details style="width:100%"><summary class="small muted">промпт этого дубля</summary>
          <pre class="code small">${esc(t.prompt)}</pre>
          ${can.gen() ? `<button class="small" data-reuse="${t.id}">Взять этот промпт</button>` : ""}</details>` : ""}
      </div>`;
    }).join("") : `<div class="muted small">Дублей ещё нет.</div>`}</div>`;
  box.open = open || !s.takes.length;

  $$("[data-take]", box).forEach((b) => (b.onclick = async () => {
    await api(`/api/shots/${s.id}/select-take`, { json: { take_id: +b.dataset.take } });
    await renderEpisode(E.id);
  }));
  const go = $("#r-go", box);
  if (go) {
    go.onclick = async () => {
      const prompt = $("#r-prompt").value.trim();
      if (!prompt) return toast("Промпт не может быть пустым", true);
      go.disabled = true;
      await api(`/api/shots/${s.id}/generate`, { json: { prompt, reason: $("#r-reason").value } });
      toast(`Шот ${s.label}: новый дубль отправлен в Veo`);
      renderEpisode(E.id);
    };
    $("#r-reset").onclick = () => { $("#r-prompt").value = s.prompt; };
    $$("[data-reuse]", box).forEach((b) => (b.onclick = () => {
      const t = s.takes.find((x) => x.id === +b.dataset.reuse);
      $("#r-prompt").value = t.prompt;
      $("#regen").open = true;
      $("#regen").scrollIntoView({ behavior: "smooth", block: "center" });
      toast(`Промпт дубля ${t.take_no} подставлен: отредактируйте и запускайте`);
    }));
  }
  const up = $("#up-take", box);
  if (up) up.onchange = async () => {
    const f = up.files[0]; if (!f) return;
    const fd = new FormData(); fd.append("video", f);
    toast("Загружаю видео…");
    await api(`/api/shots/${s.id}/upload-take`, { method: "POST", body: fd });
    renderEpisode(E.id);
  };
}
