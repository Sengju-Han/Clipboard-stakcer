// Watching something, and taking the words out of it.
//
// This is the part every app in this space sells and none of them get right on
// a phone with no extension: you are watching a show or listening to a podcast,
// a line goes past with one word in it you do not have, and that word should
// become a card without the video stopping being the point.
//
// What it works on is deliberately wide, because what a learner actually has is
// wide: a video file, an audio file, a subtitle file, or a subtitle file by
// itself. A transcript with no media is still worth reading — it is where half
// the mining happens — so nothing here requires a video to be present.
//
// Every word is marked against the deck as it is drawn. That is the feature:
// not "here are the subtitles", but "here are the four words in this episode
// you do not have yet", which is a different and much shorter list.

import { parseSubtitles, cueAt, clock } from "./subs.js";
import { index, read } from "./lex.js";

const PAGE = 120;          // cues drawn at a time; a film is thousands
const KNOWN_KEY = "lexis:known";

let cues = [];
let readings = [];         // one per cue, recomputed only when the deck changes
let idx = null;
let drawn = 0;
let filtered = false;
let media = null;          // the <video>/<audio> element in use
let following = true;
let currentCue = -1;
let deps = null;
let sourceName = "";

const $ = (id) => document.getElementById(id);

// ---- the words this person has said they already know ---------------------
// One tap while watching and a word stops being flagged, for good. This is the
// honest version of a frequency list: it is about them, not about English.
function knownWords() {
  try { return new Set(JSON.parse(localStorage.getItem(KNOWN_KEY) || "[]")); }
  catch { return new Set(); }
}

function rememberKnown(word) {
  const all = knownWords();
  all.add(String(word).toLowerCase());
  try { localStorage.setItem(KNOWN_KEY, JSON.stringify([...all])); } catch { /* full */ }
  return all;
}

export function forgetKnown(word) {
  const all = knownWords();
  all.delete(String(word).toLowerCase());
  try { localStorage.setItem(KNOWN_KEY, JSON.stringify([...all])); } catch { /* full */ }
}

// ---- drawing --------------------------------------------------------------

function esc(text) {
  return String(text ?? "").replace(/[&<>"]/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}

function lineHtml(cue, i) {
  const { tokens, unknown } = readings[i];
  let body = "";
  for (const t of tokens) {
    if (!t.word) { body += esc(t.text); continue; }
    const target = t.phrase || t.text;
    body += `<span class="w s-${t.state}${t.phrase ? " in-phrase" : ""}" ` +
      `data-word="${esc(target)}" data-cue="${i}">${esc(t.text)}</span>`;
  }
  // A made-up timestamp is worse than none: it invites a tap that goes nowhere.
  const when = cue.synthetic ? "" : `<button class="at" data-seek="${cue.start}">${clock(cue.start)}</button>`;
  return `<div class="line" data-cue="${i}" data-new="${unknown}">${when}` +
    `<p class="said">${body}</p></div>`;
}

function visibleCues() {
  const all = cues.map((cue, i) => ({ cue, i }));
  return filtered ? all.filter(({ i }) => readings[i].unknown === 1) : all;
}

let shown = [];

function drawMore(upTo = drawn + PAGE) {
  const target = Math.min(shown.length, Math.max(upTo, drawn));
  if (target <= drawn) return;
  const html = shown.slice(drawn, target).map(({ cue, i }) => lineHtml(cue, i)).join("");
  $("w-lines").insertAdjacentHTML("beforeend", html);
  drawn = target;
  $("w-more").hidden = drawn >= shown.length;
}

function redraw() {
  // Reading every line is the expensive part, so it happens once here rather
  // than once per draw, per filter and per tally.
  readings = cues.map((cue) => read(cue.text, idx));
  shown = visibleCues();
  drawn = 0;
  $("w-lines").innerHTML = "";
  drawMore();
  tally();
}

function tally() {
  const oneNew = readings.filter((r) => r.unknown === 1).length;
  const words = new Set();
  for (const reading of readings) {
    for (const t of reading.tokens) {
      if (t.word && t.state === "new") words.add((t.phrase || t.text).toLowerCase());
    }
  }
  $("w-tally").textContent =
    `${cues.length} lines · ${words.size} word${words.size === 1 ? "" : "s"} not in your deck · ` +
    `${oneNew} line${oneNew === 1 ? "" : "s"} with exactly one`;
}

// ---- following along ------------------------------------------------------

function markCurrent(i) {
  if (i === currentCue) return;
  const old = $("w-lines").querySelector(".line.on");
  if (old) old.classList.remove("on");
  currentCue = i;
  if (i < 0) return;
  // The cue has to exist before it can be scrolled to.
  const place = shown.findIndex((s) => s.i === i);
  if (place < 0) return;
  if (place >= drawn) drawMore(place + PAGE);
  const el = $("w-lines").querySelector(`.line[data-cue="${i}"]`);
  if (!el) return;
  el.classList.add("on");
  if (following) el.scrollIntoView({ block: "center", behavior: "smooth" });
}

function onTime() {
  if (!media || !cues.length) return;
  markCurrent(cueAt(cues, media.currentTime));
}

// ---- opening things -------------------------------------------------------

function useText(text, name) {
  cues = parseSubtitles(text, name);
  sourceName = name;
  refreshIndex();
  redraw();
  $("w-tools").hidden = false;
  const timed = cues.some((c) => !c.synthetic);
  $("w-note").textContent =
    `${cues.length} lines from ${name}. Tap any word to look at it` +
    (timed ? "; tap a timestamp to jump there." : ".");
}

async function loadSubs(file) {
  useText(await file.text(), file.name);
}

function loadMedia(file) {
  const url = URL.createObjectURL(file);
  const holder = $("w-player");
  holder.innerHTML = "";
  const audio = file.type.startsWith("audio");
  media = document.createElement(audio ? "audio" : "video");
  media.src = url;
  media.controls = true;
  media.playsInline = true;
  media.preload = "metadata";
  media.addEventListener("timeupdate", onTime);
  media.addEventListener("seeked", onTime);
  holder.appendChild(media);
  holder.hidden = false;
  $("w-note").textContent = cues.length
    ? `Playing ${file.name}. The transcript follows along.`
    : `Playing ${file.name}. Open a subtitle file to go with it.`;
}

function refreshIndex() {
  idx = index(deps.cards(), [...knownWords()]);
}

// ---- the word sheet -------------------------------------------------------

const LABEL = {
  known: "in your deck, settled",
  young: "in your deck, still coming back",
  learning: "in your deck, being learned",
  fresh: "in your deck, not met yet",
  familiar: "not a card, but it is in your own example sentences",
  common: "everyday word",
  new: "not in your deck",
};

let sheetWord = "";
let sheetCue = -1;

function openSheet(word, cueIndex, state) {
  sheetWord = word;
  sheetCue = cueIndex;
  $("w-sheet-word").textContent = word;
  $("w-sheet-state").textContent = LABEL[state] || "";
  $("w-sheet-line").textContent = cues[cueIndex] ? cues[cueIndex].text : "";
  $("w-sheet-why").innerHTML = "";
  // Adding a card for a word already in the deck would be a duplicate, and
  // saying you know a word you are studying is how a card goes missing.
  const inDeck = ["known", "young", "learning", "fresh"].includes(state);
  $("w-sheet-add").hidden = inDeck;
  $("w-sheet-known").hidden = inDeck || state === "common";
  $("w-sheet").hidden = false;
}

function closeSheet() {
  $("w-sheet").hidden = true;
  sheetWord = "";
}

// ---- wiring ---------------------------------------------------------------

export function mountWatch(hooks) {
  deps = hooks;

  $("w-media-btn").addEventListener("click", () => $("w-media-file").click());
  $("w-subs-btn").addEventListener("click", () => $("w-subs-file").click());

  $("w-media-file").addEventListener("change", (e) => {
    const file = e.target.files && e.target.files[0];
    e.target.value = "";
    if (file) loadMedia(file);
  });

  $("w-subs-file").addEventListener("change", async (e) => {
    const file = e.target.files && e.target.files[0];
    e.target.value = "";
    if (!file) return;
    try { await loadSubs(file); }
    catch (err) { $("w-note").textContent = err.message || String(err); }
  });

  $("w-paste-btn").addEventListener("click", () => {
    const row = $("w-paste-row");
    row.hidden = !row.hidden;
    if (!row.hidden) $("w-paste").focus();
  });

  $("w-paste-use").addEventListener("click", () => {
    const text = $("w-paste").value;
    try {
      useText(text, "what you pasted");
      $("w-paste-row").hidden = true;
      $("w-paste").value = "";
    } catch (err) {
      $("w-note").textContent = err.message || String(err);
    }
  });

  $("w-filter").addEventListener("click", () => {
    filtered = !filtered;
    $("w-filter").textContent = filtered ? "Showing only one-new-word lines" : "Only lines with one new word";
    $("w-filter").classList.toggle("on", filtered);
    redraw();
  });

  $("w-more").addEventListener("click", () => drawMore());

  $("w-lines").addEventListener("click", (e) => {
    const seek = e.target.closest(".at");
    if (seek) {
      const at = Number(seek.dataset.seek);
      if (media) { media.currentTime = at; media.play().catch(() => {}); }
      markCurrent(Number(seek.closest(".line").dataset.cue));
      return;
    }
    const word = e.target.closest(".w");
    if (!word) return;
    const state = [...word.classList].find((c) => c.startsWith("s-"))?.slice(2) || "new";
    openSheet(word.dataset.word, Number(word.dataset.cue), state);
  });

  $("w-sheet-close").addEventListener("click", closeSheet);
  $("w-sheet").addEventListener("click", (e) => { if (e.target === $("w-sheet")) closeSheet(); });

  $("w-sheet-add").addEventListener("click", () => {
    const line = cues[sheetCue] ? cues[sheetCue].text : "";
    // The sentence it came from is the example, which is the whole reason to
    // mine from a show rather than from a word list: the card arrives with the
    // context already attached.
    deps.add({ word: sheetWord, example: line, source: sourceName, at: cues[sheetCue]?.start });
    closeSheet();
  });

  $("w-sheet-known").addEventListener("click", () => {
    rememberKnown(sheetWord);
    refreshIndex();
    redraw();
    closeSheet();
  });

  $("w-sheet-explain").addEventListener("click", async () => {
    const panel = $("w-sheet-why");
    panel.innerHTML = `<div class="waiting">Looking up “${esc(sheetWord)}”…</div>`;
    try {
      const mod = await import("./explain.js");
      const { info, from } = await mod.explain(sheetWord, { apiKey: deps.apiKey() });
      panel.innerHTML = mod.render(info, from) || `<div class="waiting">Nothing more to add.</div>`;
    } catch (err) {
      panel.innerHTML = `<div class="waiting">${esc(err.message || String(err))}</div>`;
    }
  });

  $("w-follow").addEventListener("click", () => {
    following = !following;
    $("w-follow").classList.toggle("on", following);
    $("w-follow").textContent = following ? "Following along" : "Not following";
  });
}

// Called every time the screen is opened, because the deck may have grown
// since the last time — a word mined ten minutes ago should not still be
// showing as new.
export function openWatch() {
  refreshIndex();
  if (cues.length) redraw();
  else $("w-note").textContent =
    "Open a subtitle file — .srt, .vtt or .ass — and every word in it gets marked " +
    "against your deck. A video or a podcast alongside it is optional.";
}
