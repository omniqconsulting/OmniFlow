// OmniFlow service worker — Phase 6 (installable app shell + web push)
// This is a server-rendered app, not an SPA: offline caching covers the
// static shell only (fonts/css/manifest/icons), not page content.
const SHELL_CACHE = "omniflow-shell-v1";
const SHELL_ASSETS = [
  "/static/manifest.json",
  "/static/icon-192.png",
  "/static/icon-512.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(SHELL_CACHE).then((cache) => cache.addAll(SHELL_ASSETS))
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== SHELL_CACHE).map((k) => caches.delete(k)))
    )
  );
  self.clients.claim();
});

// Cache-first for static assets, network-first (with offline fallback) for
// navigations. Everything else (API/POST) just passes through to the network.
self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;

  const url = new URL(req.url);
  if (url.pathname.startsWith("/static/")) {
    event.respondWith(
      caches.match(req).then((cached) => cached || fetch(req))
    );
    return;
  }

  if (req.mode === "navigate") {
    event.respondWith(
      fetch(req).catch(() =>
        new Response(
          "<h1>You're offline</h1><p>OmniFlow needs a connection to load this page. Please reconnect and try again.</p>",
          { headers: { "Content-Type": "text/html" } }
        )
      )
    );
  }
});

// ── Web Push ──────────────────────────────────────────────────────────────
self.addEventListener("push", (event) => {
  let data = {};
  try {
    data = event.data ? event.data.json() : {};
  } catch (e) {
    data = { title: "OmniFlow", body: event.data ? event.data.text() : "" };
  }
  const title = data.title || "OmniFlow";
  const options = {
    body: data.body || "",
    icon: "/static/icon-192.png",
    badge: "/static/icon-192.png",
    data: { link: data.link || "/dashboard" },
  };
  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const link = (event.notification.data && event.notification.data.link) || "/dashboard";
  event.waitUntil(clients.openWindow(link));
});
