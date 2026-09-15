// The scheduler, and the rules about what to show next.
//
// FSRS decides *when* a card comes back. It does not decide what order today's
// cards arrive in, how many new ones to introduce, or when to stop - and those
// are what a session actually feels like. They live here.

import { fsrs, generatorParameters, Rating, State, createEmptyCard } from "./vendor/ts-fsrs.mjs";

export { Rating, State };

// Desired retention is the one knob worth exposing: it is the promise the
// scheduler makes about how much you will remember. 0.9 is the standard
// trade - higher means more reviews for less forgetting.
export const DEFAULTS = {
  retention: 0.9,
  newPerDay: 15,
  maxPerSession: 60,
};

export function scheduler(retention = DEFAULTS.retention) {
  return fsrs(generatorParameters({
    request_retention: retention,
    enable_fuzz: true,        // so cards introduced together don't return together forever
    enable_short_term: true,
  }));
}

// ts-fsrs wants real Dates on the card; the store holds ISO strings because
// IndexedDB round-trips those without surprises across browsers.
// FSRS refuses a reviewed card whose stability is under a day or whose
// difficulty is under 1 - not unreasonably, since neither describes a memory.
// A deck can still arrive carrying one, from an older build or a hand-edited
// file, and one bad row should not take the session down with it.
const S_MIN = 1, D_MIN = 1, D_MAX = 10;

export function toFsrs(card) {
  const f = card.fsrs;
  if (!f || f.state === undefined) return createEmptyCard();
  const reviewed = f.state !== 0;
  return {
    due: new Date(f.due),
    stability: reviewed ? Math.max(S_MIN, f.stability || 0) : (f.stability || 0),
    difficulty: reviewed ? Math.min(D_MAX, Math.max(D_MIN, f.difficulty || D_MIN)) : (f.difficulty || 0),
    elapsed_days: f.elapsed_days || 0,
    scheduled_days: f.scheduled_days || 0,
    learning_steps: f.learning_steps || 0,
    reps: f.reps || 0,
    lapses: f.lapses || 0,
    state: f.state,
    last_review: f.last_review ? new Date(f.last_review) : undefined,
  };
}

export function fromFsrs(c) {
  return {
    due: c.due.toISOString(),
    stability: c.stability,
    difficulty: c.difficulty,
    elapsed_days: c.elapsed_days,
    scheduled_days: c.scheduled_days,
    learning_steps: c.learning_steps || 0,
    reps: c.reps,
    lapses: c.lapses,
    state: c.state,
    last_review: c.last_review ? new Date(c.last_review).toISOString() : null,
  };
}

// What each button will do to this card, so the buttons can say it rather than
// making you learn what "Hard" means by watching it happen.
export function preview(engine, card, now = new Date()) {
  const scheduled = engine.repeat(toFsrs(card), now);
  const out = {};
  for (const rating of [Rating.Again, Rating.Hard, Rating.Good, Rating.Easy]) {
    const item = scheduled[rating];
    if (item) out[rating] = intervalLabel(item.card.due, now);
  }
  return out;
}

export function intervalLabel(due, now = new Date()) {
  const minutes = Math.round((new Date(due) - now) / 60000);
  if (minutes < 1) return "now";
  if (minutes < 60) return `${minutes}m`;
  const hours = minutes / 60;
  if (hours < 24) return `${Math.round(hours)}h`;
  const days = hours / 24;
  if (days < 30) return `${Math.round(days)}d`;
  const months = days / 30.44;
  if (months < 12) return `${months.toFixed(months < 3 ? 1 : 0)}mo`;
  return `${(days / 365.25).toFixed(1)}y`;
}

export function answer(engine, card, rating, now = new Date()) {
  const item = engine.next(toFsrs(card), now, rating);
  return {
    card: {
      ...card,
      fsrs: fromFsrs(item.card),
      reviewedHere: (card.reviewedHere || 0) + 1,
    },
    log: {
      at: now.toISOString(),
      id: card.id,
      word: card.word,
      rating,
      state: item.log.state,
      elapsed_days: item.log.elapsed_days,
      scheduled_days: item.card.scheduled_days,
      stability: round(item.card.stability),
      difficulty: round(item.card.difficulty),
    },
  };
}

function round(n) {
  return typeof n === "number" ? Math.round(n * 1000) / 1000 : n;
}

// Today's queue.
//
// Due cards come first and oldest-due first, because a card three weeks overdue
// is the one actually rotting. New cards are rationed - introducing everything
// at once is how a deck becomes a wall a week later and stops being opened.
export function queue(cards, { now = new Date(), deck = "", limit = DEFAULTS.maxPerSession,
                               newPerDay = DEFAULTS.newPerDay, introducedToday = 0 } = {}) {
  const pool = deck ? cards.filter((c) => c.deck === deck) : cards;
  const stamp = now.getTime();

  const due = pool
    .filter((c) => c.fsrs.state !== State.New && new Date(c.fsrs.due).getTime() <= stamp)
    .sort((a, b) => new Date(a.fsrs.due) - new Date(b.fsrs.due));

  const fresh = pool
    .filter((c) => c.fsrs.state === State.New)
    .slice(0, Math.max(0, newPerDay - introducedToday));

  // New cards are folded in rather than queued behind: a session of nothing but
  // review is a grind, and a session of nothing but new is not learning.
  const out = [];
  const every = fresh.length ? Math.max(2, Math.floor(due.length / (fresh.length + 1))) : 0;
  let f = 0;
  due.forEach((card, i) => {
    out.push(card);
    if (every && f < fresh.length && (i + 1) % every === 0) out.push(fresh[f++]);
  });
  while (f < fresh.length) out.push(fresh[f++]);

  return out.slice(0, limit);
}

export function counts(cards, now = new Date()) {
  const stamp = now.getTime();
  let due = 0, fresh = 0, learning = 0, known = 0;
  for (const c of cards) {
    if (c.fsrs.state === State.New) { fresh += 1; continue; }
    if (c.fsrs.state === State.Learning || c.fsrs.state === State.Relearning) learning += 1;
    if (new Date(c.fsrs.due).getTime() <= stamp) due += 1;
    else known += 1;
  }
  return { due, fresh, learning, known, total: cards.length };
}
