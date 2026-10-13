// Service worker: makes the app installable and lets the pages open without a connection.
// Pages and static files: network first, cached copy when offline. API data is never cached,
// so numbers on screen are always live (pages show "App not reachable" when offline).
const CACHE = "sahkovahti-v1";
const SHELL = [
  "/", "/details", "/settings",
  "/static/style.css", "/static/nav.js", "/static/simple.js", "/static/app.js", "/static/charts.js",
  "/static/settings.js", "/static/icons/icon-192.png",
];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys()
    .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.origin !== location.origin || url.pathname.startsWith("/api/")) return;
  e.respondWith(
    fetch(e.request)
      .then((r) => {
        if (r.ok) { const copy = r.clone(); caches.open(CACHE).then((c) => c.put(e.request, copy)); }
        return r;
      })
      .catch(() => caches.match(e.request).then((r) => r || caches.match("/")))
  );
});
