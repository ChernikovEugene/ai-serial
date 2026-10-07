// «Продакшн» — канбан нейронщика-монтажёра: все серии очереди по этапам производства, срочные сверху.
// Карточки перетаскиваются между колонками — это и есть смена статуса серии. «Выложено ✓» убирает серию с доски.
// Серии без готового ТЗ видны в «Ждёт ТЗ» (название, дата, арка), но не открываются и не двигаются.
import { $$, api, can, esc, fmtDate, state, STATUS_COLORS, toast, view } from "./core.js";
import { copyText } from "./episode.js";

const COLUMNS = [
  { key: "wait", name: "Ждёт ТЗ", hint: "сценарист ещё пишет", statuses: ["synopsis", "synopsis_review", "synopsis_ok", "dev", "review"] },
  { key: "approved", name: "ТЗ готово", hint: "можно генерировать", statuses: ["approved"], drop: "approved" },
  { key: "generating", name: "Генерация", statuses: ["generating"], drop: "generating" },
  { key: "fixes", name: "Монтаж", statuses: ["fixes"], drop: "fixes" },
  { key: "client_review", name: "У клиента", hint: "сдано на согласование", statuses: ["client_review"], drop: "client_review" },
  { key: "ready", name: "Готов к постингу", hint: "согласовано, можно выкладывать", statuses: ["ready"], drop: "ready" },
];
const canMove = () => can.gen(); // продюсер и монтажёр

export async function renderProduction() {
  const eps = (await api("/api/production")).filter((e) => e.status !== "posted");
  const today = state.meta.today;
  view().innerHTML = `
    <div class="row"><h1>Продакшн</h1><div class="spacer"></div>
      <span class="muted small">Все арки. В колонке сверху — самое срочное по дню выхода.${canMove() ? " Перетащите карточку в другую колонку, чтобы сменить этап." : ""}</span></div>
    <div class="prod-board">
      ${COLUMNS.map((col) => {
        const items = eps.filter((e) => col.statuses.includes(e.status)).sort((a, b) => a.number - b.number);
        return `<section class="prod-col" data-drop="${col.drop || ""}" style="--c:${STATUS_COLORS[col.statuses.at(-1)]}">
          <header><b>${col.name}</b><span class="muted small">${items.length}${col.hint ? ` · ${col.hint}` : ""}</span></header>
          ${items.map((e) => card(e, today)).join("") || `<p class="muted small prod-empty">Пусто</p>`}
        </section>`;
      }).join("")}
    </div>`;
  bindBoard(eps);
}

function deadline(e, today) {
  const d = Math.round((new Date(e.date + "T00:00:00") - new Date(today + "T00:00:00")) / 86400000);
  if (d < 0) return { text: `опаздывает на ${-d} дн.`, cls: "bad" };
  if (d === 0) return { text: "выход сегодня", cls: e.status === "ready" ? "good" : "bad" };
  if (d === 1) return { text: "выход завтра", cls: e.status === "ready" ? "" : "risk" };
  return { text: `через ${d} дн.`, cls: d <= 2 && e.status !== "ready" ? "risk" : "" };
}

function card(e, today) {
  const dl = deadline(e, today);
  const arc = e.arc_id ? `${esc(e.arc_title)} · серия ${e.arc_number}` : "Вне арки";
  const movable = canMove() && !e.locked;
  const body = `<div class="row"><span class="muted small">${fmtDate(e.date)}${e.pinned ? " 📌" : ""}</span><div class="spacer"></div>
      <span class="sm-flag ${dl.cls}">${dl.text}</span></div>
    <b>${esc(e.title || "Без названия")}</b>
    <span class="arc-label">${arc}</span>
    ${e.locked ? `<span class="muted small">Ждёт ТЗ от сценариста</span>`
      : `<span class="muted small">${e.shots ? `${e.shots} шотов · видео ${e.with_take}/${e.shots}` : "шотов пока нет"}${e.open_fixes ? ` · <span class="warn-t">правок: ${e.open_fixes}</span>` : ""}${e.redo ? ` · на перегенерацию: ${e.redo}` : ""}</span>`}
    ${movable && e.status === "ready" ? `<div class="row prod-actions"><button class="ghost small" data-post-text="${e.id}" title="${e.post_text ? "Скопировать описание для поста" : "Сценарист ещё не написал описание"}">📋 Текст поста</button>
      <button class="small prod-posted-btn" data-posted="${e.id}">Выложено ✓</button></div>` : ""}`;
  return e.locked
    ? `<div class="prod-card locked" style="--c:${STATUS_COLORS[e.status]}">${body}</div>`
    : `<a class="prod-card" href="#/episodes/${e.id}/shots" data-card="${e.id}" data-status="${e.status}" draggable="${movable}" style="--c:${STATUS_COLORS[e.status]}">${body}</a>`;
}

async function setStatus(id, status) {
  try {
    await api(`/api/episodes/${id}/status`, { json: { status } });
    return true;
  } catch { return false; } // причину уже показал api() во всплывашке
}

function bindBoard(eps) {
  $$("[data-post-text]").forEach((b) => (b.onclick = (ev) => {
    ev.preventDefault();
    copyText(eps.find((e) => e.id === +b.dataset.postText)?.post_text || "");
  }));
  $$("[data-posted]").forEach((b) => (b.onclick = async (ev) => {
    ev.preventDefault();
    if (await setStatus(b.dataset.posted, "posted")) { toast("Серия выложена и ушла с доски"); renderProduction(); }
  }));
  if (!canMove()) return;
  let drag = null;
  $$(".prod-card[draggable='true']").forEach((c) => {
    c.addEventListener("dragstart", (ev) => { drag = c; c.classList.add("dragging"); ev.dataTransfer.effectAllowed = "move"; });
    c.addEventListener("dragend", () => { c.classList.remove("dragging"); $$(".prod-col.drop-over").forEach((x) => x.classList.remove("drop-over")); });
  });
  $$(".prod-col[data-drop]").forEach((col) => {
    if (!col.dataset.drop) return;
    col.addEventListener("dragover", (ev) => { if (drag && drag.dataset.status !== col.dataset.drop) { ev.preventDefault(); col.classList.add("drop-over"); } });
    col.addEventListener("dragleave", (ev) => { if (!col.contains(ev.relatedTarget)) col.classList.remove("drop-over"); });
    col.addEventListener("drop", async (ev) => {
      ev.preventDefault();
      col.classList.remove("drop-over");
      if (!drag) return;
      const id = drag.dataset.card;
      drag = null;
      if (await setStatus(id, col.dataset.drop)) renderProduction();
    });
  });
}
