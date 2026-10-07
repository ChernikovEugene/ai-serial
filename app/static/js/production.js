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
  // Узкая зона: выложенная серия висит здесь несколько секунд и уходит в архив (статус «Выложено» везде)
  { key: "posted", name: "Выложено", hint: "уйдёт в архив", statuses: ["posted"], drop: "posted", narrow: true },
];
const FRESH_SECONDS = 4;
const fresh = new Map(); // id → когда серию выложили: пока не прошло FRESH_SECONDS, карточка остаётся в зоне «Выложено»
const canMove = () => can.gen(); // продюсер и монтажёр

export async function renderProduction() {
  const all = await api("/api/production");
  const eps = all.filter((e) => e.status !== "posted" || fresh.has(e.id));
  const today = state.meta.today;
  view().innerHTML = `
    <div class="row"><h1>Продакшн</h1><div class="spacer"></div>
      <span class="muted small">Все арки. В колонке сверху — самое срочное по дню выхода.${canMove() ? " Перетащите карточку в другую колонку, чтобы сменить этап." : ""}</span></div>
    <div class="prod-board">
      ${COLUMNS.map((col) => {
        const items = eps.filter((e) => col.statuses.includes(e.status)).sort((a, b) => a.number - b.number);
        return `<section class="prod-col ${col.narrow ? "narrow" : ""}" data-drop="${col.drop || ""}" style="--c:${STATUS_COLORS[col.statuses.at(-1)]}">
          <header><b>${col.name}</b><span class="muted small">${col.narrow ? "" : `${items.length}${col.hint ? " · " : ""}`}${col.hint || ""}</span></header>
          ${items.map((e) => card(e, today)).join("") || `<p class="muted small prod-empty">${col.narrow ? "Перетащите сюда выложенное" : "Пусто"}</p>`}
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
  if (e.status === "posted") {
    const gone = (Date.now() - fresh.get(e.id)) / 1000;
    return `<div class="prod-card just-posted" style="--c:${STATUS_COLORS.posted};animation-delay:-${Math.min(gone, FRESH_SECONDS).toFixed(2)}s">
      <b>${esc(e.title || "Без названия")}</b><span class="arc-label">${e.arc_id ? `${esc(e.arc_title)} · серия ${e.arc_number}` : "Вне арки"}</span>
      <span class="ok-t small">✓ Выложено, уходит в архив</span></div>`;
  }
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

/** Серия выложена: на несколько секунд остаётся в зоне «Выложено», потом исчезает с доски (в архив). */
async function markPosted(id) {
  if (!(await setStatus(id, "posted"))) return;
  id = +id;
  fresh.set(id, Date.now());
  setTimeout(() => { fresh.delete(id); if (location.hash === "#/production") renderProduction(); }, FRESH_SECONDS * 1000);
  renderProduction();
}

function bindBoard(eps) {
  $$("[data-post-text]").forEach((b) => (b.onclick = (ev) => {
    ev.preventDefault();
    copyText(eps.find((e) => e.id === +b.dataset.postText)?.post_text || "");
  }));
  $$("[data-posted]").forEach((b) => (b.onclick = async (ev) => {
    ev.preventDefault();
    markPosted(b.dataset.posted);
  }));
  if (!canMove()) return;
  let drag = null;
  $$(".prod-card[draggable='true']").forEach((c) => {
    c.addEventListener("dragstart", (ev) => { drag = c; c.classList.add("dragging"); ev.dataTransfer.effectAllowed = "move"; });
    c.addEventListener("dragend", () => { c.classList.remove("dragging"); $$(".prod-col.drop-over").forEach((x) => x.classList.remove("drop-over")); });
  });
  $$(".prod-col[data-drop]").forEach((col) => {
    if (!col.dataset.drop) return;
    // В «Выложено» можно переносить только то, что уже готово к постингу
    const accepts = () => drag && drag.dataset.status !== col.dataset.drop && (col.dataset.drop !== "posted" || drag.dataset.status === "ready");
    col.addEventListener("dragover", (ev) => { if (accepts()) { ev.preventDefault(); col.classList.add("drop-over"); } });
    col.addEventListener("dragleave", (ev) => { if (!col.contains(ev.relatedTarget)) col.classList.remove("drop-over"); });
    col.addEventListener("drop", async (ev) => {
      ev.preventDefault();
      col.classList.remove("drop-over");
      if (!drag || !(col.dataset.drop !== "posted" || drag.dataset.status === "ready")) return;
      const id = drag.dataset.card;
      drag = null;
      if (col.dataset.drop === "posted") return markPosted(id);
      if (await setStatus(id, col.dataset.drop)) renderProduction();
    });
  });
}
