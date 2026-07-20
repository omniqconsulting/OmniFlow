// OmniFlow service worker — Web Push only.
//
// The PWA "installed app shell" (offline asset caching, manifest.json,
// install prompts) was intentionally removed (native React Native app is
// replacing the PWA). This worker exists solely to receive Web Push events
// for browser tabs that opt in via the "Enable push notifications" button —
// it does not cache anything and does not intercept fetch/navigation.
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
