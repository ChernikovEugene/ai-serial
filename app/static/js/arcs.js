// Story arcs: an arc starts at a release slot; inside it episodes are counted again from 1.
// Shown as a divider in the queue and on «Статус», and as a banner on the start day in the calendar.
import { $, $$, api, can, closeModal, esc, fmtDate, loadAssets, modal, state, toast } from "./core.js";

/** Divider row before the first slot of an arc. `extraCls` lets each page fit it into its own layout. */
export function arcDivider(arc, extraCls = "") {
  const members = (arc.members || []).map(memberName).filter(Boolean);
  return `<div class="arc-div ${extraCls}" data-arc="${arc.id}" title="${can.edit() ? "Нажмите, чтобы изменить арку" : ""}">
    <div class="arc-div-head"><span class="arc-tag">Новая арка</span><b>${esc(arc.title)}</b>
      <span class="muted small">${arc.start_date ? `с ${fmtDate(arc.start_date)} · ` : ""}счёт серий начинается заново</span></div>
    ${arc.notes ? `<div class="arc-notes">${esc(arc.notes)}</div>` : ""}
    ${members.length ? `<div class="arc-members">${members.map((m) => `<span class="badge">${esc(m)}</span>`).join("")}</div>` : ""}
  </div>`;
}

function memberName(m) {
  const a = state.assets.find((x) => x.id === m.asset_id);
  if (!a) return "";
  const v = a.versions.find((x) => x.id === m.version_id);
  return `${a.kind === "location" ? "📍 " : ""}${a.name}${v ? ` · ${v.label}` : ""}`;
}

/** Make dividers clickable (edit) on the current page. */
export function bindArcDividers(onChange) {
  if (!can.edit()) return;
  $$("[data-arc]").forEach((el) => (el.onclick = async () => {
    const arcs = await api("/api/arcs");
    const arc = arcs.find((a) => a.id === +el.dataset.arc);
    if (arc) arcDialog({ arc, onChange });
  }));
}

/** Assets are needed to show arc members by name. */
export async function ensureAssets() {
  if (!state.assets.length) await loadAssets();
}

/** Create (no `arc`) or edit an arc. `date` pre-fills the start day. */
export async function arcDialog({ arc = null, date = "", onChange } = {}) {
  await loadAssets();
  const start = arc?.start_date || date || state.meta.today;
  const chosen = Object.fromEntries((arc?.members || []).map((m) => [m.asset_id, m.version_id ?? ""]));
  const pick = (kind, title) => {
    const list = state.assets.filter((a) => a.kind === kind);
    if (!list.length) return "";
    return `<label>${title}</label><div class="arc-pick">${list.map((a) => `<div class="row">
        <span style="flex:1">${esc(a.name)}</span>
        <select data-member="${a.id}" style="width:auto">
          <option value="none">не участвует</option>
          <option value="" ${a.id in chosen && chosen[a.id] === "" ? "selected" : ""}>участвует (активная версия)</option>
          ${a.versions.map((v) => `<option value="${v.id}" ${chosen[a.id] === v.id ? "selected" : ""}>v${v.version_no} ${esc(v.label)}</option>`).join("")}
        </select></div>`).join("")}</div>`;
  };
  const m = modal(`<h1>${arc ? "Арка" : "Новая арка"}</h1>
    <label>Название арки</label><input id="arc-title" value="${esc(arc?.title || "")}" placeholder="Например: Первый косплей">
    <label>Начинается с дня (с этой серии счёт серий начнётся заново)</label><input id="arc-date" type="date" value="${start}" style="width:180px">
    <label>Ключевые детали: что меняется в персонаже, локации, сюжете</label>
    <textarea id="arc-notes" rows="5" placeholder="Ксю шьёт костюм Кристал Мейден, в комнате появляется манекен; в конце — пабстомп Пари">${esc(arc?.notes || "")}</textarea>
    ${pick("character", "Персонажи в арке")}
    ${pick("location", "Локации в арке")}
    <div class="row" style="margin-top:14px">
      ${arc ? `<button class="ghost danger" id="arc-del">Удалить арку</button>` : ""}
      <div class="spacer"></div><button class="ghost" id="arc-cancel">Отмена</button><button class="primary" id="arc-save">Сохранить</button></div>`, true);
  $("#arc-cancel", m).onclick = closeModal;
  $("#arc-save", m).onclick = async () => {
    const members = $$("[data-member]", m).filter((s) => s.value !== "none")
      .map((s) => ({ asset_id: +s.dataset.member, version_id: s.value ? +s.value : null }));
    const body = { title: $("#arc-title", m).value, start_date: $("#arc-date", m).value, notes: $("#arc-notes", m).value, members };
    await api(arc ? `/api/arcs/${arc.id}` : "/api/arcs", { method: arc ? "PUT" : "POST", json: body });
    closeModal();
    toast(arc ? "Арка сохранена" : "Арка создана");
    onChange?.();
  };
  if (arc) $("#arc-del", m).onclick = async () => {
    if (!confirm(`Удалить арку «${arc.title}»? Серии останутся на своих днях, счёт серий снова станет сквозным.`)) return;
    await api(`/api/arcs/${arc.id}`, { method: "DELETE" });
    closeModal();
    onChange?.();
  };
  setTimeout(() => $("#arc-title", m).focus(), 50);
}
