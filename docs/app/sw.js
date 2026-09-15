// Offline for the shell only.
//
// The app's own files are cached so it opens with no signal; the deck is not,
// because a stale deck cached behind the app's back is how a learner ends up
// reviewing yesterday's cards forever. The deck lives in IndexedDB, which is
// the right place for it, and is refetched only when asked for.

const CACHE = "lexis-2026-09-15.17";
const SHELL = [
  "./", "./index.html", "./style.css", "./app.js",
  "./store.js", "./review.js", "./vendor/ts-fsrs.mjs",
  // apkg.js and its libraries are fetched on demand, not pre-cached: the
  // SQLite engine alone is most of a megabyte and most sessions never import.
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

  // Network first, cache as the fallback.
  //
  // Cache first is the usual advice and it is wrong here. A stale app is the
  // failure mode this project has already lost hours to: a phone went on
  // serving a page from the previous day while the fix sat deployed, and
  // nothing on screen admitted it. A service worker makes that *more* likely,
  // because keeping what it already has is its whole purpose.
  //
  // The shell is a few tens of kilobytes, so asking the network first costs
  // milliseconds on a good connection and nothing at all on a bad one, where
  // the cache answers anyway. Being certainly current is worth more than being
  // marginally faster.
  event.respondWith(
    fetch(event.request).then((res) => {
      if (res.ok && res.type === "basic") {
        const copy = res.clone();
        caches.open(CACHE).then((c) => c.put(event.request, copy));
      }
      return res;
    }).catch(() => caches.match(event.request).then((hit) => hit || (() => {
      // Only a page request gets the page back. Handing index.html to a failed
      // script or JSON request answers "200 OK" with the wrong content type,
      // and the module error that follows names neither the missing file nor
      // the fact that the network was down.
      if (event.request.mode === "navigate") return caches.match("./index.html");
      return new Response("", { status: 504, statusText: "Offline, and not in the cache" });
    })()))
  );
});
