/* CaddieInsight's deliberately small offline shell.
 *
 * Personal reports, sessions, accounts, and video uploads are never placed
 * in Cache Storage.  A missed network request gets a helpful offline page;
 * it does not pretend that an upload or current coaching data is available.
 */
const CACHE_NAME = "caddieinsight-public-shell-v3";
const PUBLIC_SHELL = ["/offline"];

function canCachePublicShell(response) {
  const cacheControl = response.headers.get("Cache-Control") || "";
  return response.ok && !/(?:private|no-store)/i.test(cacheControl);
}

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(PUBLIC_SHELL))
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(
        keys
          .filter((key) => key !== CACHE_NAME)
          .map((key) => caches.delete(key))
      )
    )
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;
  if (PUBLIC_SHELL.includes(url.pathname)) {
    event.respondWith(
      fetch(request).then((response) => {
        if (canCachePublicShell(response)) {
          const copy = response.clone();
          caches.open(CACHE_NAME).then((cache) => cache.put(request, copy));
        }
        return response;
      }).catch(() => caches.match(request))
    );
    return;
  }
  if (request.mode === "navigate") {
    event.respondWith(fetch(request).catch(() => caches.match("/offline")));
  }
});
