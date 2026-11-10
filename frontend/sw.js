// Service Worker：只缓存应用外壳（HTML/CSS/JS/图标），绝不缓存 API 响应和作业图片（含未成年人信息）。
const CACHE = "edu-eval-shell-v2";
const SHELL = ["/", "/index.html", "/css/app.css", "/js/app.js", "/js/api.js", "/js/ui.js", "/js/charts.js", "/js/demo_common.js",
  "/vendor/chart.umd.min.js", "/manifest.webmanifest", "/icons/icon-192.png"];

self.addEventListener("install", e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(SHELL)).then(() => self.skipWaiting()));
});
self.addEventListener("activate", e => {
  e.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k))))
    .then(() => self.clients.claim()));
});
self.addEventListener("fetch", e => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.origin !== location.origin || url.pathname.startsWith("/api/")) return;
  // 网络优先：保证拿到最新前端；离线时退回缓存的外壳
  e.respondWith(
    fetch(e.request).then(res => {
      if (res.ok && (SHELL.includes(url.pathname) || url.pathname.startsWith("/js/views/"))) {
        const copy = res.clone();
        caches.open(CACHE).then(c => c.put(e.request, copy));
      }
      return res;
    }).catch(() => caches.match(e.request).then(r => r || caches.match("/index.html")))
  );
});
