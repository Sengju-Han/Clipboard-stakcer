// What the deck is actually doing.
//
// Two questions a learner genuinely has, and most apps answer neither well:
// how much work is coming, and am I actually remembering any of it. Everything
// here is computed from what is already stored — the cards for the forecast,
// the append-only review log for the rest — so nothing extra has to be tracked.

import { ankiDay } from "./review.js";

const DAY = 86400000;

// One definition of "a day" for the whole app. The scheduler hands a card over
// when Anki's day containing its due date has arrived, and Anki's day starts at
// four in the morning; a chart that drew its buckets from midnight would
// disagree with the queue for four hours every night, and put a card the
// session will not give you until tomorrow into today's column.
export const startOfDay = ankiDay;

// How much work is coming. Counted from each card's own due date, so it is the
// same number the review screen will hand you on the day.
export function forecast(cards, days = 30, now = Date.now()) {
  const today = startOfDay(now);
  const buckets = Array.from({ length: days }, (_, i) => ({ day: today + i * DAY, count: 0 }));
  let overdue = 0;

  for (const card of cards) {
    if (card.fsrs.state === 0) continue;           // never reviewed; not owed yet
    const due = startOfDay(new Date(card.fsrs.due).getTime());
    if (due <= today) { overdue += 1; buckets[0].count += 1; continue; }
    const i = Math.round((due - today) / DAY);
    if (i < days) buckets[i].count += 1;
  }
  return { buckets, overdue };
}

// What you have actually done. One bar per day from the review log.
export function activity(reviews, days = 30, now = Date.now()) {
  const today = startOfDay(now);
  const first = today - (days - 1) * DAY;
  const buckets = Array.from({ length: days }, (_, i) => ({ day: first + i * DAY, count: 0 }));

  for (const entry of reviews) {
    const when = startOfDay(new Date(entry.at).getTime());
    if (when < first || when > today) continue;
    buckets[Math.round((when - first) / DAY)].count += 1;
  }
  return buckets;
}

// Retention: of the cards that were already learned and came back, how many
// did you still know. Cards in learning are excluded deliberately — counting
// them makes the number look worse on the days you study hardest, which is
// exactly backwards.
export function retention(reviews) {
  let seen = 0, held = 0;
  for (const entry of reviews) {
    if (entry.state !== 2) continue;               // 2 = Review, per ts-fsrs
    seen += 1;
    if (entry.rating > 1) held += 1;               // anything but Again
  }
  return { seen, held, pct: seen ? Math.round((held / seen) * 100) : null };
}

// Consecutive days ending today (or yesterday — a streak should survive until
// the day is actually over, not break the moment midnight passes).
export function streak(reviews, now = Date.now()) {
  if (!reviews.length) return 0;
  const days = new Set(reviews.map((r) => startOfDay(new Date(r.at).getTime())));
  const today = startOfDay(now);
  let at = days.has(today) ? today : today - DAY;
  if (!days.has(at)) return 0;
  let n = 0;
  while (days.has(at)) { n += 1; at -= DAY; }
  return n;
}

export function maturity(cards, now = Date.now()) {
  const out = { fresh: 0, learning: 0, young: 0, mature: 0 };
  for (const card of cards) {
    const s = card.fsrs.state;
    if (s === 0) { out.fresh += 1; continue; }
    if (s === 1 || s === 3) { out.learning += 1; continue; }
    // 21 days is the line Anki draws between a card you are still holding on to
    // and one that has settled. Worth keeping so the number means the same
    // thing to anyone arriving from there.
    if (card.fsrs.stability >= 21) out.mature += 1; else out.young += 1;
  }
  return out;
}

// ---- drawing -------------------------------------------------------------
//
// Inline SVG, one series, one hue. A single series needs no legend — the title
// names it — and a number on every bar would be noise at thirty bars on a
// phone, so only the tallest is labelled.

const W = 320, H = 96, PAD_B = 16, PAD_T = 10;

function esc(text) {
  return String(text).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}

export function bars(buckets, { label = "", highlightFirst = false } = {}) {
  const max = Math.max(1, ...buckets.map((b) => b.count));
  const n = buckets.length;
  const slot = W / n;
  const gap = Math.min(2, slot * 0.25);            // the 2px surface gap between bars
  const width = Math.max(1.5, slot - gap);
  const plot = H - PAD_B - PAD_T;

  const peak = buckets.reduce((best, b, i) => (b.count > buckets[best].count ? i : best), 0);

  let marks = "";
  buckets.forEach((b, i) => {
    const h = b.count ? Math.max(2, (b.count / max) * plot) : 0;
    const x = i * slot + gap / 2;
    const y = PAD_T + plot - h;
    const cls = highlightFirst && i === 0 ? "bar now" : "bar";
    if (h > 0) {
      // 4px rounded data-end, anchored to the baseline.
      marks += `<rect class="${cls}" x="${x.toFixed(1)}" y="${y.toFixed(1)}" ` +
        `width="${width.toFixed(1)}" height="${h.toFixed(1)}" rx="${Math.min(2, width / 2).toFixed(1)}">` +
        `<title>${esc(new Date(b.day).toDateString())}: ${b.count}</title></rect>`;
    } else {
      // An empty day still needs a hit target, or tapping it says nothing.
      marks += `<rect class="bar empty" x="${x.toFixed(1)}" y="${PAD_T}" ` +
        `width="${width.toFixed(1)}" height="${plot}">` +
        `<title>${esc(new Date(b.day).toDateString())}: 0</title></rect>`;
    }
  });

  // The tallest bar is labelled, and only that one — a number on every bar is
  // noise at thirty bars on a phone. The anchor has to follow the position or
  // the label runs off the edge it sits against: centred at x=5 with four
  // digits, "1105" renders as "105", which is not a rounding error but a
  // different number.
  const peakCount = buckets[peak].count;
  const centre = peak * slot + slot / 2;
  const nearLeft = centre < 14;
  const nearRight = centre > W - 14;
  const peakLabel = peakCount > 0
    ? `<text class="peak" x="${(nearLeft ? 1 : nearRight ? W - 1 : centre).toFixed(1)}" ` +
      `y="${(PAD_T - 2).toFixed(1)}" ` +
      `text-anchor="${nearLeft ? "start" : nearRight ? "end" : "middle"}">${peakCount}</text>`
    : "";

  return `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(label)}" preserveAspectRatio="none">
    <line class="axis" x1="0" y1="${H - PAD_B}" x2="${W}" y2="${H - PAD_B}"></line>
    ${marks}${peakLabel}
  </svg>`;
}
