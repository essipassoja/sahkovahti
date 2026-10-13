// Slide-in menu shared by the simple and detailed views.
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
