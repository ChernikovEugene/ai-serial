// Удаление серий и «Корзина». Удалённая серия 30 дней лежит в корзине: её можно вернуть (она вернётся в черновики
// своей арки), потом она удаляется совсем. Удалять могут те, у кого есть право «серии» (сценарист, продюсер).
import { $, $$, api, closeModal, esc, fmtDate, fmtDateTime, goBack, modal, statusPill, toast, view } from "./core.js";

/** Окно «Удалить серию». У серии в очереди спрашиваем, сдвинуть ли следующие на день назад. */
export function deleteEpisode(ep, onDone) {
  const queued = ep.number != null;
  const m = modal(`<h1>Удалить серию?</h1>
    <p>«${esc(ep.title || "Без названия")}» уйдёт в корзину. 30 дней её можно вернуть на странице «Серии» → «Корзина».</p>
    ${queued ? `<label class="check"><input type="radio" name="del-shift" value="1" checked> Сдвинуть следующие серии на день назад
        <span class="muted small">— чтобы ${fmtDate(ep.date)} не остался пустым</span></label>
      <label class="check"><input type="radio" name="del-shift" value="0"> Оставить день пустым</label>` : ""}
    <div class="row" style="margin-top:16px"><div class="spacer"></div>
      <button class="ghost" id="del-cancel">Отмена</button><button class="danger" id="del-go">Удалить</button></div>`);
  $("#del-cancel", m).onclick = closeModal;
  $("#del-go", m).onclick = async () => {
    const shift = queued && $("input[name=del-shift]:checked", m).value === "1";
    await api(`/api/episodes/${ep.id}`, { method: "DELETE", json: { shift } });
    closeModal();
    toast("Серия в корзине. Вернуть: «Серии» → «Корзина»");
    onDone?.();
  };
}

/** Несколько серий сразу (галочки у черновиков). */
export async function deleteMany(ids, onDone) {
  if (!ids.length) return;
  if (!confirm(`Удалить выбранные серии (${ids.length})? Они уйдут в корзину, 30 дней их можно вернуть.`)) return;
  await api("/api/episodes/bulk-delete", { json: { ids } });
  toast(`В корзине: ${ids.length}. Вернуть: «Серии» → «Корзина»`);
  onDone?.();
}

export async function renderTrash() {
  const { episodes, days } = await api("/api/trash");
  view().innerHTML = `
    <div class="row"><button class="ghost" id="trash-back" title="Назад">←</button><h1 style="margin:0">Корзина</h1></div>
    <p class="muted small">Удалённые серии лежат здесь ${days} дней, потом удаляются совсем вместе с шотами и видео.
      Вернуть — серия встанет в черновики своей арки, дату нужно будет назначить заново.</p>
    ${episodes.length ? `<div class="ep-list queue-list">${episodes.map((e) => `
      <div class="ep-card queue-row trash-row" style="--c:var(--line)">
        <div class="q-when"><span class="muted small">удалена ${fmtDateTime(e.deleted_at)}</span></div>
        <div>${e.arc_title ? `<span class="arc-chip">${esc(e.arc_title)}</span>` : `<span class="arc-chip none">Вне арки</span>`}</div>
        <div class="q-text"><b>${esc(e.title || "Без названия")}</b>
          <span class="muted small">${esc((e.synopsis || "").replace(/\s+/g, " ").slice(0, 140)) || "синопсиса нет"}${e.shots ? ` · шотов: ${e.shots}` : ""} · удалится совсем ${fmtDate(e.purge_at)}</span></div>
        <div class="row">${statusPill(e.status, true)}
          <button class="small" data-restore="${e.id}">↩ Вернуть</button>
          <button class="ghost small danger" data-purge="${e.id}" title="Удалить совсем, не дожидаясь ${days} дней">Удалить совсем</button></div>
      </div>`).join("")}</div>` : `<p class="muted">Корзина пуста.</p>`}`;
  $("#trash-back").onclick = () => goBack("#/series");
  $$("[data-restore]").forEach((b) => (b.onclick = async () => {
    await api(`/api/trash/${b.dataset.restore}/restore`, { json: {} });
    toast("Серия вернулась в черновики");
    renderTrash();
  }));
  $$("[data-purge]").forEach((b) => (b.onclick = async () => {
    if (!confirm("Удалить серию совсем? Сценарий, шоты и видео пропадут, вернуть будет нельзя.")) return;
    await api(`/api/trash/${b.dataset.purge}`, { method: "DELETE" });
    toast("Серия удалена совсем");
    renderTrash();
  }));
}
