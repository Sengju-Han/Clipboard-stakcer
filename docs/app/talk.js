// Saying the words out loud, to something that answers back.
//
// A word you can recognise on a card and a word you can reach for mid-sentence
// are not the same word, and the gap between them is where most vocabulary
// study quietly stops paying. The apps that sell this — Speak at $165 a year,
// Praktika, Talkpal — all teach from their own syllabus. This one has
// something none of them do: it knows exactly which words you learned this
// week, so the conversation is built to make you use those.
//
// It runs in the browser and nowhere else. Android Chrome has had speech
// recognition built in for years and it costs nothing; the phone's own voice
// reads the replies; only the conversation itself is a Claude call, and that
// is a few hundred tokens a turn. Where recognition is missing — an iPhone,
// Firefox — you type instead, and everything else is identical.

import { serverUrl } from "./where.js";

const MODEL = "claude-haiku-4-5";
const TURNS = 8;

// Two calls, doing two different jobs, because one call was doing neither well.
//
// It used to be one: every turn asked for a reply, a list of which target words
// had been used, and a correction. That made the conversation slow and the
// recap empty.
//
//   - The word list was dead. `spotted()` decided, and the line that read
//     Claude's answer was `(claimed && spotted(...)) || spotted(...)`, which is
//     just `spotted(...)`. It was asked for on every turn and thrown away.
//   - The correction was asked for on every turn and told to stay quiet:
//     "at most one thing per turn, only when it would genuinely be
//     misunderstood, ignore small slips". Which is right for a conversation -
//     being picked up mid-sentence is how people stop talking - but it left
//     the recap with nothing in it. The one screen that exists to say what to
//     work on had a heading and no list under it.
//
// So: the conversation is now plain streamed text, which is as fast as this
// gets and starts appearing while it is still being written. And the recap is
// its own call at the end, given the whole transcript and told to hold nothing
// back, at the one moment when waiting a second is fine.

const SYSTEM = `You are a warm, patient conversation partner for a Korean adult learning English.
You are talking, not teaching: keep your turns to one or two sentences, ask one question at a time,
and sound like a person rather than a lesson.

You are given a short list of TARGET WORDS the learner has recently studied. Steer the conversation
so that using them is the natural thing to do — ask about situations where they fit — but never
name the list, never say "try to use", and never quiz them.

Do not correct them and do not comment on their English at all. They get that at the end, from
somebody else, with the whole conversation in front of them. Your only job is to be easy to talk to.
The learner is speaking out loud, so what reaches you has speech-recognition errors in it: missing
articles, wrong homophones, no punctuation. Read past all of it.

Reply with what you say next, and nothing else. No quotation marks, no stage directions, no JSON.`;

// ---- talking to Claude ----------------------------------------------------

/**
 * One turn, streamed.
 *
 * `onText` is called with the reply so far as it arrives, so the words appear
 * while they are being written instead of all at once a second and a half
 * later. That second and a half is the same either way; what changes is how
 * long the screen sits there saying nothing.
 */
export async function turn(messages, targets, apiKey, onText = () => {}) {
  const res = await fetch("https://api.anthropic.com/v1/messages", {
    method: "POST",
    headers: {
      "content-type": "application/json",
      "x-api-key": apiKey,
      "anthropic-version": "2023-06-01",
      "anthropic-dangerous-direct-browser-access": "true",
    },
    body: JSON.stringify({
      model: MODEL,
      max_tokens: 200,
      system: `${SYSTEM}\n\nTARGET WORDS: ${targets.join(", ")}`,
      messages,
      stream: true,
    }),
  });

  if (!res.ok) {
    const payload = await res.json().catch(() => ({}));
    throw new Error(whyItRefused(res.status, payload?.error?.message || `${res.status}`));
  }

  let text = "";
  for await (const event of events(res)) {
    if (event.type === "content_block_delta" && event.delta?.type === "text_delta") {
      text += event.delta.text;
      onText(text);
    } else if (event.type === "error") {
      throw new Error(event.error?.message || "Claude stopped partway.");
    }
  }
  const said = text.trim();
  if (!said) throw new Error("Claude sent nothing back.");
  return said;
}

/** The server-sent events out of a streaming response, one parsed object at a time. */
async function* events(res) {
  const reader = res.body.getReader();
  const decode = new TextDecoder();
  let buffer = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decode.decode(value, { stream: true });
    // Events are separated by a blank line; a partial one stays in the buffer.
    let cut;
    while ((cut = buffer.indexOf("\n\n")) !== -1) {
      const block = buffer.slice(0, cut);
      buffer = buffer.slice(cut + 2);
      for (const line of block.split("\n")) {
        if (!line.startsWith("data:")) continue;
        const body = line.slice(5).trim();
        if (!body || body === "[DONE]") continue;
        try { yield JSON.parse(body); } catch { /* not ours to fix */ }
      }
    }
  }
}

// The message a refusal deserves, rather than its status code.
export function whyItRefused(status, said) {
  if (status === 401) return "Claude rejected the key in Settings.";
  if (status === 429) return "Claude is rate limiting; give it a moment.";
  if (/workspace/i.test(said)) return "That key belongs to the organisation rather than a workspace.";
  if (/credit balance/i.test(said)) return "The Anthropic account is out of credit.";
  return said;
}

// ---- the recap ------------------------------------------------------------
//
// The whole point of the session, and until now the thinnest part of it: two
// lists of words and a heading with nothing under it. This is a separate call
// with the whole conversation in front of it, and the instruction the turn
// prompt cannot have - say everything worth saying.

const DEBRIEF = `You are an English teacher reading back a conversation one of your learners has just
had. They are a Korean adult. You were not the one talking to them, and you are not being polite to
the other side — your job is to tell them what is actually worth knowing.

Be specific and be generous with detail. This is the one moment in the session where they have
stopped talking and can take something in, so it is the wrong moment to be brief. Quote them. Say
the natural version rather than describing it.

PATTERNS: the things that happened more than once, or that would make a native speaker pause. Two to
five of them. Not every slip — the repeated ones, and the ones that change the meaning. If they
genuinely made none, say so in one line rather than inventing some.

They were speaking out loud and the transcript came from speech recognition, so missing articles,
missing punctuation and wrong homophones ("their/there", "to/too") may be the microphone rather than
them. Never correct one of those. If a mistake could be either, leave it out.

WORDS: for each target word, whether they used it and how it sounded. A word that never came up is
worth saying so about.

Warm, plain English, no grammar jargon. Reply as JSON only.`;

const RECAP_SCHEMA = {
  type: "object",
  properties: {
    strength: {
      type: "string",
      description: "One or two things they did genuinely well, quoting them. Two sentences at most.",
    },
    patterns: {
      type: "array",
      description: "Two to five things worth saying differently. Empty only if there are truly none.",
      items: {
        type: "object",
        properties: {
          heard: { type: "string", description: "What they said, quoted." },
          natural: { type: "string", description: "How a native speaker would say it." },
          why: { type: "string", description: "One short clause. No grammar jargon." },
        },
        required: ["heard", "natural", "why"],
        additionalProperties: false,
      },
    },
    words: {
      type: "array",
      description: "One entry per target word, in the order given.",
      items: {
        type: "object",
        properties: {
          word: { type: "string" },
          note: {
            type: "string",
            description: "How it was used, or that it never came up, in one short sentence.",
          },
        },
        required: ["word", "note"],
        additionalProperties: false,
      },
    },
    next: { type: "string", description: "One thing to try in the next conversation." },
  },
  required: ["strength", "patterns", "words", "next"],
  additionalProperties: false,
};

/** The debrief, from the whole conversation. Throws; the caller has a fallback. */
export async function debrief(messages, targets, apiKey) {
  const transcript = messages
    .filter((m) => !/^\(Open the conversation/.test(m.content))
    .map((m) => `${m.role === "user" ? "LEARNER" : "PARTNER"}: ${m.content}`)
    .join("\n");

  const res = await fetch("https://api.anthropic.com/v1/messages", {
    method: "POST",
    headers: {
      "content-type": "application/json",
      "x-api-key": apiKey,
      "anthropic-version": "2023-06-01",
      "anthropic-dangerous-direct-browser-access": "true",
    },
    body: JSON.stringify({
      model: MODEL,
      max_tokens: 1200,
      system: DEBRIEF,
      messages: [{
        role: "user",
        content: `TARGET WORDS: ${targets.join(", ")}\n\nThe conversation:\n\n${transcript}`,
      }],
      output_config: { format: { type: "json_schema", schema: RECAP_SCHEMA } },
    }),
  });

  const payload = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(whyItRefused(res.status, payload?.error?.message || `${res.status}`));
  }
  const text = (payload.content || []).filter((b) => b.type === "text").map((b) => b.text).join("");
  if (!text) throw new Error("Claude sent nothing back.");
  return JSON.parse(text);
}

// ---- which words to practise ----------------------------------------------
//
// The ones in the middle: learned well enough to be reachable, not yet settled
// enough to be safe. A word you have never met cannot be used in conversation
// and a word you have known for a year does not need the practice.

export function targets(cards, n = 6, now = Date.now()) {
  const ready = cards.filter((c) => {
    const f = c.fsrs || {};
    if (f.state === 1 || f.state === 3) return true;                  // being learned
    return f.state === 2 && (f.stability || 0) < 21;                  // learned, still young
  });

  // Most recently answered first: this week's words, not this year's.
  const when = (c) => (c.fsrs?.last_review ? new Date(c.fsrs.last_review).getTime()
    : c.mod || 0);
  const pool = ready.sort((a, b) => when(b) - when(a)).slice(0, n * 4);

  // A little randomness inside that pool, so two sessions in a row are not the
  // same six words.
  for (let i = pool.length - 1; i > 0; i -= 1) {
    const j = Math.floor(Math.random() * (i + 1));
    [pool[i], pool[j]] = [pool[j], pool[i]];
  }
  const seen = new Set();
  const out = [];
  for (const card of pool) {
    const key = card.word.toLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(card);
    if (out.length >= n) break;
  }
  return out;
}

// Did they use it? Loose on purpose: "avowed" counts as "avow", and a phrasal
// verb counts however it was split. Claude is asked the same question and is
// better at it; this is the check that runs when Claude says nothing.
export function spotted(said, word) {
  // Accents fold before the a-z filter, or they become spaces and split the
  // word: "séance" tidied to "s ance", which then needed the sentence to
  // contain a lone "s" to match. It did when the sentence also carried the
  // accent, and did not when the recogniser wrote "seance" - so the card
  // worked or failed depending on how a machine chose to spell it.
  const tidy = (text) => String(text).toLowerCase()
    .normalize("NFD").replace(/[\u0300-\u036f]/g, "").normalize("NFC")
    .replace(/[^a-z' ]+/g, " ").replace(/\s+/g, " ");
  const text = ` ${tidy(said)} `;
  // The word is tidied the same way the sentence is. It used to be split on
  // whitespace alone, so a hyphen survived in the word and not in the text:
  // "tie-dye" became `\btie-dye\b` looking at "tie dye", which cannot match.
  // Twenty-one words in a 1,237-word collection are like that - jam-packed,
  // litter-mates, ne'er-do-well - and saying one of them out loud would never
  // tick it off.
  const parts = tidy(word).split(/\s+/).filter(Boolean);
  const stem = (w) => w.replace(/(ing|ed|es|s)$/, "");
  return parts.every((part) => {
    const root = stem(part);
    if (root.length < 3) return text.includes(` ${part} `);
    return new RegExp(`\\b${root.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\w{0,3}\\b`).test(text);
  });
}

// ---- the phone's ears and voice -------------------------------------------

export function listener() {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SR) return null;
  const rec = new SR();
  rec.lang = "en-US";
  rec.interimResults = true;
  rec.continuous = false;
  rec.maxAlternatives = 1;
  return rec;
}

// The message a failure deserves, rather than its error code.
export function whyItStopped(code) {
  return {
    "not-allowed": "The microphone is blocked. Allow it for this site in your browser settings, or type instead.",
    "service-not-allowed": "The microphone is blocked for this site. You can type instead.",
    "no-speech": "Nothing came through. Try again, or type it.",
    "audio-capture": "No microphone was found. You can type instead.",
    network: "Speech recognition needs the network and could not reach it. Type instead for now.",
    aborted: "",
  }[code] || "That did not come through. Try again, or type it.";
}

// The cards have sounded like a person since the TTS workflow shipped: their
// audio is generated with Microsoft's neural voices and en-US-AvaNeural is what
// plays when you tap one. This screen did not. It used speechSynthesis, which
// on Android is the flat, clipped, unmistakably synthetic voice - so the same
// app in the same session was half person and half robot, and the half doing
// the talking was the robot.
//
// The page cannot fetch the neural voice itself: the service wants an Origin
// header of `chrome-extension://...` and a browser will not let a page set
// Origin. The server does it instead, and hands back an mp3.
//
// The phone's own voice is still here, underneath all of it. Every way this can
// fail - no network, the server down, the service having retired the endpoint
// it was never supposed to offer, a browser refusing to play audio it did not
// see you ask for - ends with the robot talking rather than with silence.

export const PHONE = "phone";
export const VOICES = [
  { id: "en-US-AvaNeural", label: "Ava — American" },
  { id: "en-US-AndrewNeural", label: "Andrew — American" },
  { id: "en-US-EmmaNeural", label: "Emma — American" },
  { id: "en-US-BrianNeural", label: "Brian — American" },
  { id: "en-GB-SoniaNeural", label: "Sonia — British" },
  { id: "en-GB-RyanNeural", label: "Ryan — British" },
  { id: PHONE, label: "This phone's own voice" },
];
// The one the cards use, so the two halves of the app finally agree.
export const DEFAULT_VOICE = "en-US-AvaNeural";
const REMEMBER = "talk.voice";

export function chosenVoice() {
  try {
    const set = localStorage.getItem(REMEMBER) || "";
    return VOICES.some((v) => v.id === set) ? set : DEFAULT_VOICE;
  } catch { return DEFAULT_VOICE; }
}

export function rememberVoice(id) {
  try { localStorage.setItem(REMEMBER, id); } catch { /* private window */ }
}

let voice = null;
function phoneSays(text, onDone) {
  if (!window.speechSynthesis) { onDone(); return null; }
  try {
    speechSynthesis.cancel();
    if (!voice) {
      const all = speechSynthesis.getVoices();
      voice = all.find((v) => /^en[-_]/i.test(v.lang) && /google|natural|siri|samsung/i.test(v.name))
        || all.find((v) => /^en[-_]/i.test(v.lang)) || null;
    }
    const utter = new SpeechSynthesisUtterance(text);
    if (voice) utter.voice = voice;
    utter.lang = voice?.lang || "en-US";
    utter.rate = 0.95;                 // a shade under natural; this is practice
    utter.onend = onDone;
    utter.onerror = onDone;
    speechSynthesis.speak(utter);
    return utter;
  } catch {
    onDone();
    return null;
  }
}

async function fromServer(text, id, where) {
  const url = `${where}/api/say?${new URLSearchParams({ text, voice: id })}`;
  const res = await fetch(url);
  if (!res.ok) throw new Error(`the voice server answered ${res.status}`);
  const blob = await res.blob();
  // A 200 with nothing in it would play as silence and pass for a turn.
  if (!blob.size) throw new Error("the voice server sent no audio");
  return blob;
}

// Bumped by every hush and every new turn, so audio that arrives after the
// screen has moved on is thrown away rather than spoken over the top.
let turnId = 0;
let playing = null;
let playingSrc = "";        // the blob behind it, so hushing can let it go

export function say(text, onDone = () => {}) {
  hush();
  const mine = turnId;
  const id = chosenVoice();
  if (id === PHONE) return phoneSays(text, onDone);

  const robot = () => { if (mine === turnId) phoneSays(text, onDone); };

  fromServer(text, id, serverUrl()).then((blob) => {
    if (mine !== turnId) return;                  // hushed while it was coming
    const src = URL.createObjectURL(blob);
    const audio = new Audio(src);
    playing = audio;
    playingSrc = src;
    let finished = false;
    const finish = (fallback) => {
      if (finished) return;
      finished = true;
      URL.revokeObjectURL(src);
      if (playing === audio) { playing = null; playingSrc = ""; }
      if (fallback) robot(); else onDone();
    };
    audio.addEventListener("ended", () => finish(false));
    // A file that will not decode is the phone's turn, not silence.
    audio.addEventListener("error", () => finish(true));
    audio.play().catch(() => finish(true));
  }).catch(robot);

  return null;
}

export function hush() {
  turnId += 1;
  try { speechSynthesis.cancel(); } catch { /* nothing playing */ }
  if (playing) {
    try { playing.pause(); } catch { /* already stopped */ }
    playing = null;
  }
  // Interrupted audio never reaches its own ended handler, so the blob it was
  // reading from would sit in memory for the life of the page. One per
  // interrupted turn is not much and adds up over a session of pressing
  // "Say that again".
  if (playingSrc) {
    try { URL.revokeObjectURL(playingSrc); } catch { /* already gone */ }
    playingSrc = "";
  }
}

export const LENGTH = TURNS;

// ---- the screen -----------------------------------------------------------

const $ = (id) => document.getElementById(id);

let deps = null;
let words = [];            // the target cards
let used = new Set();      // which of them have been reached for
let history = [];          // the Anthropic messages array
let spoken = 0;            // the learner's turns so far
let rec = null;            // the live recogniser, when listening
let busy = false;

function esc(text) {
  return String(text ?? "").replace(/[&<>"]/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}

function drawTargets() {
  $("t-words").innerHTML = words.map((c) => {
    const done = used.has(c.word.toLowerCase());
    return `<span class="chip target${done ? " on" : ""}">${done ? "✓ " : ""}${esc(c.word)}</span>`;
  }).join("");
}

function bubble(who, text, extra = "") {
  const el = document.createElement("div");
  el.className = `bubble ${who}`;
  el.innerHTML = `<p>${esc(text)}</p>${extra}`;
  $("t-log").appendChild(el);
  el.scrollIntoView({ block: "end", behavior: "smooth" });
  return el;
}

function thinking(on) {
  busy = on;
  $("t-thinking").hidden = !on;
  $("t-send").disabled = on;
  $("t-mic").disabled = on;
}

function progress() {
  $("t-progress").textContent = spoken >= LENGTH
    ? "That is a session."
    : `${LENGTH - spoken} more exchange${LENGTH - spoken === 1 ? "" : "s"}`;
}

async function advance(saidByLearner) {
  thinking(true);
  let live = null;                     // the bubble being written into
  try {
    // The API needs the conversation to start with the learner, and at the very
    // beginning there is no learner yet — so the opener is an instruction, not
    // something they said, and it never appears on screen.
    history.push(saidByLearner === null
      ? { role: "user", content: "(Open the conversation. Greet them and ask one easy question.)" }
      : { role: "user", content: saidByLearner });

    const reply = await turn(history, words.map((c) => c.word), deps.apiKey(), (sofar) => {
      // Made on the first token rather than up front, so a call that fails
      // outright leaves no empty bubble sitting there. Once there are words to
      // read, the words are the progress indicator and the dots are noise.
      if (!live) { live = bubble("them", ""); $("t-thinking").hidden = true; }
      live.querySelector("p").textContent = sofar;
    });
    history.push({ role: "assistant", content: reply });
    if (!live) live = bubble("them", reply);
    else live.querySelector("p").textContent = reply;

    // What they actually said decides, not what Claude thinks they said. A word
    // credited that was never spoken is a word that quietly stops being
    // practised.
    if (saidByLearner) {
      for (const card of words) {
        if (spotted(saidByLearner, card.word)) used.add(card.word.toLowerCase());
      }
      drawTargets();
    }
    say(reply);
  } catch (err) {
    if (live) live.remove();
    bubble("note", err.message || String(err));
  } finally {
    thinking(false);
    progress();
    if (spoken >= LENGTH) finish();
  }
}

// The two word lists this used to be, kept for when the debrief cannot be had:
// no key, no signal, Claude refusing. Thin, but true and free.
function wordLists() {
  const got = words.filter((c) => used.has(c.word.toLowerCase()));
  const missed = words.filter((c) => !used.has(c.word.toLowerCase()));
  let html = `<h4>you reached for</h4><p>${got.length
    ? got.map((c) => esc(c.word)).join(", ") : "none of them, this time"}</p>`;
  if (missed.length) {
    html += `<h4>still only on paper</h4><p>${missed.map((c) => esc(c.word)).join(", ")}</p>`;
  }
  return html;
}

function recapHtml(out) {
  const noteFor = (word) => {
    const found = (out.words || []).find(
      (x) => String(x.word || "").toLowerCase() === word.toLowerCase());
    return found ? String(found.note || "") : "";
  };

  let html = "";
  if (String(out.strength || "").trim()) {
    html += `<h4>what went well</h4><p>${esc(out.strength)}</p>`;
  }

  const patterns = (out.patterns || []).filter((c) => String(c.natural || "").trim());
  html += `<h4>worth saying differently</h4>`;
  html += patterns.length
    ? `<ul>${patterns.map((c) =>
        `<li><s>${esc(c.heard)}</s> → <b>${esc(c.natural)}</b> <span>${esc(c.why)}</span></li>`
      ).join("")}</ul>`
    : `<p>Nothing that would make anybody pause. That is the point of doing this.</p>`;

  // The tick is decided here and not by Claude, for the same reason as during
  // the conversation: it is the one fact on this screen, and a word wrongly
  // ticked stops being practised.
  html += `<h4>your words</h4><ul class="said">` + words.map((card) => {
    const done = used.has(card.word.toLowerCase());
    const note = noteFor(card.word);
    return `<li><b>${done ? "✓" : "—"} ${esc(card.word)}</b>`
      + (note ? ` <span>${esc(note)}</span>` : "") + `</li>`;
  }).join("") + `</ul>`;

  if (String(out.next || "").trim()) {
    html += `<h4>next time</h4><p>${esc(out.next)}</p>`;
  }
  return html;
}

async function finish() {
  $("t-input").hidden = true;
  $("t-recap").hidden = false;
  // Said out loud, because this is a second call and the screen would otherwise
  // sit empty at the exact moment somebody is waiting to be told how they did.
  $("t-recap").innerHTML = `<div class="why"><p>Reading the conversation back…</p></div>`;

  let html;
  try {
    html = recapHtml(await debrief(history, words.map((c) => c.word), deps.apiKey()));
  } catch (err) {
    // Never nothing. The lists below are worth less than the debrief and worth
    // a great deal more than an empty panel with an error in it.
    html = wordLists()
      + `<h4>no debrief this time</h4><p>${esc(err.message || String(err))}</p>`;
  }
  $("t-recap").innerHTML = `<div class="why">${html}</div>`;
}

// ---- hearing them ---------------------------------------------------------

function heard(text) {
  const said = String(text || "").trim();
  if (!said || busy) return;
  bubble("me", said);
  spoken += 1;
  $("t-say").value = "";
  advance(said);
}

function listen() {
  if (rec) { rec.stop(); return; }
  hush();
  rec = listener();
  if (!rec) {
    $("t-note").textContent =
      "This browser has no speech recognition — Chrome on Android does. Type instead; everything else is the same.";
    return;
  }
  let best = "";
  $("t-mic").classList.add("live");
  $("t-mic").textContent = "Listening — tap when done";
  rec.onresult = (e) => {
    best = [...e.results].map((r) => r[0].transcript).join(" ").trim();
    $("t-say").value = best;
  };
  rec.onerror = (e) => { $("t-note").textContent = whyItStopped(e.error); };
  rec.onend = () => {
    rec = null;
    $("t-mic").classList.remove("live");
    $("t-mic").textContent = "🎤 Speak";
    if (best) heard(best);
  };
  $("t-note").textContent = "";
  try { rec.start(); } catch { rec = null; }
}

export function mountTalk(hooks) {
  deps = hooks;
  $("t-mic").addEventListener("click", listen);
  $("t-send").addEventListener("click", () => heard($("t-say").value));
  $("t-say").addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); heard($("t-say").value); }
  });
  $("t-again").addEventListener("click", () => openTalk());
  $("t-repeat").addEventListener("click", () => {
    const last = [...$("t-log").querySelectorAll(".bubble.them p")].pop();
    if (last) say(last.textContent);
  });

  const picker = $("t-voice");
  picker.innerHTML = VOICES.map((v) =>
    `<option value="${v.id}">${v.label}</option>`).join("");
  picker.value = chosenVoice();
  picker.addEventListener("change", () => {
    rememberVoice(picker.value);
    // Said in the new voice rather than described, because the only question
    // anybody has here is what it sounds like.
    say("Alright — this is how I sound.");
  });
}

export function openTalk() {
  hush();
  words = targets(deps.cards());
  used = new Set();
  history = [];
  spoken = 0;
  $("t-log").innerHTML = "";
  $("t-recap").hidden = true;
  $("t-recap").innerHTML = "";
  $("t-input").hidden = false;
  $("t-say").value = "";
  $("t-note").textContent = "";
  drawTargets();
  progress();

  if (!words.length) {
    bubble("note", "Nothing to practise yet — review some cards first, and the words you are " +
      "in the middle of learning will show up here.");
    $("t-input").hidden = true;
    return;
  }
  if (!deps.apiKey()) {
    bubble("note", "A conversation needs an Anthropic key. Settings → Anthropic key. " +
      "A session is a few hundred tokens a turn.");
    $("t-input").hidden = true;
    return;
  }
  advance(null);
}
