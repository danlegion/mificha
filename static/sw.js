/* MiFicha service worker — cache-first for the app shell only.
   NEVER cache /resultado, /ficha, /s/* (clinical data must always be fresh). */
var CACHE = "mificha-shell-v14";
var SHELL = ["/", "/static/app.css?v=14", "/static/app.js?v=14", "/static/manifest.json", "/static/brand-mark.svg"];

self.addEventListener("install", function (event) {
  event.waitUntil(
    caches.open(CACHE).then(function (cache) { return cache.addAll(SHELL); })
  );
  self.skipWaiting();
});

self.addEventListener("activate", function (event) {
  event.waitUntil(
    caches.keys().then(function (keys) {
      return Promise.all(
        keys.filter(function (k) { return k !== CACHE; }).map(function (k) {
          return caches.delete(k);
        })
      );
    })
  );
  self.clients.claim();
});

function isClinical(url) {
  return (
    url.pathname.indexOf("/resultado") === 0 ||
    url.pathname.indexOf("/ficha") === 0 ||
    url.pathname.indexOf("/s/") === 0
  );
}

self.addEventListener("fetch", function (event) {
  var url = new URL(event.request.url);
  if (event.request.method !== "GET" || url.origin !== self.location.origin) return;
  if (isClinical(url)) return; /* never cache: always go to network */

  var shellPath = url.pathname === "/" || url.pathname.indexOf("/static/") === 0;
  if (!shellPath) return;

  event.respondWith(
    caches.match(event.request).then(function (cached) {
      if (cached) return cached;
      return fetch(event.request).then(function (resp) {
        var copy = resp.clone();
        caches.open(CACHE).then(function (cache) { cache.put(event.request, copy); });
        return resp;
      });
    })
  );
});
