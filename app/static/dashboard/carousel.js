// The dashboard pages: one slide each, swipeable (CSS scroll-snap), auto-advancing every 10 s.
// Any touch, wheel, key or dot click restarts the 10 s wait so a manual pick stays put for a while.
// An open popup holds it still.
import { $ } from "./core.js";

const ROTATE_MS = 10000;
const box = $("slides"), dotsEl = $("dots");
let timer = null, held = false, idx = 0;
const slides = () => [...box.children].filter((s) => !s.hidden);
const current = () => Math.round(box.scrollLeft / (box.clientWidth || 1));
const popupOpen = () => !!document.querySelector("dialog[open]");

function go(i, smooth = true) {
  const list = slides();
  if (!list.length) return;
  i = (i + list.length) % list.length;
  box.scrollTo({ left: i * box.clientWidth, behavior: smooth ? "smooth" : "auto" });
  mark(i);
}
function mark(i) {
  idx = i;
  [...dotsEl.children].forEach((d, k) => d.setAttribute("aria-selected", String(k === i)));
}
function restart() {
  clearTimeout(timer);
  if (held || document.hidden || popupOpen() || slides().length < 2) return;
  timer = setTimeout(() => { go(idx + 1); restart(); }, ROTATE_MS);
}
// after a page is shown or hidden
function refresh() {
  const list = slides();
  dotsEl.hidden = list.length < 2;
  dotsEl.replaceChildren(...list.map((s, k) => {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "dot";
    b.setAttribute("role", "tab");
    b.setAttribute("aria-label", s.getAttribute("aria-label"));
    b.onclick = () => { go(k); restart(); };
    return b;
  }));
  go(Math.min(idx, list.length - 1), false);
  badges();
  restart();
}
// a yellow dot for pages with something to look at
function badges() {
  slides().forEach((s, k) => dotsEl.children[k]?.classList.toggle("alert", !!s.dataset.alert));
}
// show or hide a page; true when that changed anything
function show(id, on) {
  const s = $(id);
  if (s.hidden !== on) return false;
  s.hidden = !on;
  refresh();
  return true;
}

let scrollEnd;
box.addEventListener("scroll", () => {
  clearTimeout(scrollEnd);
  scrollEnd = setTimeout(() => mark(current()), 80);
}, { passive: true });
box.addEventListener("pointerdown", () => { held = true; clearTimeout(timer); });
window.addEventListener("pointerup", () => { if (held) { held = false; restart(); } });
window.addEventListener("pointercancel", () => { if (held) { held = false; restart(); } });
box.addEventListener("wheel", restart, { passive: true });
document.addEventListener("keydown", (e) => {
  if (e.target.closest("input, textarea, select") || !$("drawer").hidden || popupOpen()) return;
  if (e.key === "ArrowRight") { go(idx + 1); restart(); }
  else if (e.key === "ArrowLeft") { go(idx - 1); restart(); }
});
document.addEventListener("visibilitychange", restart);
window.addEventListener("resize", () => go(idx, false));

export const carousel = { refresh, badges, restart, show };
