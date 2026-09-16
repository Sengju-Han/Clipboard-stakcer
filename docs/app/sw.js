// Offline for the shell only.
//
// The app's own files are cached so it opens with no signal; the deck is not,
// because a stale deck cached behind the app's back is how a learner ends up
// reviewing yesterday's cards forever. The deck lives in IndexedDB, which is
// the right place for it, and is refetched only when asked for.

const CACHE = "lexis-2026-09-16.1";
// Everything the app can do without a network, which is nearly all of it. Each
// screen beyond the review loop is a module loaded on demand, and a module
// that has never been loaded is a module that is not in the cache — so on a
// train, the first tap on Watch used to do nothing at all and Progress opened
// blank. They are listed here so the first tap works wherever it happens.
const SHELL = [
  "./", "./index.html", "./style.css", "./app.js",
  "./store.js", "./review.js", "./vendor/ts-fsrs.mjs",
  "./stats.js",                                  // the charts
  "./watch.js", "./subs.js", "./lex.js", "./clip.js",  // a downloaded episode and its subtitles
  "./explain.js", "../explain-contract.json",    // what is already explained, after an Again
  "./talk.js",                                   // opens and says what it needs
  "./sync.js", "./account.js",                   // ditto
  "./manifest.webmanifest", "./icon.svg",
  // apkg.js, apkgout.js and their libraries stay out: the SQLite engine alone
  // is most of a megabyte, it is useless without the wasm beside it, and most
  // sessions never import or export a file.
];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then(async (cache) => {
    // One at a time rather than addAll, which is all or nothing: a single file
    // that 404s would leave the app with no offline story whatever instead of
    // one missing screen. The list is checked in the tests, so a path that has
    // gone stale is caught there rather than silently here.
    await Promise.all(SHELL.map((url) => cache.add(url).catch(() => {})));
  }).then(() => self.skipWaiting()));
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
