// «Статус»: 30 серий на одном экране — 5 прошедших дней, сегодня и дальше вперёд; этап, мини-описание, просрочки.
// Окно сдвигается само каждый день. Данные: GET /api/status-window. Страница обновляется раз в 30 секунд.
import { $, $$, api, esc, view } from "./core.js";
import { arcDivider, bindArcDividers, ensureAssets } from "./arcs.js";

// Этапы. Цвета те же, что у статусов серий на остальных страницах.
const STAGES = [
  { name: "Не начато", color: "#8b93a3" },
  { name: "Сценарий", color: "#f5b642" },
  { name: "Генерация", color: "#b36bff" },
  { name: "Правки и монтаж", color: "#ff8a3d" },
  { name: "Готов к выкладке", color: "#3ccf7e" },
  { name: "Выложено", color: "#25d0d6" },
];
// Какой статус серии в приложении к какому этапу относится
const STAGE_BY_STATUS = { dev: 1, review: 1, approved: 1, generating: 2, fixes: 3, ready: 4, posted: 5 };
const SCRIPT_SUB = { dev: "пишется", review: "на согласовании", approved: "согласован" };
const SEG_LABELS = ["Сценарий", "Генерация", "Монтаж", "К выкладке", "Выложено"];
const WD_SHORT = ["вс", "пн", "вт", "ср", "чт", "пт", "сб"];
const WD_FULL = ["воскресенье", "понедельник", "вторник", "среда", "четверг", "пятница", "суббота"];
const MONTHS_GEN = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"];

const MONTHS_NOM = ["Январь", "Февраль", "Март", "Апрель", "Май", "Июнь", "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"];
const MONTHS_IN = ["январе", "феврале", "марте", "апреле", "мае", "июне", "июле", "августе", "сентябре", "октябре", "ноябре", "декабре"];
const S = { filter: "all", hidePosted: false, data: null, mdata: null, month: "", json: "", timer: null };
const shiftMonth = (ym, k) => { const d = new Date(+ym.slice(0, 4), +ym.slice(5, 7) - 1 + k, 1); return `${d.getFullYear()}-${pad(d.getMonth() + 1)}`; };

export function stopStatusPolling() { clearInterval(S.timer); S.timer = null; }

const pad = (n) => String(n).padStart(2, "0");
const plural = (k) => (k % 10 === 1 && k % 100 !== 11 ? "день" : (k % 10 >= 2 && k % 10 <= 4 && (k % 100 < 12 || k % 100 > 14) ? "дня" : "дней"));
const daysFromToday = (iso, today) => Math.round((new Date(iso + "T00:00:00") - new Date(today + "T00:00:00")) / 86400000);
/** Слоты подряд → «3–5 окт, 8 окт». */
const dateRanges = (nums) => rng(nums).split(", ").map((part) => {
  const [a, b] = part.split("–").map(Number);
  const ra = S.rows.find((x) => x.number === a), rb = b && S.rows.find((x) => x.number === b);
  const fmt = (r) => `${r.d} ${MONTHS_GEN[r.mon].slice(0, 3)}`;
  if (!rb) return fmt(ra);
  return ra.mon === rb.mon ? `${ra.d}–${fmt(rb)}` : `${fmt(ra)} – ${fmt(rb)}`;
}).join(", ");
const rng = (a) => {
  const o = []; let i = 0;
  while (i < a.length) { let j = i; while (j + 1 < a.length && a[j + 1] === a[j] + 1) j++; o.push(j > i ? `${a[i]}–${a[j]}` : `${a[i]}`); i = j + 1; }
  return o.join(", ");
};

function stageOf(ep) {
  if (!ep) return 0;
  if (ep.status === "dev" && !ep.has_script && !ep.shots) return 0; // серия заведена, но сценария нет
  return STAGE_BY_STATUS[ep.status] ?? 1;
}

// Из данных API собираем строки: по одной на каждый слот выхода (1 серия = 1 день)
function buildRows(data, today) {
  const byNum = Object.fromEntries(data.episodes.map((e) => [e.number, e]));
  return data.slots.map((slot) => {
    const iso = slot.date;
    const ep = byNum[slot.number] || null;
    const stage = stageOf(ep);
    const isToday = iso === today;
    const diff = daysFromToday(iso, today);
    const late = diff < 0 && stage < 5;
    const todayBad = isToday && stage < 4;
    const risk = diff > 0 && diff <= 2 && stage < 4;
    const sub = stage === 1 ? SCRIPT_SUB[ep.status] : "";
    const label = !ep ? "Не начато · серии нет" : stage === 0 ? "Не начато · нет сценария" : sub ? `Сценарий · ${sub}` : STAGES[stage].name;
    let flag = "", flagCls = "";
    if (late) { flag = `Просрочено на ${-diff} ${plural(-diff)}`; flagCls = "bad"; }
    else if (todayBad) { flag = "Сегодня выход, а ролик не готов"; flagCls = "bad"; }
    else if (risk) { flag = `Горит: выход через ${diff} ${plural(diff)}`; flagCls = "risk"; }
    else if (isToday && stage === 4) { flag = "Всё готово к выкладке"; flagCls = "good"; }
    else if (isToday && stage === 5) { flag = "Выложено вовремя"; flagCls = "good"; }
    const dt = new Date(iso + "T00:00:00");
    return {
      d: dt.getDate(), mon: dt.getMonth(), iso, ep, stage, isToday, diff, late: late || todayBad, risk, label, flag, flagCls,
      wd: dt.getDay(), number: slot.number, arcNumber: slot.arc_number, arcTitle: slot.arc_title,
    };
  });
}

/** Как назвать серию в тексте: внутри арки «арка „Косплей“, серия 2», иначе сквозной номер. */
const epName = (r) => (r.arcNumber ? `арка «${r.arcTitle}», серия ${r.arcNumber}` : `серия ${r.number}`); // вне арок — сквозной

function rowHtml(r) {
  const color = STAGES[r.stage].color;
  const segs = [1, 2, 3, 4, 5].map((j) =>
    `<i style="background:${j <= r.stage ? STAGES[j].color : "var(--line)"};opacity:${j < r.stage ? 0.5 : 1}"></i>`).join("");
  const title = r.ep ? esc(r.ep.title) : "Серия не создана";
  const desc = !r.ep ? "Создайте серию на эту дату в разделе «Серии»."
    : r.ep.locked ? "Откроется, когда сценарий согласуют" : esc(r.ep.blurb || "Сценария пока нет");
  const cls = ["sm-row", r.isToday && "today", r.late && !r.isToday && "late", r.stage === 5 && !r.isToday && "past", !r.ep && "empty"].filter(Boolean).join(" ");
  const link = r.ep && !r.ep.locked; // монтажёр не открывает серии до согласования сценария
  const tag = link ? "a" : "div";
  const href = link ? ` href="#/episodes/${r.ep.id}"` : "";
  return `<${tag} class="${cls}" style="--c:${color}"${href}>
    <div class="sm-num"><b>Серия ${r.arcNumber ?? r.number}</b>${r.arcNumber ? `<span class="arc-label">${esc(r.arcTitle)}</span>` : ""}<span class="muted small">${WD_SHORT[r.wd]} · ${r.d} ${MONTHS_GEN[r.mon].slice(0, 3)}</span>${r.isToday ? `<span class="sm-tag">Сегодня</span>` : ""}</div>
    <div class="sm-info"><div class="sm-title ${r.ep ? "" : "muted"}">${title}</div><div class="sm-desc muted">${desc}</div></div>
    <div class="sm-segs">${segs}</div>
    <div class="sm-stat"><span class="status" style="--c:${color}">${esc(r.label)}</span>${r.flag ? `<span class="sm-flag ${r.flagCls}">${esc(r.flag)}</span>` : ""}</div>
  </${tag}>`;
}

function render() {
  const data = S.data;
  const today = data.today;
  const rows = buildRows(data, today); // окно в 30 серий: список и «что требует внимания»
  // Сводка, плитки и полоска считаются по выбранному календарному месяцу
  const mrows = buildRows(S.mdata, today);
  S.rows = mrows;
  const arcAt = Object.fromEntries([...data.arcs, ...S.mdata.arcs].map((a) => [a.start_number, a]));
  const by = STAGES.map(() => []);
  mrows.forEach((r) => by[r.stage].push(r.number));
  const posted = by[5].length;
  const total = rows.length;
  const mtotal = mrows.length;
  const tot = Math.max(mtotal, 1);
  const mname = MONTHS_NOM[+S.month.slice(5) - 1], minn = MONTHS_IN[+S.month.slice(5) - 1];
  const lateRows = rows.filter((r) => r.late);
  const riskRows = rows.filter((r) => r.risk && !r.late);
  const inWork = by[1].length + by[2].length + by[3].length;
  // Сколько серий окна по графику должно быть выложено к концу сегодняшнего дня
  const plan = mrows.filter((r) => r.iso <= today).length;
  const gap = Math.max(0, plan - posted);
  const todayRow = rows.find((r) => r.isToday);

  const alerts = [];
  lateRows.forEach((r) => alerts.push({ c: "var(--err)", h: "Опаздывает:", t: `${esc(epName(r))} — «${esc(r.ep?.title || "—")}» — ${r.isToday ? "выход сегодня" : `выход был ${-r.diff} ${plural(-r.diff)} назад`}, сейчас: ${esc(r.label.toLowerCase())}.` }));
  riskRows.forEach((r) => alerts.push({ c: "var(--warn)", h: "Горит:", t: `${esc(epName(r))} — «${esc(r.ep?.title || "—")}» — выход через ${r.diff} ${plural(r.diff)}, сейчас: ${esc(r.label.toLowerCase())}.` }));
  if (todayRow && todayRow.stage === 4) alerts.push({ c: "var(--accent)", h: "Сегодня:", t: `${esc(epName(todayRow))} — «${esc(todayRow.ep.title)}» готова — осталось выложить.` });
  if (todayRow && todayRow.stage === 5) alerts.push({ c: "var(--ok)", h: "Сегодня:", t: `${esc(epName(todayRow))} — «${esc(todayRow.ep.title)}» уже выложена.` });
  if (data.backlog) alerts.push({ c: "var(--muted)", h: "Без даты:", t: `серий в бэклоге — ${data.backlog}. Назначьте им номер в разделе «Серии», и они появятся здесь.` });
  if (!alerts.length) alerts.push({ c: "var(--ok)", h: "Всё по графику:", t: "опаздывающих серий нет." });

  let shown = rows;
  if (S.filter === "late") shown = rows.filter((r) => r.late);
  if (S.filter === "work") shown = rows.filter((r) => r.stage >= 1 && r.stage <= 3);
  if (S.hidePosted) shown = shown.filter((r) => r.stage < 5);

  const todayLabel = `${WD_FULL[new Date(today + "T00:00:00").getDay()]}, ${+today.slice(8)} ${MONTHS_GEN[+today.slice(5, 7) - 1]}`;
  const span = `${rows[0].d} ${MONTHS_GEN[rows[0].mon].slice(0, 3)} — ${rows.at(-1).d} ${MONTHS_GEN[rows.at(-1).mon].slice(0, 3)}`;
  const chip = (key, text) => `<button class="chip-btn ${S.filter === key ? "on" : ""}" data-sm-filter="${key}">${text}</button>`;

  view().innerHTML = `
    <div class="row sm-head">
      <h1>Статус</h1>
      <span class="muted">${span} · 5 прошедших дней, сегодня и вперёд, всего ${total} серий. Сдвигается само каждый день.</span>
      <div class="spacer"></div>
      <div class="sm-today">Сегодня: ${todayLabel}</div>
    </div>

    <div class="sm-top">
      <div class="card">
        <div class="sm-label">Выложено в ${minn} ${S.month.slice(0, 4)}</div>
        <div class="sm-big">${posted}<small> / ${mtotal}</small> <span class="muted">выложено</span></div>
        <div class="sm-bar"><i style="width:${(posted / tot) * 100}%"></i><b style="left:${(plan / tot) * 100}%"></b></div>
        <div class="muted small">Белая метка — где должны быть по графику к концу сегодняшнего дня: <b style="color:var(--text)">${plan}</b>. ${mtotal ? (gap ? `Не хватает серий: ${gap}.` : "Отставания нет.") : "В этом месяце выходов нет."}</div>
      </div>
      <div class="card sm-attn">
        <div class="sm-label">Что требует внимания</div>
        ${alerts.map((a) => `<div class="sm-alert"><i style="background:${a.c}"></i><div><b style="color:${a.c}">${a.h}</b> ${a.t}</div></div>`).join("")}
      </div>
    </div>

    <div class="row sm-month">
      <button class="ghost small" id="sm-prev" title="Предыдущий месяц">‹</button>
      <b>${mname} ${S.month.slice(0, 4)}</b>
      <button class="ghost small" id="sm-next" title="Следующий месяц">›</button>
      ${S.month !== today.slice(0, 7) ? `<button class="ghost small" id="sm-now">Текущий месяц</button>` : ""}
      <span class="muted small">Сводка, плитки и полоска ниже — за этот месяц. Список серий — скользящие ${total} дней.</span>
    </div>

    <div class="sm-tiles">
      ${STAGES.map((s, k) => `<div class="sm-tile" style="--c:${s.color}"><div class="sm-tile-name"><i></i>${s.name}</div><div class="sm-tile-n">${by[k].length}</div><div class="muted small">${by[k].length ? dateRanges(by[k]) : "—"}</div></div>`).join("")}
    </div>

    <div class="card sm-strip-card">
      <div class="sm-label">Весь ${mname.toLowerCase()} одним взглядом: 1 клетка = 1 серия = 1 день</div>
      <div class="sm-strip">
        ${mrows.map((r) => `<a class="sm-cell ${r.isToday ? "today" : ""} ${arcAt[r.number] ? "arc-start" : ""}" href="${r.ep && !r.ep.locked ? "#/episodes/" + r.ep.id : "#/queue"}" title="${esc(epName(r))}${arcAt[r.number] ? " — начало арки" : ""}: ${esc(r.label)}">
          <span class="sm-arrow"></span>
          <span class="sm-sq" style="${r.stage ? `background:${STAGES[r.stage].color};color:#111317` : ""}">${r.d}${r.late ? `<i></i>` : ""}</span></a>`).join("")}
      </div>
      <div class="muted small" style="margin-top:8px">Красная точка — серия опаздывает. Розовая рамка и стрелка — сегодняшний день. Бирюзовая черта слева — начинается новая арка.</div>
    </div>

    <div class="row sm-filters">
      ${chip("all", `Все · ${total}`)}${chip("late", `Опаздывают · ${lateRows.length}`)}${chip("work", `В работе · ${inWork}`)}
      <label class="check"><input type="checkbox" id="sm-hide" ${S.hidePosted ? "checked" : ""}> Скрыть выложенные</label>
    </div>

    <div class="sm-colhead">
      <div class="sm-num">Серия</div><div class="sm-info">О чём ролик</div>
      <div class="sm-segs">${SEG_LABELS.map((l) => `<span>${l}</span>`).join("")}</div><div class="sm-stat">Статус</div>
    </div>
    <div class="sm-rows">${shown.map((r) => (S.filter === "all" && arcAt[r.number] ? arcDivider(arcAt[r.number]) : "") + rowHtml(r)).join("")
      || `<div class="help">В этом фильтре серий нет.</div>`}</div>`;

  bindArcDividers(() => load(true));
  const go = (m) => { S.month = m; load(true); };
  $("#sm-prev").onclick = () => go(shiftMonth(S.month, -1));
  $("#sm-next").onclick = () => go(shiftMonth(S.month, 1));
  $("#sm-now") && ($("#sm-now").onclick = () => go(today.slice(0, 7)));
  $$("[data-sm-filter]").forEach((b) => b.onclick = () => { S.filter = b.dataset.smFilter; render(); });
  $("#sm-hide").onchange = (e) => { S.hidePosted = e.target.checked; render(); };
}

async function load(force = false) {
  const data = await api("/api/status-window");
  if (!S.month) S.month = data.today.slice(0, 7);
  const mdata = await api(`/api/status-window?month=${S.month}`);
  const json = JSON.stringify([data, mdata]);
  if (!force && json === S.json) return; // ничего не изменилось — не перерисовываем
  S.json = json;
  S.data = data;
  S.mdata = mdata;
  render();
}

export async function renderStatus() {
  stopStatusPolling();
  S.month = ""; // при заходе на страницу — текущий месяц
  await ensureAssets();
  await load(true);
  // «Реальное время»: раз в 30 секунд подтягиваем свежие статусы, пока открыта эта страница
  S.timer = setInterval(() => {
    if (location.hash !== "#/status") return stopStatusPolling();
    load().catch(() => {});
  }, 30000);
}
