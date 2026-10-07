// Story arcs: a group of episodes (episode.arc_id) with its own synopsis and members; inside an arc episodes are
// counted again from 1. Shown as a divider on «Статус» and as a banner on its first day in the calendar.
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

/** Create (no `arc`) or edit an arc. `onChange(arc)` gets the saved arc (or nothing after a delete). */
export async function arcDialog({ arc = null, onChange, draft = null } = {}) {
  await loadAssets();
  // draft — то, что уже введено в окне, когда оно перерисовывается после создания персонажа или локации
  const chosen = draft?.chosen || Object.fromEntries((arc?.members || []).map((m) => [m.asset_id, m.version_id ?? ""]));
  const pick = (kind, title) => {
    const list = state.assets.filter((a) => a.kind === kind);
    const add = `<div class="row arc-new" data-new-kind="${kind}" style="margin-top:6px">
        <button class="ghost small" data-new-open>${kind === "character" ? "+ Новый персонаж" : "+ Новая локация"}</button>
        <span hidden data-new-form class="row" style="flex:1"><input data-new-name placeholder="Название" style="flex:1">
          <input data-new-desc placeholder="Описание для Veo (можно потом)" style="flex:2">
          <button class="primary small" data-new-save>Создать</button></span></div>`;
    if (!list.length) return `<label>${title}</label>${add}`;
    return `<label>${title}</label><div class="arc-pick">${list.map((a) => `<div class="row">
        <span style="flex:1">${esc(a.name)}</span>
        <select data-member="${a.id}" style="width:auto">
          <option value="none">не участвует</option>
          <option value="" ${a.id in chosen && chosen[a.id] === "" ? "selected" : ""}>участвует (активная версия)</option>
          ${a.versions.map((v) => `<option value="${v.id}" ${chosen[a.id] === v.id ? "selected" : ""}>v${v.version_no} ${esc(v.label)}</option>`).join("")}
        </select>
        <button class="ghost small danger" data-del-asset="${a.id}" title="Удалить из библиотеки" aria-label="Удалить ${esc(a.name)} из библиотеки">✕</button></div>`).join("")}</div>${add}`;
  };
  const m = modal(`<h1>${arc ? "Арка" : "Новая арка"}</h1>
    <label>Название арки</label><input id="arc-title" value="${esc(draft?.title ?? arc?.title ?? "")}" placeholder="Например: Первый косплей">
    <label>Синопсис арки и ключевые детали: что меняется в персонаже, локации, сюжете</label>
    <textarea id="arc-notes" rows="5" placeholder="Ксю шьёт костюм Кристал Мейден, в комнате появляется манекен; в конце — пабстомп Пари">${esc(draft?.notes ?? arc?.notes ?? "")}</textarea>
    ${pick("character", "Персонажи в арке")}
    ${pick("location", "Локации в арке")}
    <div class="row" style="margin-top:14px">
      ${arc ? `<button class="ghost danger" id="arc-del">Удалить арку</button>` : ""}
      <div class="spacer"></div><button class="ghost" id="arc-cancel">Отмена</button><button class="primary" id="arc-save">Сохранить</button></div>`, true);
  $("#arc-cancel", m).onclick = closeModal;
  const membersOf = () => $$("[data-member]", m).filter((s) => s.value !== "none")
    .map((s) => ({ asset_id: +s.dataset.member, version_id: s.value ? +s.value : null }));
  // «+ Новый персонаж / локация»: создаём прямо здесь, он сразу попадает в библиотеку и отмечается участником арки
  $$("[data-new-kind]", m).forEach((box) => {
    $("[data-new-open]", box).onclick = () => { $("[data-new-form]", box).hidden = false; $("[data-new-open]", box).hidden = true; $("[data-new-name]", box).focus(); };
    $("[data-new-save]", box).onclick = async () => {
      const name = $("[data-new-name]", box).value.trim();
      if (!name) return toast("Укажите название", true);
      const fd = new FormData();
      fd.append("kind", box.dataset.newKind); fd.append("name", name); fd.append("description", $("[data-new-desc]", box).value);
      const created = await api("/api/assets", { method: "POST", body: fd });
      const picked = Object.fromEntries(membersOf().map((x) => [x.asset_id, x.version_id ?? ""]));
      picked[created.id] = "";
      closeModal();
      toast(`«${name}» добавлен в библиотеку`);
      arcDialog({ arc, onChange, draft: { title: $("#arc-title", m).value, notes: $("#arc-notes", m).value, chosen: picked } });
    };
  });
  // «✕» у персонажа или локации: удаляем из библиотеки совсем (с подтверждением), окно перерисовывается с введённым
  $$("[data-del-asset]", m).forEach((b) => (b.onclick = async () => {
    const a = state.assets.find((x) => x.id === +b.dataset.delAsset);
    if (!a || !confirm(`Удалить «${a.name}» из библиотеки? Исчезнут все его версии и фото, он пропадёт из арок и серий. Это нельзя отменить.`)) return;
    const picked = Object.fromEntries(membersOf().filter((x) => x.asset_id !== a.id).map((x) => [x.asset_id, x.version_id ?? ""]));
    await api(`/api/assets/${a.id}`, { method: "DELETE" });
    const draft = { title: $("#arc-title", m).value, notes: $("#arc-notes", m).value, chosen: picked };
    closeModal();
    toast(`«${a.name}» удалён из библиотеки`);
    arcDialog({ arc, onChange, draft });
  }));
  $("#arc-save", m).onclick = async () => {
    const members = membersOf();
    const body = { title: $("#arc-title", m).value, notes: $("#arc-notes", m).value, members };
    const saved = await api(arc ? `/api/arcs/${arc.id}` : "/api/arcs", { method: arc ? "PUT" : "POST", json: body });
    closeModal();
    toast(arc ? "Арка сохранена" : "Арка создана");
    onChange?.(saved);
  };
  if (arc) $("#arc-del", m).onclick = async () => {
    if (!confirm(`Удалить арку «${arc.title}»? Серии не удалятся: они останутся на своих днях в разделе «Без арки».`)) return;
    await api(`/api/arcs/${arc.id}`, { method: "DELETE" });
    closeModal();
    onChange?.();
  };
  setTimeout(() => $("#arc-title", m).focus(), 50);
}
