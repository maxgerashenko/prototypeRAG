// Service worker for the voice app (/web/): caches the app shell only, so the home-screen app
// opens instantly and shows its own error banner offline. Never touches the API, the voice
// WebSocket, SSE (/voice/feed) or anything outside /web/ — those are not in this scope or are
// skipped below. Bump CACHE when this file's strategy changes.
const CACHE = "voice-shell-v1";
const SHELL = ["/web/", "/web/manifest.webmanifest", "/web/icons/icon-192.png", "/web/icons/apple-touch-icon.png"];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin || !url.pathname.startsWith("/web/")) return;
  if (url.pathname === "/web/dev-reload.js" || url.pathname === "/web/sw.js") return;

  // the page itself: network first (so a new build shows up), cached copy when offline
  if (req.mode === "navigate") {
    if (url.pathname !== "/web/") return; // debug pages (chat, voice-debug, call) stay uncached
    event.respondWith(
      fetch(req)
        .then((res) => {
          if (res.ok) {
            const copy = res.clone();
            caches.open(CACHE).then((c) => c.put("/web/", copy));
          }
          return res;
        })
        .catch(() => caches.match("/web/").then((r) => r || Response.error())),
    );
    return;
  }

  // hashed build output and icons never change under the same URL: cache first
  if (url.pathname.startsWith("/web/assets/") || url.pathname.startsWith("/web/icons/") || url.pathname === "/web/manifest.webmanifest") {
    event.respondWith(
      caches.match(req).then(
        (hit) =>
          hit ||
          fetch(req).then((res) => {
            if (res.ok) {
              const copy = res.clone();
              caches.open(CACHE).then((c) => c.put(req, copy));
            }
            return res;
          }),
      ),
    );
  }
});
