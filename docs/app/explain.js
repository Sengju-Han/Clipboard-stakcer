// Why a word means what it means, at the moment it did not stick.
//
// A definition tells you what a word means. It does not tell you when a native
// speaker reaches for it, what it habitually travels with, or which
// near-identical word you were probably confusing it with — and those are what
// a card that keeps failing is failing on. So this is shown on the answer side,
// and shown by itself after an Again, because that is the only moment when
// someone is reliably willing to read it.
//
// Cheapest source first, and most of the time the cheapest is free:
//
//   1. this browser          — instant, already paid for
//   2. the deployed site     — a static file in the repository, no key, no cost
//   3. the API               — only for a word nobody has asked about yet
//
// The same answers the card form writes are the ones read here, so a word
// explained once anywhere is explained everywhere, forever.

const CACHE = "lexis:why:";
const SITE = "../lookups/";

export function slug(term) {
  return String(term || "").trim().toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
}

function remembered(key) {
  try {
    const raw = localStorage.getItem(CACHE + key);
    return raw ? JSON.parse(raw) : null;
  } catch { return null; }
}

function remember(key, info) {
  try { localStorage.setItem(CACHE + key, JSON.stringify(info)); } catch { /* full or blocked */ }
}

async function fromSite(key) {
  try {
    const res = await fetch(`${SITE}${key}.json`, { cache: "no-cache" });
    if (res.ok) return await res.json();
  } catch { /* offline, or not deployed yet */ }
  return null;
}

// The prompt and schema come from the file anki/explain.py writes, so the app
// and the workflow cannot drift into asking for different things.
let contract = null;
async function theContract() {
  if (contract) return contract;
  const res = await fetch("../explain-contract.json", { cache: "no-cache" });
  if (!res.ok) throw new Error("The explanation contract could not be loaded.");
  contract = await res.json();
  return contract;
}

async function fromApi(term, key, apiKey) {
  const { system, schema, max_tokens } = await theContract();
  const res = await fetch("https://api.anthropic.com/v1/messages", {
    method: "POST",
    headers: {
      "content-type": "application/json",
      "x-api-key": apiKey,
      "anthropic-version": "2023-06-01",
      "anthropic-dangerous-direct-browser-access": "true",
    },
    body: JSON.stringify({
      model: "claude-haiku-4-5",
      max_tokens,
      system,
      messages: [{ role: "user", content: term }],
      output_config: { format: { type: "json_schema", schema } },
    }),
  });

  const payload = await res.json().catch(() => ({}));
  if (!res.ok) {
    const said = payload?.error?.message || `${res.status}`;
    throw new Error(
      res.status === 401 ? "Claude rejected the key in Settings."
      : res.status === 429 ? "Claude is rate limiting; it will work again shortly."
      : /workspace/i.test(said) ? "That key belongs to the organisation rather than a workspace."
      : /credit balance/i.test(said) ? "The Anthropic account is out of credit."
      : said);
  }
  if (payload.stop_reason === "refusal") throw new Error("Claude declined to explain that one.");
  const text = (payload.content || []).filter((b) => b.type === "text").map((b) => b.text).join("");
  if (!text) throw new Error("Claude sent nothing back.");
  const info = JSON.parse(text);
  if (info.recognised === false) throw new Error(info.meaning || "That does not look like an English word.");
  return info;
}

export async function explain(term, { apiKey = "" } = {}) {
  const key = slug(term);
  if (!key) throw new Error("Nothing to look up.");

  const local = remembered(key);
  if (local) return { info: local, from: "this browser" };

  const site = await fromSite(key);
  if (site) { remember(key, site); return { info: site, from: "the deck" }; }

  if (!apiKey) {
    throw new Error("Not explained yet. Add an Anthropic key under Settings, " +
      "or run the explain workflow for this word.");
  }
  const info = await fromApi(term, key, apiKey);
  remember(key, info);
  return { info, from: "Claude" };
}

function esc(text) {
  return String(text ?? "").replace(/[&<>"]/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}

// The card already shows the word, the meaning and an example. Repeating them
// here would push the part worth reading off the screen, so only what the card
// cannot say is rendered.
export function render(info, from) {
  let html = "";
  if (info.nuance) html += `<h4>when you would use it</h4><p>${esc(info.nuance)}</p>`;

  if ((info.collocations || []).length) {
    html += `<h4>goes with</h4><div>` +
      info.collocations.map((c) => `<code>${esc(c)}</code>`).join("") + `</div>`;
  }
  if ((info.confusables || []).length) {
    html += `<h4>not to be confused with</h4><ul>` +
      info.confusables.map((c) => `<li><b>${esc(c.word)}</b> — ${esc(c.difference)}</li>`).join("") +
      `</ul>`;
  }
  if (info.memory_hook) html += `<h4>to remember it</h4><p>${esc(info.memory_hook)}</p>`;

  if (!html) return "";
  return `<div class="why">${html}<h4>from ${esc(from)}</h4></div>`;
}
