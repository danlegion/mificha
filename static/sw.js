/* MiFicha service worker.
   - /static/* : cache-first (los assets llevan ?v= en el nombre).
   - / : network-first (el home es personal y cambia con cada acción:
     borrar, subir, guardar). Solo se usa la caché sin conexión.
   - NEVER cache /resultado, /ficha, /s/* (clinical data must always be fresh). */
var CACHE = "mificha-shell-v31";
var SHELL = ["/static/app.css?v=26", "/static/app.js?v=26", "/static/manifest.json", "/static/brand-mark.svg"];

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

  /* Home: red primero, caché solo como respaldo sin conexión. */
  if (url.pathname === "/") {
    event.respondWith(
      fetch(event.request).then(function (resp) {
        var copy = resp.clone();
        caches.open(CACHE).then(function (cache) { cache.put(event.request, copy); });
        return resp;
      }).catch(function () {
        return caches.match(event.request);
      })
    );
    return;
  }

  if (url.pathname.indexOf("/static/") !== 0) return;

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
