// Slide-in menu shared by all pages, plus the service worker that makes the app installable.
if ("serviceWorker" in navigator) {   // only on https:// or localhost; plain http on the LAN skips this
  navigator.serviceWorker.register("/sw.js").catch(() => {});
}
(function () {
  const btn = document.getElementById("menuBtn"), drawer = document.getElementById("drawer"),
    scrim = document.getElementById("scrim"), close = document.getElementById("drawerClose");
  if (!btn || !drawer) return;
  const set = (open) => {
    drawer.hidden = scrim.hidden = !open;
    btn.setAttribute("aria-expanded", String(open));
    if (open) drawer.querySelector("a").focus(); else btn.focus();
  };
  btn.onclick = () => set(true);
  close.onclick = scrim.onclick = () => set(false);
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !drawer.hidden) set(false); });
})();
