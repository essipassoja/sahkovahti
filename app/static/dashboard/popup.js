// One popup for the pages' details. Each page passes a view: { render() } and optionally
// { poll(), pollMs } to keep its data fresh while open. The page keeps the data; render() draws it.
import { $, el } from "./core.js";
import { carousel } from "./carousel.js";

const dlg = $("pop");
let view = null, timer = null;

export function open(v) {
  view = v;
  dlg.append($("tip"));         // the dialog sits above the page, so chart tooltips have to live inside it
  if (!dlg.open) dlg.showModal();
  carousel.restart();           // holds the pages still while the popup is open
  clearInterval(timer);
  dlg.scrollTop = 0;
  v.render();
  if (v.poll) {
    v.poll();
    timer = setInterval(() => { if (!document.hidden) v.poll(); }, v.pollMs || 60000);
  }
}

// redraw when new data comes in, if that view is the one showing
export const refresh = (v) => { if (view === v) v.render(); };

export function head(title, sub = "") {
  $("popTitle").textContent = title;
  $("popSub").textContent = sub;
}
export const body = (...nodes) => $("popBody").replaceChildren(...nodes);
export const note = (text) => body(el("p", "pop-note", text));

// a tile that opens a popup, by click, tap, Enter or Space
export function opens(node, fn) {
  node.setAttribute("role", "button");
  node.setAttribute("tabindex", "0");
  node.setAttribute("aria-haspopup", "dialog");
  node.addEventListener("click", fn);
  node.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); fn(); }
  });
}

dlg.addEventListener("close", () => {
  view = null;
  clearInterval(timer);
  Charts.hideTip();
  document.body.append($("tip"));
  $("popBody").replaceChildren();
  carousel.restart();
});
dlg.addEventListener("click", (e) => { if (e.target === dlg) dlg.close(); });   // a click on the backdrop
$("popClose").onclick = () => dlg.close();

let rt;
window.addEventListener("resize", () => {
  clearTimeout(rt);
  rt = setTimeout(() => view?.render(), 150);
});
