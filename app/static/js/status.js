// «Статус месяца»: все ролики месяца на одном экране — этап, мини-описание, просрочки.
// Данные: GET /api/month-status?month=YYYY-MM. Страница сама обновляется раз в 30 секунд.
import { $, $$, api, esc, isoDate, view, MONTHS } from "./core.js";

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

const S = { month: null, filter: "all", hidePosted: false, data: null, json: "", timer: null };

export function stopStatusPolling() { clearInterval(S.timer); S.timer = null; }

const pad = (n) => String(n).padStart(2, "0");
const plural = (k) => (k % 10 === 1 && k % 100 !== 11 ? "день" : (k % 10 >= 2 && k % 10 <= 4 && (k % 100 < 12 || k % 100 > 14) ? "дня" : "дней"));
const daysFromToday = (iso, today) => Math.round((new Date(iso + "T00:00:00") - new Date(today + "T00:00:00")) / 86400000);
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

// Из данных API собираем строки: по одной на каждый день месяца
function buildRows(data, today) {
  const byDate = Object.fromEntries(data.episodes.map((e) => [e.date, e]));
  const rows = [];
  for (let d = 1; d <= data.days; d++) {
    if (data.first_number + d - 1 < 1) continue; // дни до старта сериала не показываем
    const iso = `${S.month}-${pad(d)}`;
    const ep = byDate[iso] || null;
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
    const wd = new Date(iso + "T00:00:00").getDay();
    rows.push({
      d, iso, ep, stage, isToday, diff, late: late || todayBad, risk, label, flag, flagCls, wd,
      number: data.first_number + d - 1,
    });
  }
  return rows;
}

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
    <div class="sm-num"><b>№ ${r.number}</b><span class="muted small">${WD_SHORT[r.wd]} · ${r.d} ${MONTHS_GEN[+S.month.slice(5) - 1].slice(0, 3)}</span>${r.isToday ? `<span class="sm-tag">Сегодня</span>` : ""}</div>
    <div class="sm-info"><div class="sm-title ${r.ep ? "" : "muted"}">${title}</div><div class="sm-desc muted">${desc}</div></div>
    <div class="sm-segs">${segs}</div>
    <div class="sm-stat"><span class="status" style="--c:${color}">${esc(r.label)}</span>${r.flag ? `<span class="sm-flag ${r.flagCls}">${esc(r.flag)}</span>` : ""}</div>
  </${tag}>`;
}

function render() {
  const data = S.data;
  const today = isoDate(new Date());
  const curMonth = today.slice(0, 7);
  const rows = buildRows(data, today);
  const [y, m] = S.month.split("-").map(Number);
  const by = STAGES.map(() => []);
  rows.forEach((r) => by[r.stage].push(r.number));
  const posted = by[5].length;
  const total = rows.length;
  const tot = Math.max(total, 1);
  const lateRows = rows.filter((r) => r.late);
  const riskRows = rows.filter((r) => r.risk && !r.late);
  const inWork = by[1].length + by[2].length + by[3].length;
  // Сколько серий по графику должно быть выложено к концу сегодняшнего дня
  const plan = rows.filter((r) => r.iso <= today).length;
  const gap = Math.max(0, plan - posted);
  const todayRow = rows.find((r) => r.isToday);

  const alerts = [];
  lateRows.forEach((r) => alerts.push({ c: "var(--err)", h: "Опаздывает:", t: `серия ${r.number} «${esc(r.ep?.title || "—")}» — ${r.isToday ? "выход сегодня" : `выход был ${-r.diff} ${plural(-r.diff)} назад`}, сейчас: ${esc(r.label.toLowerCase())}.` }));
  riskRows.forEach((r) => alerts.push({ c: "var(--warn)", h: "Горит:", t: `серия ${r.number} «${esc(r.ep?.title || "—")}» — выход через ${r.diff} ${plural(r.diff)}, сейчас: ${esc(r.label.toLowerCase())}.` }));
  if (todayRow && todayRow.stage === 4) alerts.push({ c: "var(--accent)", h: "Сегодня:", t: `серия ${todayRow.number} «${esc(todayRow.ep.title)}» готова — осталось выложить.` });
  if (todayRow && todayRow.stage === 5) alerts.push({ c: "var(--ok)", h: "Сегодня:", t: `серия ${todayRow.number} «${esc(todayRow.ep.title)}» уже выложена.` });
  if (data.backlog) alerts.push({ c: "var(--muted)", h: "Без даты:", t: `серий в бэклоге — ${data.backlog}. Назначьте им номер в разделе «Серии», и они появятся здесь.` });
  if (!alerts.length) alerts.push({ c: "var(--ok)", h: "Всё по графику:", t: "опаздывающих серий нет." });

  let shown = rows;
  if (S.filter === "late") shown = rows.filter((r) => r.late);
  if (S.filter === "work") shown = rows.filter((r) => r.stage >= 1 && r.stage <= 3);
  if (S.hidePosted) shown = shown.filter((r) => r.stage < 5);

  const todayLabel = `${WD_FULL[new Date().getDay()]}, ${+today.slice(8)} ${MONTHS_GEN[+today.slice(5, 7) - 1]}`;
  const chip = (key, text) => `<button class="chip-btn ${S.filter === key ? "on" : ""}" data-sm-filter="${key}">${text}</button>`;

  view().innerHTML = `
    <div class="row sm-head">
      <h1>Статус месяца</h1>
      <div class="row sm-nav"><button class="small" id="sm-prev" aria-label="Предыдущий месяц">‹</button><b>${MONTHS[m - 1]} ${y}</b><button class="small" id="sm-next" aria-label="Следующий месяц">›</button>${S.month !== curMonth ? `<button class="small ghost" id="sm-now">К текущему</button>` : ""}</div>
      <div class="spacer"></div>
      <div class="sm-today">Сегодня: ${todayLabel}</div>
    </div>

    <div class="sm-top">
      <div class="card">
        <div class="sm-label">Месяц в целом</div>
        <div class="sm-big">${posted}<small> / ${total}</small> <span class="muted">выложено</span></div>
        <div class="sm-bar"><i style="width:${(posted / tot) * 100}%"></i><b style="left:${(plan / tot) * 100}%"></b></div>
        <div class="muted small">Белая метка — где должны быть по графику к концу сегодняшнего дня: <b style="color:var(--text)">${plan}</b>. ${gap ? `Не хватает серий: ${gap}.` : "Отставания нет."}</div>
      </div>
      <div class="card sm-attn">
        <div class="sm-label">Что требует внимания</div>
        ${alerts.map((a) => `<div class="sm-alert"><i style="background:${a.c}"></i><div><b style="color:${a.c}">${a.h}</b> ${a.t}</div></div>`).join("")}
      </div>
    </div>

    <div class="sm-tiles">
      ${STAGES.map((s, k) => `<div class="sm-tile" style="--c:${s.color}"><div class="sm-tile-name"><i></i>${s.name}</div><div class="sm-tile-n">${by[k].length}</div><div class="muted small">${by[k].length ? "№ " + rng(by[k]) : "—"}</div></div>`).join("")}
    </div>

    <div class="card sm-strip-card">
      <div class="sm-label">Весь месяц одним взглядом: 1 клетка = 1 серия = 1 день</div>
      <div class="sm-strip">
        ${rows.map((r) => `<a class="sm-cell ${r.isToday ? "today" : ""}" href="${r.ep && !r.ep.locked ? "#/episodes/" + r.ep.id : "#/queue"}" title="№ ${r.number}: ${esc(r.label)}">
          <span class="sm-arrow"></span>
          <span class="sm-sq" style="${r.stage ? `background:${STAGES[r.stage].color};color:#111317` : ""}">${r.d}${r.late ? `<i></i>` : ""}</span></a>`).join("")}
      </div>
      <div class="muted small" style="margin-top:8px">Красная точка — серия опаздывает. Розовая рамка и стрелка — сегодняшний день.</div>
    </div>

    <div class="row sm-filters">
      ${chip("all", `Все · ${total}`)}${chip("late", `Опаздывают · ${lateRows.length}`)}${chip("work", `В работе · ${inWork}`)}
      <label class="check"><input type="checkbox" id="sm-hide" ${S.hidePosted ? "checked" : ""}> Скрыть выложенные</label>
    </div>

    <div class="sm-colhead">
      <div class="sm-num">Серия</div><div class="sm-info">О чём ролик</div>
      <div class="sm-segs">${SEG_LABELS.map((l) => `<span>${l}</span>`).join("")}</div><div class="sm-stat">Статус</div>
    </div>
    <div class="sm-rows">${shown.map(rowHtml).join("") || `<div class="help">В этом фильтре серий нет.</div>`}</div>`;

  $("#sm-prev").onclick = () => shiftMonth(-1);
  $("#sm-next").onclick = () => shiftMonth(1);
  $("#sm-now")?.addEventListener("click", () => { S.month = curMonth; load(true); });
  $$("[data-sm-filter]").forEach((b) => b.onclick = () => { S.filter = b.dataset.smFilter; render(); });
  $("#sm-hide").onchange = (e) => { S.hidePosted = e.target.checked; render(); };
}

function shiftMonth(delta) {
  const [y, m] = S.month.split("-").map(Number);
  const d = new Date(y, m - 1 + delta, 1);
  S.month = `${d.getFullYear()}-${pad(d.getMonth() + 1)}`;
  load(true);
}

async function load(force = false) {
  const data = await api(`/api/month-status?month=${S.month}`);
  const json = JSON.stringify(data) + isoDate(new Date());
  if (!force && json === S.json) return; // ничего не изменилось — не перерисовываем
  S.json = json;
  S.data = data;
  render();
}

export async function renderStatus() {
  stopStatusPolling();
  if (!S.month) S.month = isoDate(new Date()).slice(0, 7);
  await load(true);
  // «Реальное время»: раз в 30 секунд подтягиваем свежие статусы, пока открыта эта страница
  S.timer = setInterval(() => {
    if (location.hash !== "#/status") return stopStatusPolling();
    load().catch(() => {});
  }, 30000);
}
