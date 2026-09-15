// Subtitles, from whatever file you happen to have.
//
// Three formats cover effectively everything a learner ends up with: .srt from
// almost anywhere, .vtt from the web and from YouTube, .ass from fansubs and
// from anime. They disagree about nearly everything except the one thing that
// matters — a start, an end, and some words — so all three are parsed down to
// that and nothing else in the app has to know which one it came from.

const SRT_TIME = /(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})/;
const ASS_TIME = /(\d{1,2}):(\d{2}):(\d{2})[.:](\d{1,2})/;

function seconds(h, m, s, frac) {
  const f = frac ? Number(`0.${String(frac).padEnd(3, "0").slice(0, 3)}`) : 0;
  return Number(h) * 3600 + Number(m) * 60 + Number(s) + f;
}

// What is left after the markup: WebVTT's <c> and <v> tags, its inline karaoke
// timestamps, ASS override blocks, and the HTML that .srt files smuggle in.
function clean(text) {
  return String(text || "")
    .replace(/\{\\[^}]*\}/g, "")              // ASS: {\pos(12,34)}
    .replace(/<\d{2}:\d{2}:\d{2}[.,]\d{3}>/g, "")   // VTT karaoke timing
    .replace(/<[^>]*>/g, "")                  // <c>, <v Name>, <i>, <b>
    .replace(/\\[Nnh]/g, " ")                 // ASS line breaks
    .replace(/&nbsp;/g, " ").replace(/&amp;/g, "&")
    .replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&quot;/g, '"')
    .replace(/\s+/g, " ")
    .trim();
}

function parseSrt(text) {
  const cues = [];
  // Blank-line separated blocks, tolerant of \r\n and of missing index lines.
  for (const block of text.replace(/\r/g, "").split(/\n{2,}/)) {
    const lines = block.split("\n").filter((l) => l.trim() !== "");
    if (!lines.length) continue;
    const at = lines.findIndex((l) => l.includes("-->"));
    if (at < 0) continue;
    const [from, to] = lines[at].split("-->").map((p) => p.trim());
    const a = SRT_TIME.exec(from), b = SRT_TIME.exec(to);
    if (!a || !b) continue;
    const body = clean(lines.slice(at + 1).join("\n"));
    if (body) cues.push({ start: seconds(a[1], a[2], a[3], a[4]), end: seconds(b[1], b[2], b[3], b[4]), text: body });
  }
  return cues;
}

function parseAss(text) {
  const cues = [];
  let order = null;
  for (const raw of text.replace(/\r/g, "").split("\n")) {
    const line = raw.trim();
    if (/^Format\s*:/i.test(line) && order === null && /start/i.test(line)) {
      order = line.slice(line.indexOf(":") + 1).split(",").map((f) => f.trim().toLowerCase());
      continue;
    }
    if (!/^Dialogue\s*:/i.test(line)) continue;
    const fields = line.slice(line.indexOf(":") + 1).split(",");
    // Text is always last in the Format line and may itself contain commas, so
    // the tail is rejoined rather than indexed.
    const names = order || ["layer", "start", "end", "style", "name", "marginl", "marginr", "marginv", "effect", "text"];
    const iStart = names.indexOf("start"), iEnd = names.indexOf("end"), iText = names.indexOf("text");
    if (iStart < 0 || iEnd < 0 || iText < 0) continue;
    const a = ASS_TIME.exec(fields[iStart] || ""), b = ASS_TIME.exec(fields[iEnd] || "");
    if (!a || !b) continue;
    const body = clean(fields.slice(iText).join(","));
    if (body) cues.push({ start: seconds(a[1], a[2], a[3], a[4]), end: seconds(b[1], b[2], b[3], b[4]), text: body });
  }
  return cues;
}

// YouTube's automatic captions roll: each cue repeats the tail of the one
// before it so the text scrolls. Left alone that turns a ten-word sentence into
// ten near-identical cues and makes the transcript unreadable, so a cue whose
// text the previous one already ends with replaces it rather than following it.
function unroll(cues) {
  const out = [];
  for (const cue of cues) {
    const last = out[out.length - 1];
    if (last && (last.text === cue.text || cue.text.startsWith(last.text))) {
      last.text = cue.text;
      last.end = cue.end;
      continue;
    }
    out.push({ ...cue });
  }
  return out;
}

// YouTube's own transcript panel, copied. This matters more than it looks:
// captions cannot be fetched from a browser — YouTube refuses the request from
// anywhere but itself — and Android Chrome runs no extensions, so for someone
// with only a phone this is the way in. Open the video, Show transcript, select
// all, copy, paste here. The format is a timestamp on its own line and the text
// under it.
const STAMP_LINE = /^(\d{1,2}:)?\d{1,2}:\d{2}$/;

function parseTranscript(text) {
  const lines = text.replace(/\r/g, "").split("\n").map((l) => l.trim());
  const cues = [];
  for (let i = 0; i < lines.length; i += 1) {
    if (!STAMP_LINE.test(lines[i])) continue;
    const parts = lines[i].split(":").map(Number);
    const start = parts.length === 3
      ? parts[0] * 3600 + parts[1] * 60 + parts[2]
      : parts[0] * 60 + parts[1];
    const body = [];
    while (i + 1 < lines.length && !STAMP_LINE.test(lines[i + 1])) {
      i += 1;
      if (lines[i]) body.push(lines[i]);
    }
    const said = clean(body.join(" "));
    if (said) cues.push({ start, end: start + 4, text: said });
  }
  // An end that runs to the next line's start, so following along works.
  cues.forEach((cue, i) => { if (cues[i + 1]) cue.end = Math.max(cue.start + 0.5, cues[i + 1].start); });
  return cues;
}

// Prose with no timings at all — a transcript from a podcast page, a paragraph
// out of a book, a paste from anywhere. There is nothing to sync to, so it is
// split into sentences and the timestamps are marked as made up, which is what
// hides them in the transcript.
function parseProse(text) {
  const flat = clean(text.replace(/\n+/g, " "));
  const sentences = flat.match(/[^.!?…]+[.!?…]*/g) || [];
  return sentences
    .map((raw) => raw.trim())
    .filter((line) => line.length > 1)
    .map((line, i) => ({ start: i * 3, end: i * 3 + 2.5, text: line, synthetic: true }));
}

export function parseSubtitles(text, name = "") {
  const body = String(text || "");
  const looksAss = /^\s*\[Script Info\]/im.test(body) || /^Dialogue\s*:/im.test(body);
  let cues = looksAss ? parseAss(body) : parseSrt(body);
  if (!cues.length) cues = parseTranscript(body);
  if (!cues.length) cues = parseProse(body);
  const ready = unroll(cues.filter((c) => c.end > c.start && c.text))
    .sort((a, b) => a.start - b.start);
  if (!ready.length) {
    throw new Error(name
      ? `Nothing readable in ${name}. It should be a .srt, .vtt or .ass file, ` +
        `a copied transcript, or just text.`
      : "There were no words in that.");
  }
  return ready;
}

// Which cue is on screen at a given moment. Binary search, because this is
// called on every timeupdate and a two-hour film is thousands of cues.
export function cueAt(cues, time) {
  let lo = 0, hi = cues.length - 1, best = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (cues[mid].start <= time) { best = mid; lo = mid + 1; } else { hi = mid - 1; }
  }
  if (best < 0) return -1;
  // A gap between cues is silence, not the previous line still standing.
  return time <= cues[best].end + 0.4 ? best : -1;
}

export function clock(seconds) {
  const s = Math.max(0, Math.floor(seconds));
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), r = s % 60;
  const pad = (n) => String(n).padStart(2, "0");
  return h ? `${h}:${pad(m)}:${pad(r)}` : `${m}:${pad(r)}`;
}
