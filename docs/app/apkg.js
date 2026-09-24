// Reading an .apkg, in the browser, with nothing uploaded anywhere.
//
// This is what "compatible with Anki" has to mean in practice: you hand it the
// file AnkiDroid exported and it works, without a workflow, without an account,
// without your collection leaving the phone. Everything below runs locally.
//
// The format, as of Anki 2.1.50 and later:
//
//   meta                 a version marker
//   collection.anki21b   the real database, SQLite compressed with zstd
//   collection.anki2     a decoy — a valid SQLite file holding one warning row,
//                        there so that pre-2.1.50 Anki says "upgrade" instead
//                        of crashing. Reading it would import one junk card and
//                        report success, which is worse than failing, so the
//                        newest collection present always wins.
//   media                a JSON map of numbered files to real names
//
// Older exports carry collection.anki21 or collection.anki2 for real, and both
// are handled.

import { unzipSync } from "./vendor/fflate.mjs";
import { decompress as unzstd } from "./vendor/fzstd.mjs";

// Newest first: the decoy is last, and only ever read when it is the only one.
const COLLECTIONS = ["collection.anki21b", "collection.anki21", "collection.anki2"];

const FIELD_SEP = "\x1f";

// Anki card types, which are not FSRS states and do not share its numbering by
// accident — they happen to line up, and are mapped explicitly rather than cast.
const ANKI_NEW = 0, ANKI_LEARNING = 1, ANKI_REVIEW = 2, ANKI_RELEARNING = 3;
const FSRS_NEW = 0, FSRS_LEARNING = 1, FSRS_REVIEW = 2, FSRS_RELEARNING = 3;
const STATE = {
  [ANKI_NEW]: FSRS_NEW,
  [ANKI_LEARNING]: FSRS_LEARNING,
  [ANKI_REVIEW]: FSRS_REVIEW,
  [ANKI_RELEARNING]: FSRS_RELEARNING,
};

const S_MIN = 1.0;
const EASE_ANCHOR = 2500;   // Anki stores ease ×10; 2500 is the default start
const DIFFICULTY_MID = 5.0;

const SOUND_TAG = /\[sound:([^\]]*)\]/g;
const MARKUP = /<[^>]+>/g;
const BREAK = /<\s*(?:div|br|p|li|tr|details|summary)\b[^>]*>/gi;

// The Add to Anki page folds the word's generated meaning into a <details>
// block underneath it, where on the card it costs one tap and nothing until
// then. Run through plain() it is several hundred words of definition, Korean
// and example sentences, and it would land in the memory hook - the one line
// of italics under the word on this app's cards.
//
// So it is kept out of the text and kept on the card. plain() drops it, and
// the raw block is carried on the side, exactly as it was written, so a deck
// that goes out through apkgout.js arrives back in Anki with it still there.
//
// The same decision as the [sound:] names further down, for the same reason:
// this app does not use it, and losing it on the way in would lose it on the
// way back out, silently, with the card looking perfectly healthy. It is never
// rendered here - it goes from one Anki collection to another.
const DETAIL_BLOCK = /<details\b[^>]*lexis-detail[\s\S]*?<\/details>/gi;

// Twice the page's own cap on the block it writes, and a block past it is not
// one this project wrote. Kept out rather than carried: every card holds its
// own copy in this browser's database, and a file that arrived from somewhere
// else should not decide how much room that takes.
const DETAIL_MAX = 8000;

// Which field holds the word being learned. Name first, because a deck that
// says "Back" or "Word" has told you; shape second, because across a whole
// notetype the vocabulary field is reliably the short one and the definition
// and example are long. Neither guess is needed when there is only one field.
const WORD_NAMES = /^(back|word|term|vocab|vocabulary|target|expression|answer)$/i;
const EXAMPLE_NAMES = /^(example|sentence|context|usage|sample)$/i;

let sqlReady = null;

// sql.js ships as a classic script that hangs initSqlJs on window, so it is
// injected rather than imported, and only when an import actually happens —
// it is the better part of a megabyte and most sessions never need it.
export function loadSql() {
  if (sqlReady) return sqlReady;
  sqlReady = new Promise((resolve, reject) => {
    if (window.initSqlJs) return resolve(window.initSqlJs);
    const tag = document.createElement("script");
    tag.src = "vendor/sql-wasm.js";
    tag.onload = () => window.initSqlJs ? resolve(window.initSqlJs)
      : reject(new Error("sql-wasm.js loaded but defined nothing"));
    tag.onerror = () => reject(new Error("vendor/sql-wasm.js could not be loaded"));
    document.head.appendChild(tag);
  }).then((init) => init({ locateFile: () => "vendor/sql-wasm.wasm" }));
  return sqlReady;
}

function plain(text) {
  return String(text || "")
    .replace(DETAIL_BLOCK, "")
    .replace(SOUND_TAG, "")
    .replace(BREAK, "\n")
    .replace(MARKUP, "")
    .replace(/&nbsp;/g, " ").replace(/&amp;/g, "&")
    .replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&quot;/g, '"')
    .replace(/[ \t]+/g, " ")
    .split("\n").map((l) => l.trim()).filter(Boolean).join("\n")
    .trim();
}

function sounds(text) {
  return [...String(text || "").matchAll(SOUND_TAG)].map((m) => m[1]);
}

function rows(db, sql) {
  const out = [];
  const stmt = db.prepare(sql);
  try {
    while (stmt.step()) out.push(stmt.getAsObject());
  } finally {
    stmt.free();
  }
  return out;
}

function tableExists(db, name) {
  return rows(db, `select name from sqlite_master where type='table' and name='${name}'`).length > 0;
}

// Field names per notetype. The modern schema keeps them in their own table;
// before that they lived inside a JSON blob in col.models.
function fieldNames(db) {
  const byType = new Map();
  if (tableExists(db, "fields")) {
    for (const r of rows(db, "select ntid, ord, name from fields order by ntid, ord")) {
      if (!byType.has(r.ntid)) byType.set(r.ntid, []);
      byType.get(r.ntid)[r.ord] = r.name;
    }
    return byType;
  }
  const col = rows(db, "select models from col")[0];
  if (!col || !col.models) return byType;
  try {
    for (const [id, model] of Object.entries(JSON.parse(col.models))) {
      byType.set(Number(id), (model.flds || []).sort((a, b) => a.ord - b.ord).map((f) => f.name));
    }
  } catch { /* an unreadable models blob leaves every notetype unnamed */ }
  return byType;
}

function deckNames(db) {
  const byId = new Map();
  if (tableExists(db, "decks") && rows(db, "select name from sqlite_master where type='table' and name='decks'").length) {
    for (const r of rows(db, "select id, name from decks")) {
      // Subdecks are separated by \x1f in the modern schema and :: in the old.
      byId.set(r.id, String(r.name).split(FIELD_SEP).join("::"));
    }
    if (byId.size) return byId;
  }
  const col = rows(db, "select decks from col")[0];
  if (col && col.decks) {
    try {
      for (const [id, deck] of Object.entries(JSON.parse(col.decks))) byId.set(Number(id), deck.name);
    } catch { /* fall through to Default */ }
  }
  return byId;
}

// Decide, once per notetype, which field is the word and which is the example.
function chooseFields(names, samples) {
  if (!names || !names.length) return { word: -1, example: -1 };
  if (names.length === 1) return { word: 0, example: -1 };

  let word = names.findIndex((n) => WORD_NAMES.test(String(n || "").trim()));
  let example = names.findIndex((n) => EXAMPLE_NAMES.test(String(n || "").trim()));

  // Nothing named usefully: fall back to length. A vocabulary field is short
  // across a whole notetype; a definition or an example sentence is not.
  if (word < 0 || example < 0) {
    const medians = names.map((_, i) => {
      const lengths = samples.map((s) => (s[i] || "").length).filter((n) => n > 0).sort((a, b) => a - b);
      return lengths.length ? lengths[Math.floor(lengths.length / 2)] : Infinity;
    });
    if (word < 0) word = medians.indexOf(Math.min(...medians));
    if (example < 0) {
      const longest = Math.max(...medians.filter((n) => Number.isFinite(n)));
      const at = medians.indexOf(longest);
      example = at === word ? -1 : at;
    }
  }
  return { word, example };
}

function difficultyFrom(card) {
  // Anki 23.10+ keeps FSRS memory state in cards.data as JSON.
  try {
    const data = card.data ? JSON.parse(card.data) : null;
    const d = data && (data.d ?? data.difficulty);
    if (typeof d === "number" && d > 0) return Math.min(10, Math.max(1, d));
  } catch { /* not JSON, or not FSRS */ }
  const ease = Number(card.factor) || 0;
  if (ease <= 0) return DIFFICULTY_MID;
  // 1300 (Anki's floor) lands near 8, 2500 in the middle, higher eases below.
  return Math.min(10, Math.max(1, DIFFICULTY_MID + (EASE_ANCHOR - ease) / 400));
}

function stabilityFrom(card) {
  try {
    const data = card.data ? JSON.parse(card.data) : null;
    const s = data && (data.s ?? data.stability);
    if (typeof s === "number" && s > 0) return Math.max(S_MIN, s);
  } catch { /* not JSON, or not FSRS */ }
  return Math.max(S_MIN, Number(card.ivl) || 0);
}

// Anki stores a review card's due as days since the collection was created, a
// learning card's as an epoch timestamp, and a new card's as a queue position.
// A review card is owed on a day, and that day has to survive being read
// somewhere else. Noon UTC is the same calendar day from UTC-8 to UTC+15 and
// stays on it after the four-hour shift the app's day starts with; midnight
// UTC does neither, and made a card due today arrive at nine in the morning in
// Seoul and a day early west of it. The day taken is the local one, because
// the person who exported the file and the person reading it are the same
// person in the same place.
function noonOfTheDay(ms) {
  const local = new Date(ms);
  return new Date(Date.UTC(local.getFullYear(), local.getMonth(), local.getDate(), 12)).toISOString();
}

function dueFrom(card, crt, now) {
  const type = Number(card.type);
  if (type === ANKI_NEW) return new Date(now).toISOString();
  const due = Number(card.due) || 0;
  // Learning and relearning are timed in minutes, so the instant is the point.
  if (type === ANKI_LEARNING || (type === ANKI_RELEARNING && due > 1e9)) {
    return new Date(due * 1000).toISOString();
  }
  const when = (crt + due * 86400) * 1000;
  // A card parked centuries out is a tombstone, not a schedule.
  if (!Number.isFinite(when) || when > now + 50 * 365 * 86400 * 1000) {
    return noonOfTheDay(now);
  }
  return noonOfTheDay(when);
}

export async function readApkg(file, onProgress = () => {}) {
  onProgress("Opening the package…");
  const buffer = new Uint8Array(await file.arrayBuffer());

  let entries;
  try {
    entries = unzipSync(buffer);
  } catch (err) {
    throw new Error(`That is not a readable .apkg — it did not unzip (${err.message || err}).`);
  }

  const found = COLLECTIONS.find((name) => entries[name]);
  if (!found) {
    throw new Error("No Anki collection inside that file. An .apkg contains " +
      "collection.anki21b or collection.anki2; this one has neither.");
  }

  onProgress("Decompressing the collection…");
  let bytes = entries[found];
  if (found.endsWith("b")) {
    try {
      bytes = unzstd(bytes);
    } catch (err) {
      throw new Error(`The collection is zstd-compressed and would not decompress (${err.message || err}).`);
    }
  }

  onProgress("Reading the database…");
  const SQL = await loadSql();
  const db = new SQL.Database(bytes);

  try {
    return extract(db, found, onProgress);
  } finally {
    db.close();
  }
}

function extract(db, source, onProgress) {
  const col = rows(db, "select crt from col")[0];
  const crt = col ? Number(col.crt) : Math.floor(Date.now() / 1000);
  const now = Date.now();

  const decks = deckNames(db);
  const fields = fieldNames(db);

  onProgress("Reading notes…");
  const all = rows(db, `
    select c.id as cid, c.nid, c.did, c.odid, c.type, c.queue, c.due, c.ivl,
           c.factor, c.reps, c.lapses, c.data,
           n.guid, n.mid, n.tags, n.flds
    from cards c join notes n on n.id = c.nid
    order by c.id
  `);

  if (!all.length) {
    throw new Error("That collection has no cards in it.");
  }

  // Work out the field roles per notetype, from a sample of its own notes.
  const samplesByType = new Map();
  for (const row of all) {
    if (!samplesByType.has(row.mid)) samplesByType.set(row.mid, []);
    const bag = samplesByType.get(row.mid);
    if (bag.length < 40) bag.push(String(row.flds || "").split(FIELD_SEP));
  }
  const rolesByType = new Map();
  for (const [mid, samples] of samplesByType) {
    rolesByType.set(mid, chooseFields(fields.get(mid) || [], samples));
  }

  onProgress("Converting scheduling…");
  const cards = [];
  const seen = new Set();
  let skipped = 0, carried = 0, converted = 0, fresh = 0;

  for (const row of all) {
    const parts = String(row.flds || "").split(FIELD_SEP);
    const roles = rolesByType.get(row.mid) || { word: 0, example: -1 };
    const names = fields.get(row.mid) || [];

    const wordRaw = String(parts[roles.word] || "");
    const word = plain(wordRaw);
    if (!word) { skipped += 1; continue; }
    // .match() with a global pattern resets it first, so there is one regex
    // here rather than a second one a character different from the first.
    const found = (wordRaw.match(DETAIL_BLOCK) || [""])[0];
    const detail = found.length <= DETAIL_MAX ? found : "";

    // One card per note: this app reviews a word, not each of Anki's templates.
    // A reversed-card notetype would otherwise import the same word twice.
    if (seen.has(row.guid)) { skipped += 1; continue; }
    seen.add(row.guid);

    const exampleRaw = roles.example >= 0 ? (parts[roles.example] || "") : "";
    const clue = parts
      .map((value, i) => (i === roles.word || i === roles.example) ? "" : plain(value))
      .filter(Boolean)
      .join("\n");

    // Every recording the note refers to, the example's first because that is
    // the one this app plays. The rest are kept rather than dropped: a note
    // can carry a recording of the word as well as of the sentence, and the
    // one that is not played here is still the one that plays in Anki. Losing
    // it on the way in would lose it on the way back out, silently, and the
    // file would sit in the media folder looking perfectly healthy.
    const audio = [...sounds(exampleRaw), ...parts.flatMap((p, i) => i === roles.example ? [] : sounds(p))];

    const type = Number(row.type);
    const state = STATE[type] ?? FSRS_NEW;
    const isNew = state === FSRS_NEW;

    let hadFsrs = false;
    try {
      const data = row.data ? JSON.parse(row.data) : null;
      hadFsrs = Boolean(data && (data.s ?? data.stability));
    } catch { /* not FSRS */ }

    if (isNew) fresh += 1; else if (hadFsrs) carried += 1; else converted += 1;

    // Half of some collections write the answer as the word on one line with a
    // memory hook underneath. Kept apart so the word stays checkable at a glance.
    const [head, ...rest] = word.split("\n");

    cards.push({
      id: row.guid,
      guid: row.guid,
      deck: decks.get(row.odid || row.did) || decks.get(row.did) || "Default",
      word: head.trim(),
      hook: rest.join(" ").trim(),
      ...(detail ? { detail } : {}),
      clue,
      example: plain(exampleRaw),
      audio: audio[0] || "",
      ...(audio.length > 1 ? { audioMore: audio.slice(1) } : {}),
      tags: String(row.tags || "").split(" ").filter(Boolean),
      notetype: names.length ? (names.join("/") ? `${names.length} fields` : "") : "",
      created: new Date(Number(row.nid)).toISOString().slice(0, 10),
      fsrs: {
        due: dueFrom(row, crt, now),
        stability: isNew ? 0 : Number(stabilityFrom(row).toFixed(4)),
        difficulty: isNew ? 0 : Number(difficultyFrom(row).toFixed(4)),
        elapsed_days: 0,
        scheduled_days: Number(row.ivl) || 0,
        learning_steps: 0,
        reps: Number(row.reps) || 0,
        lapses: Number(row.lapses) || 0,
        state,
        last_review: null,
      },
      imported: {
        interval_days: Number(row.ivl) || 0,
        ease_pct: (Number(row.factor) || 0) / 10,
        had_fsrs_state: hadFsrs,
      },
    });
  }

  return {
    built_at: new Date().toISOString(),
    source: `${source} from an .apkg`,
    card_count: cards.length,
    decks: [...new Set(cards.map((c) => c.deck))].sort(),
    scheduling: { carried_fsrs_state: carried, converted_from_ease: converted, new: fresh },
    skipped,
    cards,
  };
}
