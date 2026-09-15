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

const MODEL = "claude-haiku-4-5";
const TURNS = 8;

const SYSTEM = `You are a warm, patient conversation partner for a Korean adult learning English.
You are talking, not teaching: keep your turns to one or two sentences, ask one question at a time,
and sound like a person rather than a lesson.

You are given a short list of TARGET WORDS the learner has recently studied. Steer the conversation
so that using them is the natural thing to do — ask about situations where they fit — but never
name the list, never say "try to use", and never quiz them. If they use one, react to what they
said, not to the fact that they used it.

Correct at most one thing per turn, and only when it would genuinely be misunderstood or sounds
clearly unnatural. Say the natural version, briefly, and move on. Ignore small slips. When there is
nothing worth correcting, leave all three correction fields as empty strings. The learner
is speaking out loud, so what reaches you has speech-recognition errors in it: missing articles,
wrong homophones, no punctuation. Never correct anything that is plainly a transcription artefact.

Reply as JSON only.`;

const SCHEMA = {
  type: "object",
  properties: {
    reply: { type: "string", description: "What you say next. One or two sentences, spoken English." },
    used: {
      type: "array",
      items: { type: "string" },
      description: "Target words the learner actually used in their last message, naturally or not. Empty if none.",
    },
    // Every field is always present and every field is a plain string. A
    // nullable union would say "no correction" more elegantly and is not worth
    // the risk: the schema shape that is known to work with this API is the
    // one the explanation contract already uses, and it has no unions in it.
    // Nothing to correct is three empty strings.
    correction: {
      type: "object",
      properties: {
        said: { type: "string", description: "What they said, quoted. Empty if nothing needs correcting." },
        better: { type: "string", description: "The natural way to say it. Empty if nothing needs correcting." },
        why: { type: "string", description: "One short clause, no grammar jargon. Empty if nothing needs correcting." },
      },
      required: ["said", "better", "why"],
      additionalProperties: false,
    },
  },
  required: ["reply", "used", "correction"],
  additionalProperties: false,
};

// ---- talking to Claude ----------------------------------------------------

export async function turn(messages, targets, apiKey) {
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
      max_tokens: 400,
      system: `${SYSTEM}\n\nTARGET WORDS: ${targets.join(", ")}`,
      messages,
      output_config: { format: { type: "json_schema", schema: SCHEMA } },
    }),
  });

  const payload = await res.json().catch(() => ({}));
  if (!res.ok) {
    const said = payload?.error?.message || `${res.status}`;
    throw new Error(
      res.status === 401 ? "Claude rejected the key in Settings."
      : res.status === 429 ? "Claude is rate limiting; give it a moment."
      : /workspace/i.test(said) ? "That key belongs to the organisation rather than a workspace."
      : /credit balance/i.test(said) ? "The Anthropic account is out of credit."
      : said);
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
  const text = ` ${String(said).toLowerCase().replace(/[^a-z' ]+/g, " ").replace(/\s+/g, " ")} `;
  const parts = String(word).toLowerCase().split(/\s+/).filter(Boolean);
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

let voice = null;
export function say(text, onDone = () => {}) {
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

export function hush() {
  try { speechSynthesis.cancel(); } catch { /* nothing playing */ }
}

export const LENGTH = TURNS;

// ---- the screen -----------------------------------------------------------

const $ = (id) => document.getElementById(id);

let deps = null;
let words = [];            // the target cards
let used = new Set();      // which of them have been reached for
let history = [];          // the Anthropic messages array
let corrections = [];
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
  try {
    // The API needs the conversation to start with the learner, and at the very
    // beginning there is no learner yet — so the opener is an instruction, not
    // something they said, and it never appears on screen.
    history.push(saidByLearner === null
      ? { role: "user", content: "(Open the conversation. Greet them and ask one easy question.)" }
      : { role: "user", content: saidByLearner });
    const result = await turn(history, words.map((c) => c.word), deps.apiKey());
    history.push({ role: "assistant", content: JSON.stringify(result) });

    // Claude is asked which target words were used and is good at it, but it
    // is the learner's sentence that decides — a word credited that was never
    // said is a word that quietly stops being practised.
    if (saidByLearner) {
      for (const card of words) {
        const claimed = (result.used || []).some((w) => w.toLowerCase() === card.word.toLowerCase());
        if ((claimed && spotted(saidByLearner, card.word)) || spotted(saidByLearner, card.word)) {
          used.add(card.word.toLowerCase());
        }
      }
      drawTargets();
    }

    let extra = "";
    const fix = result.correction;
    if (fix && String(fix.better || "").trim()) {
      corrections.push(fix);
      extra = `<div class="fix"><s>${esc(fix.said)}</s>` +
        `<b>${esc(fix.better)}</b>` +
        `<span>${esc(fix.why)}</span></div>`;
    }
    bubble("them", result.reply, extra);
    say(result.reply);
  } catch (err) {
    bubble("note", err.message || String(err));
  } finally {
    thinking(false);
    progress();
    if (spoken >= LENGTH) finish();
  }
}

function finish() {
  const got = words.filter((c) => used.has(c.word.toLowerCase()));
  const missed = words.filter((c) => !used.has(c.word.toLowerCase()));
  let html = `<h4>you reached for</h4><p>${got.length ? got.map((c) => esc(c.word)).join(", ") : "none of them, this time"}</p>`;
  if (missed.length) html += `<h4>still only on paper</h4><p>${missed.map((c) => esc(c.word)).join(", ")}</p>`;
  if (corrections.length) {
    html += `<h4>worth saying differently</h4><ul>` + corrections.map((c) =>
      `<li><s>${esc(c.said)}</s> → <b>${esc(c.better)}</b> <span>${esc(c.why)}</span></li>`).join("") + `</ul>`;
  }
  $("t-recap").innerHTML = `<div class="why">${html}</div>`;
  $("t-recap").hidden = false;
  $("t-input").hidden = true;
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
}

export function openTalk() {
  hush();
  words = targets(deps.cards());
  used = new Set();
  history = [];
  corrections = [];
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
