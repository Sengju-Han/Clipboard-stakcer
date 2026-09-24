// Writing an .apkg, in the browser, so that nothing here is a one-way door.
//
// Reading Anki's format made this app usable. Writing it is what makes it
// safe to use: a deck you cannot get back out is a deck you are renting. Every
// card, every deck name, every tag and — the part a CSV cannot carry — every
// card's scheduling goes into a file that Anki, AnkiDroid and AnkiMobile all
// open with File → Import.
//
// Two decisions are worth stating, because both are deliberate:
//
//   The guid travels. Anki identifies a note by its guid, not its text, so a
//   card exported from here and imported there updates the note it came from
//   instead of arriving as a duplicate. Export, review in Anki for a week,
//   export back — the same 1,177 notes, not 2,354.
//
//   The legacy container is used on purpose. Anki 2.1.50 and later can write
//   a zstd-compressed collection.anki21b, but every version ever shipped reads
//   a plain collection.anki2 at schema 11, and this has to work on whatever
//   the person on the other end happens to be running.

import { zipSync, strToU8 } from "./vendor/fflate.mjs";
import { loadSql } from "./apkg.js";

const DAY = 86400000;
const FIELD_SEP = "\x1f";
const SCHEMA_VERSION = 11;

// Anki stores ease ×10 and will not schedule below 1300. FSRS difficulty runs
// 1..10 the other way up, so the map is inverted and then clamped: an easy
// card (difficulty 1) lands near 3100, a hard one (difficulty 10) at the floor.
const EASE_MAX = 3100, EASE_MIN = 1300;
function easeFrom(difficulty) {
  const d = Math.min(10, Math.max(1, Number(difficulty) || 5));
  return Math.round(Math.min(EASE_MAX, Math.max(EASE_MIN, EASE_MAX - (d - 1) * 200)));
}

// ---- the notetype --------------------------------------------------------
//
// Three fields, named the way this app's own reader already recognises them,
// so a deck that goes out and comes back arrives intact rather than as three
// anonymous columns. The memory hook rides inside the answer field, in italics
// under the word, which is the shape half of the collections in the wild use
// and the shape the reader here splits back apart.

const CSS = `.card {
  font-family: -apple-system, system-ui, sans-serif;
  font-size: 21px; text-align: center; color: #1c1d21; background: #fbfaf7;
  padding: 18px;
}
.word { font-size: 30px; font-weight: 700; }
.hook { font-size: 15px; color: #6c6a66; font-style: italic; margin-top: 6px; }
.example { font-size: 17px; color: #3a3b40; margin-top: 16px;
  border-top: 1px solid #e6e2da; padding-top: 14px; }
.night_mode .card, .card.night_mode { color: #efece6; background: #16171b; }`;

const FRONT = `{{Front}}`;
const BACK = `{{FrontSide}}<hr id=answer>{{Back}}{{#Example}}<div class=example>{{Example}}</div>{{/Example}}`;

function notetype(id, mod) {
  return {
    id, name: "Lexis", type: 0, mod, usn: -1, sortf: 0, did: 1,
    tmpls: [{
      name: "Recall", ord: 0, qfmt: FRONT, afmt: BACK,
      did: null, bqfmt: "", bafmt: "", bfont: "", bsize: 0,
    }],
    flds: [
      { name: "Front", ord: 0, sticky: false, rtl: false, font: "Arial", size: 20, media: [] },
      { name: "Back", ord: 1, sticky: false, rtl: false, font: "Arial", size: 20, media: [] },
      { name: "Example", ord: 2, sticky: false, rtl: false, font: "Arial", size: 20, media: [] },
    ],
    css: CSS,
    latexPre: "\\documentclass[12pt]{article}\n\\special{papersize=3in,5in}\n\\usepackage{amssymb,amsmath}\n\\pagestyle{empty}\n\\setlength{\\parindent}{0in}\n\\begin{document}\n",
    latexPost: "\\end{document}",
    latexsvg: false,
    // "the card exists if field 0 is non-empty" — without this Anki 2.1 decides
    // for itself and can conclude that no card should be generated at all.
    req: [[0, "any", [0]]],
    tags: [], vers: [],
  };
}

function deck(id, name, mod) {
  return {
    id, name, mod, usn: -1, desc: "", dyn: 0, conf: 1, collapsed: false,
    browserCollapsed: false, extendNew: 0, extendRev: 0,
    lrnToday: [0, 0], revToday: [0, 0], newToday: [0, 0], timeToday: [0, 0],
  };
}

function deckConfig(mod) {
  return {
    1: {
      id: 1, name: "Default", mod, usn: -1, maxTaken: 60, autoplay: true,
      timer: 0, replayq: true, dyn: 0,
      new: { bury: false, delays: [1, 10], initialFactor: 2500, ints: [1, 4, 0],
             order: 1, perDay: 20 },
      rev: { bury: false, ease4: 1.3, ivlFct: 1, maxIvl: 36500, perDay: 200, hardFactor: 1.2 },
      lapse: { delays: [10], leechAction: 1, leechFails: 8, minInt: 1, mult: 0 },
    },
  };
}

function collectionConfig(modelId) {
  return {
    nextPos: 1, estTimes: true, activeDecks: [1], sortType: "noteFld",
    timeLim: 0, sortBackwards: false, addToCur: true, curDeck: 1,
    newBury: true, newSpread: 0, dueCounts: true, curModel: String(modelId),
    collapseTime: 1200, schedVer: 2,
  };
}

const SCHEMA = `
CREATE TABLE col (id integer primary key, crt integer not null, mod integer not null,
  scm integer not null, ver integer not null, dty integer not null, usn integer not null,
  ls integer not null, conf text not null, models text not null, decks text not null,
  dconf text not null, tags text not null);
CREATE TABLE notes (id integer primary key, guid text not null, mid integer not null,
  mod integer not null, usn integer not null, tags text not null, flds text not null,
  sfld integer not null, csum integer not null, flags integer not null, data text not null);
CREATE TABLE cards (id integer primary key, nid integer not null, did integer not null,
  ord integer not null, mod integer not null, usn integer not null, type integer not null,
  queue integer not null, due integer not null, ivl integer not null, factor integer not null,
  reps integer not null, lapses integer not null, left integer not null, odue integer not null,
  odid integer not null, flags integer not null, data text not null);
CREATE TABLE revlog (id integer primary key, cid integer not null, usn integer not null,
  ease integer not null, ivl integer not null, lastIvl integer not null, factor integer not null,
  time integer not null, type integer not null);
CREATE TABLE graves (usn integer not null, oid integer not null, type integer not null);
CREATE INDEX ix_notes_usn on notes (usn);
CREATE INDEX ix_cards_usn on cards (usn);
CREATE INDEX ix_revlog_usn on revlog (usn);
CREATE INDEX ix_cards_nid on cards (nid);
CREATE INDEX ix_cards_sched on cards (did, queue, due);
CREATE INDEX ix_revlog_cid on revlog (cid);
CREATE INDEX ix_notes_csum on notes (csum);
`;

// ---- small helpers -------------------------------------------------------

function esc(text) {
  return String(text ?? "")
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

// Anki's own field text with HTML and media stripped, which is what it stores
// in sfld and hashes into csum.
function stripped(text) {
  return String(text ?? "")
    .replace(/\[sound:[^\]]*\]/g, "")
    .replace(/<[^>]+>/g, "")
    .replace(/&nbsp;/g, " ").replace(/&amp;/g, "&")
    .replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&quot;/g, '"')
    .trim();
}

// The duplicate check Anki runs in the browser is a hash of the first field:
// the first eight hex digits of its SHA-1, read as a number. Get it wrong and
// nothing breaks — it simply stops noticing duplicates — but it is four lines.
async function checksum(text) {
  const bytes = new TextEncoder().encode(stripped(text));
  const digest = await crypto.subtle.digest("SHA-1", bytes);
  const hex = [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
  return parseInt(hex.slice(0, 8), 16);
}

// A guid Anki will accept for a card that was born here rather than imported.
// Anki's own is 64-bit base91; any stable unique string works, and being
// stable is the point — export twice and the second one updates the first.
function guidFor(card) {
  if (card.guid) return String(card.guid);
  const raw = String(card.id || card.word || Math.random());
  let hash = 0x811c9dc5;
  for (let i = 0; i < raw.length; i += 1) {
    hash ^= raw.charCodeAt(i);
    hash = Math.imul(hash, 0x01000193) >>> 0;
  }
  return `lx${hash.toString(36)}${raw.length.toString(36)}`;
}

function startOfToday(now) {
  const d = new Date(now);
  d.setHours(0, 0, 0, 0);
  return d.getTime();
}

// ---- building it ---------------------------------------------------------

export async function buildApkg(cards, reviews = [], { now = Date.now(), media = [], onProgress = () => {} } = {}) {
  // Which captured clips this device actually holds. A card mined on the other
  // phone syncs across without its audio — clips do not travel — and writing
  // [sound:…] for a file that is not in the package produces a card that is
  // silent in Anki and a missing-media warning that is not the person's fault.
  // A reference to a file that came out of their own collection is different:
  // it is already on the other side, and that one stays.
  const captured = new Set(media.map((row) => row.name).filter(Boolean));
  onProgress("Opening a collection…");
  const SQL = await loadSql();
  const db = new SQL.Database();
  db.run(SCHEMA);

  const crt = Math.floor(startOfToday(now) / 1000);   // Anki counts days from here
  const modSec = Math.floor(now / 1000);
  const modelId = now;

  // Decks, in the order they are first seen, so the list in Anki reads the way
  // the deck list here does.
  const deckIds = new Map();
  const decks = { 1: deck(1, "Default", modSec) };
  let nextDeck = now + 1;
  for (const card of cards) {
    const name = (card.deck || "Default").trim() || "Default";
    if (deckIds.has(name)) continue;
    if (name === "Default") { deckIds.set(name, 1); continue; }
    const id = nextDeck++;
    deckIds.set(name, id);
    decks[id] = deck(id, name, modSec);
  }

  db.run(
    `INSERT INTO col VALUES (1, ?, ?, ?, ?, 0, -1, 0, ?, ?, ?, ?, '{}')`,
    [crt, modSec, now, SCHEMA_VERSION,
      JSON.stringify(collectionConfig(modelId)),
      JSON.stringify({ [modelId]: notetype(modelId, modSec) }),
      JSON.stringify(decks),
      JSON.stringify(deckConfig(modSec))],
  );

  onProgress(`Writing ${cards.length} cards…`);
  const note = db.prepare(`INSERT INTO notes VALUES (?,?,?,?,-1,?,?,?,?,0,'')`);
  const row = db.prepare(`INSERT INTO cards VALUES (?,?,?,0,?,-1,?,?,?,?,?,?,?,?,0,0,0,?)`);

  const today = startOfToday(now);
  const cardIds = new Map();     // Lexis card id -> Anki card id, for the revlog
  let id = now;
  let newPos = 0;

  for (const card of cards) {
    const nid = id;
    const cid = id + 1;
    id += 2;
    cardIds.set(card.id, cid);

    // The answer field keeps the word and its hook together, because that is
    // where a hook is useful — under the word, on the back, in italics.
    //
    // The folded meaning goes back on the end, unescaped and exactly as it
    // arrived: it is markup this project wrote, it was read off an Anki card,
    // and it is going onto an Anki card. This app never renders it. Escaping
    // it would put the tags on the card as text, and dropping it would quietly
    // undo the work the fold workflow did — the same reason the audio names
    // below travel even though the files do not.
    const back = [
      `<div class=word>${esc(card.word)}</div>`,
      card.hook ? `<div class=hook>${esc(card.hook)}</div>` : "",
      card.detail || "",
    ].filter(Boolean).join("");
    // The audio reference travels even though the file does not: these names
    // came out of the person's own collection, so on the other side they
    // already resolve. A missing name costs a silent card; a stripped one
    // costs the card its sound forever.
    const hasSound = card.audio && (!card.audioLocal || captured.has(card.audio));
    // Anything else the note referred to when it was read. Those names came
    // out of the collection too, so they resolve on the other side; only a
    // clip captured on this phone can be missing, and there is one of those
    // at most, which is card.audio above.
    const alsoSound = Array.isArray(card.audioMore) ? card.audioMore.filter(Boolean) : [];
    const example = [
      esc(card.example || ""),
      ...(hasSound ? [`[sound:${card.audio}]`] : []),
      ...alsoSound.map((name) => `[sound:${name}]`),
    ].filter(Boolean).join(" ");
    const front = esc(card.clue || card.word);
    const flds = [front, back, example].join(FIELD_SEP);

    note.run([nid, guidFor(card), modelId, modSec,
      ` ${(card.tags || []).join(" ")} `.replace(/\s+/g, " "),
      flds, stripped(front), await checksum(front)]);

    const f = card.fsrs || {};
    const state = Number(f.state) || 0;
    const reps = Number(f.reps) || 0;
    const lapses = Number(f.lapses) || 0;
    const stability = Number(f.stability) || 0;
    const ivl = Math.max(1, Math.round(Number(f.scheduled_days) || stability || 1));
    const dueAt = f.due ? new Date(f.due).getTime() : now;

    let type, queue, due, factor, left, data;
    if (state === 0) {
      // A new card is a queue position, not a date.
      type = 0; queue = 0; due = newPos++; factor = 0; left = 0;
      data = JSON.stringify({ pos: due });
    } else if (state === 1 || state === 3) {
      // Learning and relearning are timed in seconds, and `left` is Anki's
      // "steps remaining today, plus a thousand per step overall".
      type = state === 1 ? 1 : 3;
      queue = 1;
      due = Math.floor(Math.max(now, dueAt) / 1000);
      factor = easeFrom(f.difficulty);
      left = 1001;
      data = JSON.stringify({ s: round4(stability), d: round4(f.difficulty) });
    } else {
      type = 2; queue = 2;
      due = Math.round((startOfToday(dueAt) - today) / DAY) + daysSinceCrt(today, crt);
      factor = easeFrom(f.difficulty);
      left = 0;
      data = JSON.stringify({ s: round4(stability), d: round4(f.difficulty) });
    }

    row.run([cid, nid, deckIds.get((card.deck || "Default").trim() || "Default") || 1,
      modSec, type, queue, due, state === 0 ? 0 : ivl, factor, reps, lapses, left, data]);
  }
  note.free();
  row.free();

  // The history goes too. Anki's own statistics, its optimiser and anything
  // that wants to re-derive a schedule all read revlog, so a deck that arrives
  // without it arrives with amnesia.
  onProgress(`Writing ${reviews.length} reviews…`);
  const log = db.prepare(`INSERT INTO revlog VALUES (?,?,-1,?,?,?,?,?,?)`);
  const used = new Set();
  for (const entry of reviews) {
    const cid = cardIds.get(entry.id);
    if (!cid) continue;                              // a review of a deleted card
    let at = new Date(entry.at).getTime();
    while (used.has(at)) at += 1;                    // the id is the timestamp
    used.add(at);
    const kind = entry.state === 0 || entry.state === 1 ? 0 : entry.state === 3 ? 2 : 1;
    log.run([at, cid, Number(entry.rating) || 3, Math.round(Number(entry.scheduled_days) || 0),
      Math.round(Number(entry.elapsed_days) || 0), easeFrom(entry.difficulty),
      0, kind]);   // Anki's `time` is how long the answer took; nothing here measures it
  }
  log.free();

  onProgress("Packing…");
  const bytes = db.export();
  db.close();

  // Media travels as numbered files plus a map from those numbers to the real
  // names, which is how every .apkg has always carried it. Only audio captured
  // here is included: the mp3s that came out of the person's own collection are
  // already on the other side, and sending them back would turn a 200KB file
  // into most of a gigabyte for no gain.
  const files = { "collection.anki2": bytes };
  const names = {};
  let n = 0;
  for (const { name, blob } of media) {
    if (!name || !blob) continue;
    onProgress(`Packing audio ${n + 1} of ${media.length}…`);
    files[String(n)] = new Uint8Array(await blob.arrayBuffer());
    names[String(n)] = name;
    n += 1;
  }
  files.media = strToU8(JSON.stringify(names));

  // Legacy container: a plain SQLite collection. Read by every Anki ever
  // shipped, including AnkiDroid and AnkiMobile.
  const zip = zipSync(files, { level: 6 });

  return new Blob([zip], { type: "application/octet-stream" });
}

function round4(n) {
  const v = Number(n);
  return Number.isFinite(v) ? Number(v.toFixed(4)) : 0;
}

// Days between the collection's creation and today, which is what a review
// card's due column counts from. Today is the creation day here, so this is
// zero — stated rather than assumed, because the two stop being the same the
// moment an export happens after midnight on a collection made yesterday.
function daysSinceCrt(today, crt) {
  return Math.round((today - crt * 1000) / DAY);
}
