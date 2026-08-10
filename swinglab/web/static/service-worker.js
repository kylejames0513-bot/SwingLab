/* CaddieInsight's deliberately small offline shell.
 *
 * Personal reports, sessions, accounts, and video uploads are never placed
 * in Cache Storage.  A missed network request gets a helpful offline page;
 * it does not pretend that an upload or current coaching data is available.
 *
 * The cacheable surface is an ALLOWLIST, not a denylist: only the offline
 * page and files under /static/ may ever be stored, so no future route can
 * accidentally become cacheable by omission.  Every response is additionally
 * checked for a private/no-store Cache-Control before it is kept.
 */
/* v5: the precache carries the caddieinsight-* lockups. v4 precached the
 * retired swinglab-* filenames, so the offline shell — the one surface
 * that paints entirely from this cache — showed the v3 mark long after
 * every online page had moved on. Bumping the name is what evicts the old
 * cache on activate. */
const CACHE_NAME = "caddieinsight-public-shell-v5";

/* The offline page plus the chrome an installed app paints before it has a
 * network answer.  Kept short on purpose — a long precache list makes an
 * install fail atomically on one bad entry. */
const PRECACHE = [
  "/offline",
  "/static/pwa-icon.svg",
  "/static/caddieinsight-logo.png",
  "/static/caddieinsight-logo-inverse.png",
];

function isCacheable(pathname) {
  return pathname === "/offline" || pathname.startsWith("/static/");
}

function mayStore(response) {
  const cacheControl = response.headers.get("Cache-Control") || "";
  return response.ok && !/(?:private|no-store)/i.test(cacheControl);
}

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(CACHE_NAME)
      // addAll is atomic; a single 404 would throw away the whole install,
      // so each entry is added independently and failures are tolerated.
      .then((cache) =>
        Promise.all(
          PRECACHE.map((url) =>
            cache.add(new Request(url, { cache: "reload" })).catch(() => {})
          )
        )
      )
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(
          keys.filter((key) => key !== CACHE_NAME).map((key) => caches.delete(key))
        )
      )
      .then(() => self.clients.claim())
  );
});

/* Stale-while-revalidate for the shell: paint instantly from cache, then
 * quietly refresh so a redeployed logo or icon lands on the next visit. */
function staleWhileRevalidate(request) {
  return caches.open(CACHE_NAME).then((cache) =>
    cache.match(request).then((cached) => {
      const network = fetch(request)
        .then((response) => {
          if (mayStore(response)) cache.put(request, response.clone());
          return response;
        })
        .catch(() => cached);
      return cached || network;
    })
  );
}

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") return;

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;

  if (isCacheable(url.pathname)) {
    event.respondWith(staleWhileRevalidate(request));
    return;
  }

  /* Everything else is network-only. A failed navigation falls back to the
   * offline page; a failed sub-resource simply fails, because a stale
   * personal answer is worse than none. */
  if (request.mode === "navigate") {
    event.respondWith(fetch(request).catch(() => caches.match("/offline")));
  }
});
