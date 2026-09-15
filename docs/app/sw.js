// Offline for the shell only.
//
// The app's own files are cached so it opens with no signal; the deck is not,
// because a stale deck cached behind the app's back is how a learner ends up
// reviewing yesterday's cards forever. The deck lives in IndexedDB, which is
// the right place for it, and is refetched only when asked for.

const CACHE = "lexis-2026-09-15.1";
const SHELL = [
  "./", "./index.html", "./style.css", "./app.js",
  "./store.js", "./review.js", "./vendor/ts-fsrs.mjs",
  "./manifest.webmanifest", "./icon.svg",
];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (event.request.method !== "GET") return;
  // Never serve the deck or the media from here.
  if (url.pathname.includes("/deck/") || url.pathname.includes("/media/")) return;
  if (url.origin !== self.location.origin) return;

  event.respondWith(
    caches.match(event.request).then((hit) => hit || fetch(event.request).then((res) => {
      if (res.ok && res.type === "basic") {
        const copy = res.clone();
        caches.open(CACHE).then((c) => c.put(event.request, copy));
      }
      return res;
    }).catch(() => caches.match("./index.html")))
  );
});
