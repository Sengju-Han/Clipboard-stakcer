// Lexis — the review loop.
//
// The whole app runs from IndexedDB. The network is touched exactly twice: once
// to fetch a deck that is newer than the one already here, and never during a
// session. Answering a card is a local write, so it stays instant on a train.

import * as store from "./store.js";
import { scheduler, queue, counts, preview, answer, intervalLabel, Rating, State, DEFAULTS }
  from "./review.js";

const VERSION = "2026-09-15.13";
const DECK_URL = "../deck/deck.json";

const $ = (id) => document.getElementById(id);
const screens = ["boot", "home", "stats", "watch", "browse", "edit", "add", "review", "done"];

function show(name) {
  for (const s of screens) $(`screen-${s}`).hidden = s !== name;
}

// ---- settings ------------------------------------------------------------
// Kept in localStorage rather than the database: they are per-device
// preferences, not learning state, and losing them costs nothing.
const prefs = {
  read() {
    let raw = {};
    try { raw = JSON.parse(localStorage.getItem("lexis:prefs") || "{}"); } catch { /* blocked */ }
    return {
      retention: Number(raw.retention) || DEFAULTS.retention,
      newPerDay: Number.isFinite(raw.newPerDay) ? raw.newPerDay : DEFAULTS.newPerDay,
      mediaBase: typeof raw.mediaBase === "string" ? raw.mediaBase : "",
      deck: raw.deck || "",
      anthropic: typeof raw.anthropic === "string" ? raw.anthropic : "",
      ghToken: typeof raw.ghToken === "string" ? raw.ghToken : "",
      ghOwner: typeof raw.ghOwner === "string" ? raw.ghOwner : "",
      ghRepo: typeof raw.ghRepo === "string" ? raw.ghRepo : "",
    };
  },
  write(patch) {
    const next = { ...prefs.read(), ...patch };
    try { localStorage.setItem("lexis:prefs", JSON.stringify(next)); } catch { /* blocked */ }
    return next;
  },
};

// New cards are rationed per day, which means the day's count has to survive a
// reload — otherwise closing the app is a way to get unlimited new cards.
function introducedToday() {
  const today = new Date().toISOString().slice(0, 10);
  try {
    const raw = JSON.parse(localStorage.getItem("lexis:new") || "{}");
    return raw.day === today ? (raw.count || 0) : 0;
  } catch { return 0; }
}

function noteIntroduced(n = 1) {
  const today = new Date().toISOString().slice(0, 10);
  try {
    localStorage.setItem("lexis:new", JSON.stringify({ day: today, count: introducedToday() + n }));
  } catch { /* blocked */ }
}

// ---- session state -------------------------------------------------------
let engine = scheduler();
let session = null;   // { cards, index, size }
let current = null;
let cache = [];       // every card, held in memory for the duration of a session

// ---- boot ----------------------------------------------------------------
async function boot() {
  const settings = prefs.read();
  engine = scheduler(settings.retention);
  applySettingsToForm(settings);

  let count = 0;
  try {
    count = await store.cardCount();
  } catch (err) {
    return bootFailed(`This browser would not open its database (${err.name || err}). ` +
      `Private browsing usually causes that.`);
  }

  if (count === 0) {
    const fetched = await tryImport({ silent: true });
    if (!fetched.ok) {
      return bootFailed(fetched.why, true);
    }
  }
  await goHome();
}

function bootFailed(why, offerImport = false) {
  $("boot-spin").hidden = true;
  $("boot-note").textContent = "Nothing to review yet.";
  $("boot-detail").textContent = why;
  $("boot-actions").hidden = !offerImport;
  show("boot");
}

async function tryImport({ silent = false } = {}) {
  try {
    const res = await fetch(`${DECK_URL}?v=${Date.now()}`, { cache: "no-store" });
    if (!res.ok) return { ok: false, why: `The deck could not be fetched (${res.status}).` };
    const deck = await res.json();
    if (!deck.cards || !deck.cards.length) return { ok: false, why: "That deck file has no cards in it." };
    const result = await store.importDeck(deck);
    if (!silent) {
      $("settings-note").textContent =
        `${result.added} added, ${result.refreshed} refreshed. Scheduling you had already earned was kept.`;
    }
    return { ok: true, ...result };
  } catch (err) {
    return { ok: false, why: `The deck could not be fetched (${err.message || err}). ` +
      `It is built by the deck workflow and committed to docs/deck/.` };
  }
}

// ---- home ----------------------------------------------------------------
async function goHome() {
  cache = await store.allCards();
  const settings = prefs.read();
  const tally = counts(cache);

  $("n-due").textContent = tally.due;
  $("n-new").textContent = Math.max(0, Math.min(tally.fresh, settings.newPerDay - introducedToday()));
  $("n-known").textContent = tally.known;

  const ready = queue(cache, {
    deck: settings.deck,
    newPerDay: settings.newPerDay,
    introducedToday: introducedToday(),
  });
  $("start-btn").disabled = ready.length === 0;
  $("start-btn").textContent = ready.length ? `Review ${ready.length}` : "Nothing due right now";

  const info = await store.meta("deck");
  $("home-note").textContent = info
    ? `${tally.total} cards${settings.deck ? ` · ${settings.deck}` : ""} · imported ${new Date(info.importedAt).toLocaleDateString()}`
    : `${tally.total} cards`;

  renderDecks(cache, settings);
  show("home");
}

function renderDecks(cards, settings) {
  const names = [...new Set(cards.map((c) => c.deck))].sort();
  const list = $("deck-list");
  list.innerHTML = "";
  if (names.length < 2) return;

  const row = (name, label) => {
    const pool = name ? cards.filter((c) => c.deck === name) : cards;
    const t = counts(pool);
    const b = document.createElement("button");
    b.type = "button";
    b.className = "deck-row";
    b.innerHTML = `<span class="name"></span><span class="tally"><b></b> due · <span></span> cards</span>`;
    b.querySelector(".name").textContent = label + (settings.deck === name ? "  ✓" : "");
    b.querySelector("b").textContent = t.due;
    b.querySelector(".tally span").textContent = t.total;
    b.addEventListener("click", async () => {
      prefs.write({ deck: settings.deck === name ? "" : name });
      await goHome();
    });
    return b;
  };

  list.appendChild(row("", "Everything"));
  for (const name of names) list.appendChild(row(name, name));
}

// ---- review --------------------------------------------------------------
function startSession() {
  const settings = prefs.read();
  const cards = queue(cache, {
    deck: settings.deck,
    newPerDay: settings.newPerDay,
    introducedToday: introducedToday(),
  });
  if (!cards.length) return;
  session = { cards, index: 0, size: cards.length, answered: 0 };
  show("review");
  nextCard();
}

function nextCard() {
  if (!session || session.index >= session.cards.length) return finish();
  current = session.cards[session.index];

  $("card-deck").textContent = current.deck;
  $("card-clue").textContent = current.clue || "(no clue on this card)";
  $("card-word").textContent = current.word;
  $("card-hook").textContent = current.hook || "";
  $("card-hook").hidden = !current.hook;
  $("card-example").textContent = current.example || "";
  $("card-example").hidden = !current.example;

  $("answer").hidden = true;
  $("grades").hidden = true;
  $("reveal-btn").hidden = false;
  $("next-btn").hidden = true;
  $("brain").innerHTML = "";
  explaining = "";

  const done = session.index;
  $("progress-bar").style.width = `${(done / session.size) * 100}%`;
  $("left-count").textContent = session.size - done;
}

function reveal() {
  if (!current) return;
  $("answer").hidden = false;
  $("reveal-btn").hidden = true;
  $("grades").hidden = false;

  const labels = preview(engine, current);
  for (const r of [1, 2, 3, 4]) $(`i-${r}`).textContent = labels[r] || "";

  speak(current);
}

async function grade(rating) {
  if (!current) return;
  const wasNew = current.fsrs.state === State.New;
  const { card, log } = answer(engine, current, rating);

  // The log first: an answer that was given and not scheduled can be replayed,
  // a schedule with no answer behind it cannot be explained.
  await store.record(log);
  await store.saveCard(card);
  if (wasNew) noteIntroduced();

  const i = cache.findIndex((c) => c.id === card.id);
  if (i >= 0) cache[i] = card;

  session.answered += 1;
  session.index += 1;

  // Again means it is not learned; it comes back before the session ends
  // rather than at whatever minute FSRS nominated, which may be after you
  // have put the phone down.
  if (rating === Rating.Again) session.cards.push(card);

  // Again also means the word did not stick, and the moment it has just failed
  // is the only one where the explanation reliably gets read rather than
  // skipped past. So the card stays up, the panel opens itself, and moving on
  // becomes a deliberate tap — advancing straight away would flash the
  // explanation and wipe it before a word of it was read.
  if (rating === Rating.Again) {
    $("grades").hidden = true;
    $("next-btn").hidden = false;
    showWhy(current);
    return;
  }

  nextCard();
}

// Only reachable from the pause after an Again.
function moveOn() {
  $("next-btn").hidden = true;
  nextCard();
}

function finish() {
  const n = session ? session.answered : 0;
  $("done-head").textContent = n ? "Session done" : "Nothing reviewed";
  $("done-note").textContent = n
    ? `${n} card${n === 1 ? "" : "s"} answered. The next ones are scheduled.`
    : "";
  session = null;
  current = null;
  show("done");
}

// ---- audio ---------------------------------------------------------------
// Real recorded audio when the media is published, the phone's own voice when
// it is not. A card with no sound at all is worse than a synthetic one.
let voice = null;

function speak(card) {
  const base = prefs.read().mediaBase.trim();
  if (base && card.audio) {
    const src = base.replace(/\/?$/, "/") + card.audio;
    const audio = new Audio(src);
    audio.play().catch(() => sayIt(card));
    return;
  }
  sayIt(card);
}

function sayIt(card) {
  if (!("speechSynthesis" in window)) return;
  const text = card.example || card.word;
  try {
    speechSynthesis.cancel();
    const say = new SpeechSynthesisUtterance(text);
    say.lang = "en-US";
    say.rate = 0.95;
    if (!voice) {
      voice = speechSynthesis.getVoices().find((v) => /^en[-_]/i.test(v.lang)) || null;
    }
    if (voice) say.voice = voice;
    speechSynthesis.speak(say);
  } catch { /* no voices on this device */ }
}

// ---- settings form -------------------------------------------------------
function applySettingsToForm(s) {
  $("version-note").textContent = `Lexis ${VERSION}`;
  $("anthropic").value = s.anthropic;
  $("gh-token").value = s.ghToken;
  $("gh-owner").value = s.ghOwner;
  $("gh-repo").value = s.ghRepo;
  $("retention").value = s.retention;
  $("retention-out").textContent = `${Math.round(s.retention * 100)}%`;
  $("new-per-day").value = s.newPerDay;
  $("media-base").value = s.mediaBase;
}

// ---- wiring --------------------------------------------------------------
$("start-btn").addEventListener("click", startSession);
$("reveal-btn").addEventListener("click", reveal);
$("again-btn").addEventListener("click", goHome);
$("quit-btn").addEventListener("click", () => { session = null; goHome(); });
$("settings-btn").addEventListener("click", () => {
  $("settings").open = !$("settings").open;
  $("settings").scrollIntoView({ behavior: "smooth", block: "nearest" });
});
$("import-btn").addEventListener("click", async () => {
  $("boot-detail").textContent = "Fetching…";
  const r = await tryImport();
  if (r.ok) await goHome(); else $("boot-detail").textContent = r.why;
});
$("audio-btn").addEventListener("click", () => current && speak(current));

// ---- why a word will not stick -------------------------------------------
// Offered on every card, and opened by itself after an Again, because the
// moment a word has just failed is the only one where anyone reliably reads
// the explanation rather than pressing on.
let explaining = "";

async function showWhy(card) {
  const panel = $("brain");
  if (!panel || !card) return;
  if (explaining === card.id) return;
  explaining = card.id;
  panel.innerHTML = `<div class="waiting">Looking up “${card.word}”…</div>`;

  try {
    const mod = await import("./explain.js");
    const { info, from } = await mod.explain(card.word, { apiKey: prefs.read().anthropic });
    // The card may have moved on while this was in flight.
    if (!current || current.id !== card.id) return;
    panel.innerHTML = mod.render(info, from) ||
      `<div class="waiting">Nothing more to add about this one.</div>`;
  } catch (err) {
    if (!current || current.id !== card.id) return;
    panel.innerHTML = `<div class="waiting">${err.message || String(err)}</div>`;
  }
}

$("explain-btn").addEventListener("click", () => current && showWhy(current));
$("next-btn").addEventListener("click", moveOn);

// ---- progress ------------------------------------------------------------
// Two questions a learner actually has: how much work is coming, and whether
// any of it is sticking. Both are computed from what is already stored, so
// nothing extra has to be tracked to answer them.
async function openStats() {
  show("stats");
  const s = await import("./stats.js");
  const reviews = await store.history();

  const r = s.retention(reviews);
  $("s-retention").textContent = r.pct === null ? "—" : `${r.pct}%`;
  $("s-retention-note").textContent = r.seen
    ? `${r.held} of ${r.seen} cards that were already learned came back and still were. ` +
      `Cards still in learning are left out — counting them makes this look worse on the days you study hardest.`
    : "Retention needs cards you have reviewed at least twice. Keep going and it will appear.";

  $("s-streak").textContent = s.streak(reviews);

  const done = s.activity(reviews, 30);
  const total = done.reduce((n, b) => n + b.count, 0);
  $("s-done").textContent = total;

  const f = s.forecast(cache, 30);
  $("s-forecast").innerHTML = s.bars(f.buckets, { label: "Cards due each day for the next 30 days", highlightFirst: true });
  const ahead = f.buckets.reduce((n, b) => n + b.count, 0);
  $("s-forecast-note").textContent = f.overdue
    ? `${f.overdue} already owed, shown in red on today. ${ahead} cards come back within the month.`
    : `${ahead} cards come back within the month. Nothing is overdue.`;

  $("s-activity").innerHTML = s.bars(done, { label: "Reviews you answered each day for the last 30 days" });
  const active = done.filter((b) => b.count > 0).length;
  $("s-activity-note").textContent = total
    ? `${total} reviews across ${active} day${active === 1 ? "" : "s"}.`
    : "Nothing reviewed in this browser yet.";

  const m = s.maturity(cache);
  const rows = [
    ["Mature", m.mature, "settled — three weeks or more between reviews"],
    ["Young", m.young, "learned, but still coming back often"],
    ["Learning", m.learning, "in the middle of being learned"],
    ["New", m.fresh, "never reviewed"],
  ];
  $("s-maturity").innerHTML = rows
    .map(([name, n, what]) =>
      `<tr><td>${name}<span class="what">${what}</span></td><td>${n}</td></tr>`)
    .join("");
}

// ---- watching ------------------------------------------------------------
let watchMounted = false;

async function openWatchScreen() {
  const mod = await import("./watch.js");
  if (!watchMounted) {
    mod.mountWatch({
      cards: () => cache,
      apiKey: () => prefs.read().anthropic,
      add: (prefill) => openAdd(prefill),
    });
    watchMounted = true;
  }
  show("watch");
  mod.openWatch();
}

$("watch-btn").addEventListener("click", openWatchScreen);
$("watch-close").addEventListener("click", goHome);

$("stats-btn").addEventListener("click", openStats);
$("stats-close").addEventListener("click", goHome);

// ---- sync ----------------------------------------------------------------
// Cards are merged per card, by when each was actually changed, so a review
// answered on the tablet at nine beats one answered on the phone at eight no
// matter which device syncs first. Nothing is deleted by a sync: a card the
// other device has and this one does not simply arrives.
let syncing = false;

$("anthropic").addEventListener("change", (e) => prefs.write({ anthropic: e.target.value.trim() }));

for (const id of ["gh-token", "gh-owner", "gh-repo"]) {
  $(id).addEventListener("change", (e) => {
    const key = { "gh-token": "ghToken", "gh-owner": "ghOwner", "gh-repo": "ghRepo" }[id];
    prefs.write({ [key]: e.target.value.trim() });
  });
}

$("sync-btn").addEventListener("click", async () => {
  if (syncing) return;
  const settings = prefs.read();
  const token = $("gh-token").value.trim();
  const owner = $("gh-owner").value.trim();
  const repo = $("gh-repo").value.trim();
  const say = (text) => { $("sync-note").textContent = text; };

  if (!token || !owner || !repo) {
    return say("Fill in the token, owner and repository first.");
  }
  prefs.write({ ghToken: token, ghOwner: owner, ghRepo: repo });

  syncing = true;
  $("sync-btn").disabled = true;
  try {
    const { github, sync } = await import("./sync.js");
    const cards = await store.allCards();
    const reviews = await store.history();
    const result = await sync(github({ token, owner, repo }), { cards, reviews }, say);

    // Write the merged state back before reporting success: a sync that says it
    // worked while this device still holds the old cards is a lie you only find
    // out about on the next review.
    await store.putCards(result.cards);
    await store.putLog(result.reviews);

    const d = result.detail;
    say(`Synced with ${owner}/${repo}. ${result.cards.length} cards, ${result.reviews.length} reviews. ` +
        (d.taken ? `${d.taken} newer from the other device, ` : "") +
        (d.kept ? `${d.kept} newer here, ` : "") +
        `${d.added} only here.`);
    await goHome();
  } catch (err) {
    say(err.message || String(err));
  } finally {
    syncing = false;
    $("sync-btn").disabled = false;
  }
});

// ---- browsing ------------------------------------------------------------
// An app you can add to but never correct is a trap: the typos already in this
// collection came in with it, and a card typed wrong at 1am is worse than no
// card, because it gets reviewed a hundred times.
const FOUND_LIMIT = 60;

function openBrowse() {
  $("find").value = "";
  renderResults("");
  show("browse");
  $("find").focus();
}

function matches(card, needle) {
  if (!needle) return true;
  return (card.word + " " + (card.hook || "") + " " + (card.clue || "") + " " + (card.example || ""))
    .toLowerCase().includes(needle);
}

function renderResults(needle) {
  const list = $("results");
  list.innerHTML = "";
  const hits = cache.filter((c) => matches(c, needle));

  $("found-note").textContent = !hits.length
    ? (needle ? `Nothing matches "${needle}".` : "No cards yet.")
    : hits.length > FOUND_LIMIT
      ? `${hits.length} match — showing the first ${FOUND_LIMIT}.`
      : `${hits.length} card${hits.length === 1 ? "" : "s"}.`;

  for (const card of hits.slice(0, FOUND_LIMIT)) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "hit";
    b.innerHTML = `<span class="w"></span><span class="c"></span><span class="m"></span>`;
    b.querySelector(".w").textContent = card.word;
    b.querySelector(".c").textContent = card.clue || card.example || "";
    // What the scheduler thinks of it, which is the thing you came to check.
    const due = new Date(card.fsrs.due);
    const when = card.fsrs.state === State.New ? "new"
      : due <= new Date() ? "due now"
      : `in ${intervalLabel(due)}`;
    b.querySelector(".m").innerHTML =
      `${card.deck} · <span class="pip">${when}</span>` +
      (card.fsrs.reps ? ` · ${card.fsrs.reps} reviews` : "") +
      (card.fsrs.lapses ? ` · ${card.fsrs.lapses} lapses` : "");
    b.addEventListener("click", () => openEdit(card.id));
    list.appendChild(b);
  }
}

$("browse-btn").addEventListener("click", openBrowse);
$("browse-close").addEventListener("click", goHome);

let findTimer = null;
$("find").addEventListener("input", (e) => {
  clearTimeout(findTimer);
  const needle = e.target.value.trim().toLowerCase();
  // 1,177 cards filter faster than a keystroke, but the render does not.
  findTimer = setTimeout(() => renderResults(needle), 120);
});

// ---- editing one card ----------------------------------------------------
let editing = null;

function openEdit(id) {
  const card = cache.find((c) => c.id === id);
  if (!card) return;
  editing = card;

  $("e-word").value = card.word;
  $("e-hook").value = card.hook || "";
  $("e-clue").value = card.clue || "";
  $("e-example").value = card.example || "";
  $("e-deck").value = card.deck;
  $("edit-note").textContent = "";

  const due = new Date(card.fsrs.due);
  $("edit-state").textContent = card.fsrs.state === State.New
    ? "Never reviewed."
    : `${card.fsrs.reps} review${card.fsrs.reps === 1 ? "" : "s"}, ` +
      `${card.fsrs.lapses} lapse${card.fsrs.lapses === 1 ? "" : "s"}, ` +
      `next ${due <= new Date() ? "now" : "in " + intervalLabel(due)}. ` +
      `Editing the wording leaves all of that alone.`;

  show("edit");
}

$("edit-close").addEventListener("click", openBrowse);

$("edit-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!editing) return;
  const word = $("e-word").value.trim();
  if (!word) return;

  // Only the wording changes. Scheduling is evidence about how well this word
  // is known, and fixing a typo is not evidence about anything.
  const updated = {
    ...editing,
    word,
    hook: $("e-hook").value.trim(),
    clue: $("e-clue").value.trim(),
    example: $("e-example").value.trim(),
    deck: $("e-deck").value.trim() || editing.deck,
  };

  await store.saveCard(updated);
  const i = cache.findIndex((c) => c.id === updated.id);
  if (i >= 0) cache[i] = updated;
  editing = updated;
  $("edit-note").textContent = "Saved.";
});

$("edit-delete").addEventListener("click", async () => {
  if (!editing) return;
  if (!confirm(`Delete "${editing.word}"? Its review history goes with it, and that cannot be undone.`)) return;
  await store.deleteCard(editing.id);
  cache = cache.filter((c) => c.id !== editing.id);
  editing = null;
  openBrowse();
});

// ---- adding a card -------------------------------------------------------
// Typed here, saved here, due immediately. No network, so it works on a train
// and a card added offline is not a card lost.
// A card can arrive here empty, from the + button, or already half-written,
// from a word tapped in a transcript — in which case the sentence it was said
// in comes with it, which is the whole point of mining from a show.
function openAdd(prefill = null) {
  const list = $("deck-options");
  list.innerHTML = "";
  for (const name of [...new Set(cache.map((c) => c.deck))].sort()) {
    const opt = document.createElement("option");
    opt.value = name;
    list.appendChild(opt);
  }
  $("a-word").value = prefill?.word || "";
  $("a-hook").value = "";
  $("a-clue").value = "";
  $("a-example").value = prefill?.example || "";
  $("a-deck").value = prefs.read().deck || (cache[0] && cache[0].deck) || "Default";
  $("add-note").textContent = prefill?.source
    ? `From ${prefill.source}${prefill.at ? ` at ${Math.floor(prefill.at / 60)}:${String(Math.floor(prefill.at % 60)).padStart(2, "0")}` : ""}.`
    : "";
  cameFrom = prefill ? "watch" : "home";
  show("add");
  $(prefill?.word ? "a-clue" : "a-word").focus();
}

// Where "close" and "added" should go back to.
let cameFrom = "home";

$("add-btn").addEventListener("click", () => openAdd());
$("add-close").addEventListener("click", () => (cameFrom === "watch" ? openWatchScreen() : goHome()));

$("add-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const word = $("a-word").value.trim();
  if (!word) return;

  // A card typed twice is a card reviewed twice for no reason, so a repeat of
  // a word already in the deck is refused rather than quietly duplicated.
  const already = cache.find((c) => c.word.toLowerCase() === word.toLowerCase());
  if (already) {
    $("add-note").textContent = `"${already.word}" is already in ${already.deck}. Nothing was added.`;
    return;
  }

  const card = {
    // Not a guid from Anki, so it is marked as ours and cannot collide with one.
    id: `lexis-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`,
    guid: "",
    deck: $("a-deck").value.trim() || "Default",
    word,
    hook: $("a-hook").value.trim(),
    clue: $("a-clue").value.trim(),
    example: $("a-example").value.trim(),
    audio: "",
    tags: ["lexis"],
    notetype: "",
    created: new Date().toISOString().slice(0, 10),
    fsrs: {
      due: new Date().toISOString(),
      stability: 0, difficulty: 0, elapsed_days: 0, scheduled_days: 0,
      learning_steps: 0, reps: 0, lapses: 0, state: State.New, last_review: null,
    },
    imported: { interval_days: 0, ease_pct: 0, had_fsrs_state: false },
    reviewedHere: 0,
  };

  await store.saveCard(card);
  cache.push(card);

  // Mined from a transcript: go straight back to it, and to a line where that
  // word is no longer one of the ones you do not have.
  if (cameFrom === "watch") {
    const said = `"${card.word}" added to ${card.deck}. It is due now.`;
    await openWatchScreen();
    $("w-note").textContent = said;
    return;
  }

  $("add-form").reset();
  $("a-deck").value = card.deck;
  $("a-word").focus();
  $("add-note").textContent = `"${card.word}" added to ${card.deck}. It is due now.`;
});

// ---- getting your cards back out -----------------------------------------
// Whatever else this app is, it must never be a place data goes into and
// cannot come out of. Two ways out: everything, losslessly, for coming back
// here; and a CSV Anki imports, for leaving.
function download(name, body, type) {
  // Text from the JSON and CSV exports, an already-built Blob from the .apkg.
  const url = URL.createObjectURL(body instanceof Blob ? body : new Blob([body], { type }));
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function stamp() {
  return new Date().toISOString().slice(0, 10);
}

$("export-json-btn").addEventListener("click", async () => {
  const cards = await store.allCards();
  const log = await store.history();
  download(`lexis-backup-${stamp()}.json`, JSON.stringify({
    exported_at: new Date().toISOString(),
    version: VERSION,
    card_count: cards.length,
    review_count: log.length,
    cards,
    // The log goes too: it is what lets a schedule be rebuilt from nothing.
    reviews: log,
  }, null, 1), "application/json");
  $("settings-note").textContent = `${cards.length} cards and ${log.length} reviews saved to your downloads.`;
});

// A deck you cannot get back out is a deck you are renting. This writes the
// real thing — every card, deck, tag and, unlike a CSV, every card's schedule
// and the whole review history — in the container every Anki ever shipped
// reads. The guids travel with it, so importing this into the collection it
// came from updates those notes rather than doubling them.
$("export-apkg-btn").addEventListener("click", async () => {
  const button = $("export-apkg-btn");
  if (button.disabled) return;
  button.disabled = true;
  const say = (text) => { $("settings-note").textContent = text; };
  try {
    const [cards, log, mod] = await Promise.all([
      store.allCards(), store.history(), import("./apkgout.js"),
    ]);
    const blob = await mod.buildApkg(cards, log, { onProgress: say });
    download(`lexis-${stamp()}.apkg`, blob, "application/octet-stream");
    const sounds = cards.filter((c) => c.audio).length;
    say(`${cards.length} cards and ${log.length} reviews written as an .apkg, ` +
      `with their decks, tags and scheduling. In Anki: File → Import, and turn on ` +
      `“Import any learning progress” — left off, Anki resets every card to new on purpose. ` +
      (sounds ? `The ${sounds} audio references point at files already in that collection, ` +
        `so the mp3s are not in here and it stays small. ` : "") +
      `The note ids travel too, so importing this back into the collection these came from ` +
      `updates those notes rather than doubling them.`);
  } catch (err) {
    say(err.message || String(err));
  } finally {
    button.disabled = false;
  }
});

$("export-csv-btn").addEventListener("click", async () => {
  const cards = await store.allCards();
  const cell = (v) => {
    const text = String(v ?? "");
    return /[",\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
  };
  // Anki maps a CSV onto whatever notetype it picks, and it picks Basic, which
  // has two fields. Four columns of content therefore does not give you four
  // fields — the extras spill into tags, which is exactly what happened the
  // first time this was tested: every example sentence arrived as nine tags.
  //
  // So it is written the way Anki actually reads it. Two field columns onto
  // Basic's two fields, and the deck and tags columns declared by position so
  // they land where they are meant to rather than being guessed at.
  const lines = [
    "#separator:Comma",
    "#html:true",
    "#notetype:Basic",
    "#columns:Front,Back,Tags,Deck",
    "#tags column:3",
    "#deck column:4",
  ];
  const esc = (t) => String(t || "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  for (const c of cards) {
    // The answer keeps its parts, stacked, because a CSV has nowhere else to
    // put them and losing the example would be worse than losing the structure.
    const back = [
      esc(c.word),
      c.hook ? `<i>${esc(c.hook)}</i>` : "",
      c.example ? `<br>${esc(c.example)}` : "",
    ].filter(Boolean).join("<br>");
    lines.push([esc(c.clue), back, (c.tags || []).join(" "), c.deck].map(cell).join(","));
  }
  download(`lexis-for-anki-${stamp()}.csv`, lines.join("\n") + "\n", "text/csv");
  $("settings-note").textContent =
    `${cards.length} cards written as CSV, with their decks and tags. Anki reads the header, ` +
    `so File → Import needs nothing set by hand. Scheduling cannot travel in a CSV — ` +
    `the .apkg export carries that, and so does the JSON backup.`;
});

// ---- importing an .apkg --------------------------------------------------
// The file never leaves the phone: it is unzipped, decompressed and read in
// this tab. Nothing is uploaded, so nothing has to be trusted with it.
let importing = false;

function pickApkg(where) {
  if (importing) return;
  $("apkg-file").dataset.report = where;
  $("apkg-file").click();
}

$("apkg-btn").addEventListener("click", () => pickApkg("settings-note"));
$("apkg-boot-btn").addEventListener("click", () => pickApkg("boot-detail"));

$("apkg-file").addEventListener("change", async (event) => {
  const file = event.target.files && event.target.files[0];
  event.target.value = "";                       // so the same file can be picked twice
  if (!file) return;

  const say = (text) => { const el = $($("apkg-file").dataset.report || "settings-note"); if (el) el.textContent = text; };
  importing = true;
  say("Reading…");

  try {
    // Loaded only when an import actually happens: the SQLite engine is the
    // better part of a megabyte and most sessions never open a file.
    const { readApkg } = await import("./apkg.js");
    const deck = await readApkg(file, say);
    const result = await store.importDeck(deck);
    const s = deck.scheduling;
    say(`${result.added} added, ${result.refreshed} refreshed from ${file.name}. ` +
        `${s.carried_fsrs_state} kept their FSRS state, ${s.converted_from_ease} were converted ` +
        `from interval and ease, ${s.new} are new.` +
        (deck.skipped ? ` ${deck.skipped} skipped (no word, or a duplicate of another card).` : ""));
    await goHome();
  } catch (err) {
    say(err.message || String(err));
  } finally {
    importing = false;
  }
});

for (const b of document.querySelectorAll(".grade")) {
  b.addEventListener("click", () => grade(Number(b.dataset.rating)));
}

$("retention").addEventListener("input", (e) => {
  const v = Number(e.target.value);
  $("retention-out").textContent = `${Math.round(v * 100)}%`;
  prefs.write({ retention: v });
  engine = scheduler(v);
});
$("new-per-day").addEventListener("change", (e) => {
  prefs.write({ newPerDay: Math.max(0, Number(e.target.value) || 0) });
  goHome();
});
$("media-base").addEventListener("change", (e) => prefs.write({ mediaBase: e.target.value.trim() }));

$("refresh-btn").addEventListener("click", async () => {
  $("settings-note").textContent = "Checking…";
  const r = await tryImport();
  if (r.ok) await goHome(); else $("settings-note").textContent = r.why;
});

$("reset-btn").addEventListener("click", async () => {
  // Destructive and irreversible, so it asks — and says exactly what goes.
  if (!confirm("Erase every card and review recorded in this browser? The deck can be imported again, but the scheduling you have earned here cannot be recovered.")) return;
  await store.wipe();
  location.reload();
});

// Keyboard, for a tablet with a case keyboard: space reveals, 1-4 grade.
document.addEventListener("keydown", (e) => {
  if ($("screen-review").hidden) return;
  if (e.key === " " || e.key === "Enter") {
    e.preventDefault();
    if (!$("reveal-btn").hidden) reveal();
    else if (!$("next-btn").hidden) moveOn();
    return;
  }
  if (!$("grades").hidden && ["1", "2", "3", "4"].includes(e.key)) {
    e.preventDefault();
    grade(Number(e.key));
  }
});

// A service worker makes a stale copy *more* likely than a plain page, not
// less: it is designed to keep serving what it already has. This one takes over
// as soon as it installs, but the tab that is already open goes on running the
// old code until it is reloaded — which is the gap a learner falls into,
// reporting a bug that was fixed days ago. So the reload is automatic, and
// guarded so a worker that keeps re-activating cannot put the app in a loop.
if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("sw.js").catch(() => { /* offline is a bonus, not a requirement */ });

  let reloading = false;
  navigator.serviceWorker.addEventListener("controllerchange", () => {
    if (reloading) return;
    // The first worker to take control of a page that had none is the initial
    // install, not an update. Reloading there would bounce every first visit.
    if (!sessionStorage.getItem("lexis:controlled")) {
      sessionStorage.setItem("lexis:controlled", "1");
      return;
    }
    reloading = true;
    location.reload();
  });
  if (navigator.serviceWorker.controller) sessionStorage.setItem("lexis:controlled", "1");
}

boot();
