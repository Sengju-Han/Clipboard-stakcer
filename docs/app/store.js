// Everything the app knows, kept in this browser.
//
// A review app is opened every morning, often on a train, often with no signal,
// and it has to answer "what is due" instantly. That rules out asking a server.
// So the deck lives in IndexedDB and the app reads from it; the network is only
// ever used to fetch a newer deck, never to show the next card.
//
// Two stores. `cards` holds one row per card, scheduling state included, which
// is the working set the review loop reads and writes. `log` holds one row per
// answer, append-only and never rewritten, because it is the record that lets a
// schedule be rebuilt from scratch if the card state is ever lost or a better
// scheduler arrives later.

const DB_NAME = "lexis";
const DB_VERSION = 1;

let ready = null;

function open() {
  if (ready) return ready;
  ready = new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, DB_VERSION);
    req.onupgradeneeded = () => {
      const db = req.result;
      if (!db.objectStoreNames.contains("cards")) {
        const cards = db.createObjectStore("cards", { keyPath: "id" });
        // Answering "what is due" is the hot path, so it gets an index.
        cards.createIndex("due", "fsrs.due");
        cards.createIndex("deck", "deck");
        cards.createIndex("state", "fsrs.state");
      }
      if (!db.objectStoreNames.contains("log")) {
        db.createObjectStore("log", { keyPath: "at" });
      }
      if (!db.objectStoreNames.contains("meta")) {
        db.createObjectStore("meta", { keyPath: "key" });
      }
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
  return ready;
}

function run(storeNames, mode, body) {
  return open().then((db) => new Promise((resolve, reject) => {
    const tx = db.transaction(storeNames, mode);
    const out = body(...storeNames.map((n) => tx.objectStore(n)));
    tx.oncomplete = () => resolve(out && out.value !== undefined ? out.value : out);
    tx.onerror = () => reject(tx.error);
    tx.onabort = () => reject(tx.error);
  }));
}

function all(store) {
  return new Promise((resolve, reject) => {
    const req = store.getAll();
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

export async function cardCount() {
  const db = await open();
  return new Promise((resolve, reject) => {
    const req = db.transaction("cards").objectStore("cards").count();
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

export async function allCards() {
  const db = await open();
  return all(db.transaction("cards").objectStore("cards"));
}

// Importing replaces the deck's content but must not touch scheduling a learner
// has already earned in this app. A card already here keeps its own state; only
// its wording is refreshed. Anything else would reset progress on every import.
export async function importDeck(deck) {
  const existing = new Map((await allCards()).map((c) => [c.id, c]));
  const now = new Date().toISOString();
  let added = 0, refreshed = 0;

  await run(["cards", "meta"], "readwrite", (cards, meta) => {
    for (const card of deck.cards) {
      const had = existing.get(card.id);
      if (had) {
        cards.put({ ...card, fsrs: had.fsrs, reviewedHere: had.reviewedHere || 0 });
        refreshed += 1;
      } else {
        cards.put({ ...card, reviewedHere: 0 });
        added += 1;
      }
    }
    meta.put({
      key: "deck",
      builtAt: deck.built_at,
      importedAt: now,
      cardCount: deck.card_count,
      decks: deck.decks,
      scheduling: deck.scheduling,
    });
  });

  return { added, refreshed };
}

export async function meta(key) {
  const db = await open();
  return new Promise((resolve, reject) => {
    const req = db.transaction("meta").objectStore("meta").get(key);
    req.onsuccess = () => resolve(req.result || null);
    req.onerror = () => reject(req.error);
  });
}

export async function setMeta(key, value) {
  return run(["meta"], "readwrite", (meta) => meta.put({ key, ...value }));
}

// One answer, written before the card state is. If the tab dies between the two,
// the log is what survives, and a log entry without its card update can be
// replayed; a card update without its log entry cannot be explained.
export async function record(entry) {
  return run(["log"], "readwrite", (log) => log.put(entry));
}

// Every write stamps the card. Two devices reviewing the same word is the whole
// reason sync is hard, and without a per-card modification time there is no way
// to tell which answer happened later - only which device pushed last, which is
// not the same thing and loses real reviews.
export async function saveCard(card) {
  const stamped = { ...card, mod: Date.now() };
  await run(["cards"], "readwrite", (cards) => cards.put(stamped));
  return stamped;
}

// Bulk write for sync, which must not stamp: a card arriving from another
// device already carries the time it was actually changed there.
export async function putCards(list) {
  return run(["cards"], "readwrite", (cards) => {
    for (const card of list) cards.put(card);
  });
}

export async function putLog(entries) {
  return run(["log"], "readwrite", (log) => {
    for (const entry of entries) log.put(entry);
  });
}

// Deleting is the one action here that destroys something a learner made, so
// it takes the card's whole row with it - leaving an orphaned review log entry
// pointing at nothing would quietly corrupt any later attempt to rebuild a
// schedule from the log.
export async function deleteCard(id) {
  return run(["cards"], "readwrite", (cards) => cards.delete(id));
}

export async function history() {
  const db = await open();
  return all(db.transaction("log").objectStore("log"));
}

export async function wipe() {
  return run(["cards", "log", "meta"], "readwrite", (cards, log, meta) => {
    cards.clear(); log.clear(); meta.clear();
  });
}
