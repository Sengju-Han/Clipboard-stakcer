// Lexis — the review loop.
//
// The whole app runs from IndexedDB. The network is touched exactly twice: once
// to fetch a deck that is newer than the one already here, and never during a
// session. Answering a card is a local write, so it stays instant on a train.

import * as store from "./store.js";
import { scheduler, queue, counts, preview, answer, intervalLabel, Rating, State, DEFAULTS }
  from "./review.js";

const VERSION = "2026-09-15.3";
const DECK_URL = "../deck/deck.json";

const $ = (id) => document.getElementById(id);
const screens = ["boot", "home", "review", "done"];

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
