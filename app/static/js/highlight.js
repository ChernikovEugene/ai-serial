// Подсветка героев и локаций цветом прямо в поле шота: за прозрачным полем лежит копия текста с цветными плашками.
import { assetColor, esc, state } from "./core.js";

const stem = (w) => { w = w.toLowerCase(); return w.length >= 4 && "аяоеиыуюьй".includes(w.at(-1)) ? w.slice(0, -1) : w; };
const rxEsc = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

/** Находит в тексте имена из библиотеки (с падежами) и @-пометки. Возвращает [{from, to, asset}] без пересечений. */
export function findMentions(text) {
  const hits = [];
  for (const a of state.assets) {
    for (const name of [a.name, ...(a.aliases || [])]) {
      const n = (name || "").trim();
      if (!n) continue;
      const body = n.split(/\s+/).map((t) => rxEsc(stem(t)) + "[а-яёa-z]{0,4}").join("\\s+");
      const rx = new RegExp(`(@?)(?<![\\p{L}\\p{N}_])${body}(?:[:][\\p{L}\\p{N}_\\-]+)?(?![\\p{L}\\p{N}_])`, "giu");
      for (const m of text.matchAll(rx)) hits.push({ from: m.index, to: m.index + m[0].length, asset: a });
    }
  }
  hits.sort((x, y) => x.from - y.from || y.to - x.to);
  const out = [];
  for (const h of hits) if (!out.length || h.from >= out.at(-1).to) out.push(h);
  return out;
}

function backHtml(text) {
  let html = "", pos = 0;
  for (const h of findMentions(text)) {
    const c = assetColor(h.asset);
    html += esc(text.slice(pos, h.from)) + `<mark style="--c:${c}">${esc(text.slice(h.from, h.to))}</mark>`;
    pos = h.to;
  }
  return html + esc(text.slice(pos)) + "\n";
}

/** Оборачивает поле и рисует подсветку под ним. Вызывать до attachMentions. */
export function attachHighlight(el) {
  const wrap = document.createElement("div");
  wrap.className = "hl-wrap" + (el.tagName === "INPUT" ? " single" : "");
  el.replaceWith(wrap);
  const back = document.createElement("div");
  back.className = "hl-back";
  back.setAttribute("aria-hidden", "true");
  wrap.append(back, el);
  const sync = () => { back.innerHTML = backHtml(el.value); back.scrollTop = el.scrollTop; back.scrollLeft = el.scrollLeft; };
  el.addEventListener("input", sync);
  el.addEventListener("scroll", sync);
  new ResizeObserver(sync).observe(el);
  sync();
}
