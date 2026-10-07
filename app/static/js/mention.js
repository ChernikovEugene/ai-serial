// "@" autocomplete for characters and locations (and their versions) in a textarea or a text input.
import { esc, media, state } from "./core.js";

const token = (s) => s.trim().replace(/\s+/g, "_");

function caretCoords(ta) {
  const single = ta.tagName === "INPUT"; // однострочное поле: каретка не переносится, список — под полем
  const div = document.createElement("div");
  const cs = getComputedStyle(ta);
  for (const p of ["fontFamily", "fontSize", "fontWeight", "lineHeight", "letterSpacing", "paddingTop", "paddingLeft",
    "paddingRight", "paddingBottom", "borderTopWidth", "borderLeftWidth", "boxSizing", "whiteSpace", "wordWrap", "tabSize"]) {
    div.style[p] = cs[p];
  }
  Object.assign(div.style, { position: "absolute", visibility: "hidden", whiteSpace: single ? "pre" : "pre-wrap", wordWrap: "break-word",
    width: single ? "auto" : ta.clientWidth + "px", overflow: "hidden" });
  div.textContent = ta.value.slice(0, ta.selectionStart);
  const span = document.createElement("span");
  span.textContent = "​";
  div.append(span);
  document.body.append(div);
  const top = single ? ta.offsetHeight : span.offsetTop - ta.scrollTop + parseFloat(cs.lineHeight || 18);
  const left = Math.min(span.offsetLeft - (single ? ta.scrollLeft : 0), ta.clientWidth - 260);
  div.remove();
  return { top, left };
}

function items() {
  const out = [];
  for (const a of state.assets) {
    const active = a.versions.find((v) => v.id === a.active_version_id) || a.versions.at(-1);
    out.push({ text: `@${token(a.name)}`, asset: a, version: active, sub: "активная версия" });
    if (a.versions.length > 1) {
      for (const v of a.versions) {
        out.push({ text: `@${token(a.name)}:${token(v.label || "v" + v.version_no)}`, asset: a, version: v, sub: `версия v${v.version_no}` });
      }
    }
  }
  return out;
}

export function attachMentions(ta, onPick) {
  const box = document.createElement("div");
  box.className = "mention-box";
  box.hidden = true;
  ta.parentElement.style.position = "relative";
  ta.parentElement.append(box);
  let list = [], sel = 0, start = -1;

  const close = () => { box.hidden = true; start = -1; };
  const render = () => {
    box.innerHTML = list.map((it, i) => {
      const img = it.version?.images?.[0];
      return `<div class="mention-item ${i === sel ? "sel" : ""}" data-i="${i}">
        <span class="mi-thumb" style="${img ? `background-image:url('${media(img)}')` : ""}">${img ? "" : it.asset.kind === "character" ? "👤" : "🏙️"}</span>
        <span><b>${esc(it.text)}</b><br><span class="muted small">${it.asset.kind === "character" ? "персонаж" : "локация"} · ${esc(it.sub)}</span></span></div>`;
    }).join("") || `<div class="muted small" style="padding:8px">Нет совпадений. Создайте персонажа или локацию в меню слева.</div>`;
  };
  const pick = (i) => {
    const it = list[i];
    if (!it) return;
    const before = ta.value.slice(0, start), after = ta.value.slice(ta.selectionStart);
    ta.value = before + it.text + " " + after;
    const pos = (before + it.text + " ").length;
    ta.setSelectionRange(pos, pos);
    close();
    ta.focus();
    ta.dispatchEvent(new Event("input"));
    onPick && onPick(it);
  };

  ta.addEventListener("input", () => {
    const upto = ta.value.slice(0, ta.selectionStart);
    const m = upto.match(/(^|[\s(«"—–-])@([\p{L}\p{N}_\-:]*)$/u);
    if (!m) return close();
    start = ta.selectionStart - m[2].length - 1;
    const q = m[2].toLowerCase().replace(/_/g, " ");
    list = items().filter((it) => it.text.slice(1).toLowerCase().replace(/_/g, " ").includes(q)).slice(0, 12);
    sel = 0;
    const { top, left } = caretCoords(ta);
    Object.assign(box.style, { top: ta.offsetTop + top + "px", left: ta.offsetLeft + Math.max(0, left) + "px" });
    box.hidden = false;
    render();
    const r = box.getBoundingClientRect();
    if (r.bottom > window.innerHeight - 8) box.style.top = ta.offsetTop + top - parseFloat(getComputedStyle(ta).lineHeight || 18) - r.height - 4 + "px";
  });
  ta.addEventListener("keydown", (e) => {
    if (box.hidden) return;
    if (e.key === "ArrowDown") { sel = (sel + 1) % Math.max(1, list.length); render(); e.preventDefault(); }
    else if (e.key === "ArrowUp") { sel = (sel - 1 + list.length) % Math.max(1, list.length); render(); e.preventDefault(); }
    else if (e.key === "Enter" || e.key === "Tab") { if (list.length) { pick(sel); e.preventDefault(); } }
    else if (e.key === "Escape") { close(); e.preventDefault(); }
  });
  ta.addEventListener("blur", () => setTimeout(close, 150));
  box.addEventListener("mousedown", (e) => {
    const el = e.target.closest("[data-i]");
    if (el) { e.preventDefault(); pick(+el.dataset.i); }
  });
}
