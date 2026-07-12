// OmniFlow service worker — Phase 6 (installable app shell + web push),
// extended July 2026 with the Native-Feel UI Brief's cache-versioning
// protocol (Section 6).
//
// This is a server-rendered app, not an SPA: offline caching covers the
// static shell only (fonts/css/js/icons), never page content or manifest.json.
//
// ── Section 6.1: bump this version suffix (v1 -> v2 -> v3 ...) any time you
// change the contents of a file listed in SHELL_ASSETS below (CSS, JS, or
// icons). This is the ONLY manual step ever required to ship an update — do
// it in the same commit as the asset change. On 'activate' below, every
// cache that doesn't match this exact name is deleted, so stale versions
// never persist on a user's device.
const SHELL_CACHE = "omniflow-shell-v9";
const SHELL_ASSETS = [
  "/static/icon-192.png",
  "/static/icon-512.png",
  "/static/icon-512-maskable.png",
  "/static/css/theme-tokens.css",
  "/static/css/app-shell.css",
  "/static/js/app-shell.js",
];

// Static asset types that are safe to cache-first (Section 6.2). Everything
// else falls through to the network-first handler below, so a path we
// forgot to classify fails safe (hits network) instead of failing stale.
const CACHEABLE_STATIC_RE = /\.(?:css|js|png|jpe?g|svg|webp|ico|woff2?)$/i;

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(SHELL_CACHE).then((cache) => cache.addAll(SHELL_ASSETS))
  );
  // Section 6.3: take control immediately instead of waiting for tabs to
  // close, so a new deploy reaches every open tab on its next navigation.
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

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;

  const url = new URL(req.url);

  // manifest.json: never intercept — browsers fetch it independently (6.2).
  if (url.pathname === "/static/manifest.json") return;

  // CSS / JS / icons under /static/: cache-first, versioned by SHELL_CACHE.
  if (url.pathname.startsWith("/static/") && CACHEABLE_STATIC_RE.test(url.pathname)) {
    event.respondWith(
      caches.match(req).then((cached) => {
        if (cached) return cached;
        return fetch(req).then((res) => {
          if (res.ok) {
            const copy = res.clone();
            caches.open(SHELL_CACHE).then((cache) => cache.put(req, copy));
          }
          return res;
        });
      })
    );
    return;
  }

  // Everything else — Jinja2 pages, /api/*, uploads, login/auth — is
  // network-first per 6.2 (tickets, dashboards, FMS, checklists, and login
  // state must never be served stale). Navigations get an offline fallback;
  // everything else just fails through to the browser's normal network error.
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
