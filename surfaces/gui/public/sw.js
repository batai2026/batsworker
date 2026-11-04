// Offline shell for the browser build. Scope is the app directory (`base: "./"`), so all
// URLs below stay relative and work under a subpath deployment.
//
// Deliberately narrow: only same-origin static assets are cached. The sidecar API
// (`/v1/*`, a different origin/port) and WebSocket traffic pass straight through — caching
// them would hand the app a stale launch token (see the boot path in src/hooks/useBoot.ts).
const CACHE = "openworker-shell-v1";
const SCOPE = self.registration.scope;
// Each release adds a fresh hashed bundle (~1.2 MB); cap the runtime cache so repeated
// updates cannot grow it unbounded. Cache.keys() preserves insertion order.
const MAX_ASSETS = 80;

// The shell itself + install metadata. Hashed bundles are NOT precached: they are unknown at
// authoring time and get cached on first use by the runtime handler below.
const SHELL_FILES = [
  "./",
  "./index.html",
  "./manifest.webmanifest",
  "./icons/icon.svg",
  "./icons/icon-192.png",
  "./icons/icon-512.png",
];

const shellUrl = (rel) => new URL(rel, SCOPE).href;

self.addEventListener("install", (event) => {
  event.waitUntil(
    (async () => {
      const cache = await caches.open(CACHE);
      // addAll() fails the whole install on one 404; add them individually so a missing
      // optional icon can never block offline support.
      await Promise.all(
        SHELL_FILES.map(async (rel) => {
          try {
            await cache.add(shellUrl(rel));
          } catch {
            /* non-fatal */
          }
        }),
      );
      await self.skipWaiting();
    })(),
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    (async () => {
      const keys = await caches.keys();
      await Promise.all(
        keys
          .filter((key) => key.startsWith("openworker-") && key !== CACHE)
          .map((key) => caches.delete(key)),
      );
      await self.clients.claim();
    })(),
  );
});

async function trimCache(cache) {
  const keys = await cache.keys();
  if (keys.length <= MAX_ASSETS) return;
  await Promise.all(keys.slice(0, keys.length - MAX_ASSETS).map((key) => cache.delete(key)));
}

async function networkFirstNavigation(request, event) {
  const cache = await caches.open(CACHE);
  try {
    const response = await fetch(request);
    // Keep one canonical shell copy for offline reloads; navigations carry ad-hoc query
    // strings (?surface=…), which would otherwise fragment the cache.
    if (response && response.ok) {
      event.waitUntil(
        cache
          .put(shellUrl("./index.html"), response.clone())
          .then(() => trimCache(cache))
          .catch(() => {}),
      );
    }
    return response;
  } catch (error) {
    const cached =
      (await cache.match(shellUrl("./index.html"))) ||
      (await cache.match(shellUrl("./")));
    if (cached) return cached;
    throw error;
  }
}

async function cacheFirst(request, event) {
  const cache = await caches.open(CACHE);
  const hit = await cache.match(request);
  if (hit) return hit;
  const response = await fetch(request);
  // Build assets are content-hashed, so a cache hit is always the right bytes; only store
  // full, same-origin responses (opaque/partial ones would poison the cache).
  if (response && response.ok && response.type === "basic") {
    event.waitUntil(
      cache
        .put(request, response.clone())
        .then(() => trimCache(cache))
        .catch(() => {}),
    );
  }
  return response;
}

self.addEventListener("fetch", (event) => {
  const { request } = event;
  if (request.method !== "GET") return;

  let url;
  try {
    url = new URL(request.url);
  } catch {
    return;
  }
  if (url.origin !== self.location.origin) return;
  if (url.pathname.includes("/v1/")) return;

  if (request.mode === "navigate") {
    event.respondWith(networkFirstNavigation(request, event));
    return;
  }

  // Static bundle output and the install surface; everything else (unknown same-origin
  // endpoints) stays a plain network request.
  if (url.pathname.includes("/assets/") || url.pathname.includes("/icons/")) {
    event.respondWith(cacheFirst(request, event));
  }
});
