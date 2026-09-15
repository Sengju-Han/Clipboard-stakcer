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
const DB_VERSION = 2;

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
      // Audio captured off a video while mining a line. Kept out of the card
      // row on purpose: a card is read on every queue build and a few hundred
      // audio clips alongside it would make that read the slow part of the
      // morning. Created here rather than in a migration because an upgrade
      // that only adds a store leaves every existing row exactly where it was.
      if (!db.objectStoreNames.contains("media")) {
        db.createObjectStore("media", { keyPath: "name" });
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

// Every row as stored, tombstones included. Only sync wants this: a deletion
// that does not travel is a deletion that gets undone by the other device.
export async function allRows() {
  const db = await open();
  return all(db.transaction("cards").objectStore("cards"));
}

// The deck as the app should see it: everything except the tombstones, which
// are bookkeeping and not cards.
export async function allCards() {
  return (await allRows()).filter((row) => !row.deleted);
}

// Importing replaces the deck's content but must not touch scheduling a learner
// has already earned in this app. A card already here keeps its own state; only
// its wording is refreshed. Anything else would reset progress on every import.
export async function importDeck(deck) {
  // Keyed off every row, tombstones included: a card deleted here and then met
  // again in a re-imported file should stay deleted unless the file is newer.
  const existing = new Map((await allRows()).map((c) => [c.id, c]));
  const now = new Date().toISOString();
  let added = 0, refreshed = 0, restored = 0;

  // Every card needs a modification time or it compares as zero forever, is
  // never newer than anything, and never gets sent anywhere — the first sync
  // to an account pushed nothing at all, and an account holding no cards is
  // not a backup of anything.
  //
  // The time has to come from the deck rather than from the clock. Stamping
  // with the moment the file was read says the card changed then, which is not
  // true and has a consequence: two phones importing the same deck give it two
  // different times, the later import looks newer, and it overwrites a review
  // the other phone made yesterday. Both phones reading the same deck must
  // agree on when its cards last changed, and that is when the deck was built.
  const built = Date.parse(deck.built_at) || Date.now();

  await run(["cards", "meta"], "readwrite", (cards, meta) => {
    for (const card of deck.cards) {
      const had = existing.get(card.id);
      // Opening a file is somebody saying "this is what my deck is", so a card
      // they deleted here and then handed back in a file comes back — unlike a
      // sync, which is two devices reconciling with nobody watching and must
      // never undo a deletion. The count is reported either way.
      if (had?.deleted) {
        // Restoring is a change the learner just made, so it is stamped now —
        // otherwise the other phone's tombstone would delete it again.
        cards.put({ ...card, reviewedHere: 0, mod: Date.now() });
        restored += 1;
      } else if (had) {
        // Already here: its own history is what decides how new it is. An
        // import that only refreshes wording has changed nothing worth
        // claiming to be the newest thing that happened to this card.
        cards.put({
          ...card,
          fsrs: had.fsrs,
          reviewedHere: had.reviewedHere || 0,
          mod: had.mod || built,
        });
        refreshed += 1;
      } else {
        cards.put({ ...card, reviewedHere: 0, mod: built });
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

  return { added, refreshed, restored };
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

// Deleting leaves a mark behind, and it has to.
//
// A row simply removed is a row the other phone has never heard of, so the next
// sync sees a card it holds and this one does not, calls that "only there", and
// hands it straight back. Delete a card on Tuesday, sync on Wednesday, and it
// is in your deck again on Thursday — with nothing on screen admitting it.
//
// So what is left is a tombstone: the id, the fact of the deletion, and when.
// The merge already prefers whichever side changed last, so a deletion is
// simply the newest change to that card and needs no special case anywhere. It
// costs a few dozen bytes per deleted card, which is the right price.
export async function deleteCard(id) {
  return run(["cards"], "readwrite", (cards) => cards.put({ id, deleted: true, mod: Date.now() }));
}

// Undo. The log is append-only and stays that way: an undone answer is marked
// rather than deleted, which keeps two things true that deleting would break.
// Sync unions the two devices' logs by timestamp, so a row deleted here would
// simply come back from the other device the next time they met; and a review
// that was given and then taken back is itself a fact about the evening, which
// the row still records. Everything that counts answers - the statistics, the
// history written into an .apkg - skips the marked ones.
export async function unrecord(at) {
  const db = await open();
  return new Promise((resolve, reject) => {
    const store = db.transaction("log", "readwrite").objectStore("log");
    const req = store.get(at);
    req.onsuccess = () => {
      if (!req.result) return resolve(false);
      const put = store.put({ ...req.result, undone: true });
      put.onsuccess = () => resolve(true);
      put.onerror = () => reject(put.error);
    };
    req.onerror = () => reject(req.error);
  });
}

// Every answer that still stands. Undone ones are in the database and are not
// in here, because every caller wants the answers that count.
export async function history() {
  const db = await open();
  const rows = await all(db.transaction("log").objectStore("log"));
  return rows.filter((row) => !row.undone);
}

// The log exactly as stored, undone rows included. Only sync wants this: the
// mark has to travel, or the other device hands the answer back.
export async function wholeLog() {
  const db = await open();
  return all(db.transaction("log").objectStore("log"));
}

// ---- captured audio -------------------------------------------------------

export async function putMedia(name, blob) {
  return run(["media"], "readwrite", (media) => media.put({ name, blob, at: Date.now() }));
}

export async function getMedia(name) {
  const db = await open();
  return new Promise((resolve, reject) => {
    const req = db.transaction("media").objectStore("media").get(name);
    req.onsuccess = () => resolve(req.result ? req.result.blob : null);
    req.onerror = () => reject(req.error);
  });
}

export async function allMedia() {
  const db = await open();
  return all(db.transaction("media").objectStore("media"));
}

export async function deleteMedia(name) {
  return run(["media"], "readwrite", (media) => media.delete(name));
}

export async function mediaSize() {
  const rows = await allMedia();
  return { count: rows.length, bytes: rows.reduce((n, r) => n + (r.blob?.size || 0), 0) };
}

export async function wipe() {
  return run(["cards", "log", "meta", "media"], "readwrite", (cards, log, meta, media) => {
    cards.clear(); log.clear(); meta.clear(); media.clear();
  });
}
