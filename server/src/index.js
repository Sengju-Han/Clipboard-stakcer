// Lexis, the server.
//
// Everything this app does works without it: the deck lives in the browser, the
// review loop never touches the network, and two phones can already agree
// through a repository. What a server adds is the thing a repository cannot —
// an account. Somebody who has never heard of GitHub can put their email in and
// have their deck on both phones.
//
// So it is small on purpose. It holds cards and reviews and nothing else. It
// runs the same merge the app runs, because a server with its own opinion about
// what your deck says is a server that can lose an evening's work. It has no
// opinion about scheduling at all — FSRS stays in the browser, where it can be
// answered on a train.

import { hashPassword, checkPassword, newToken, tokenHash, id, pairingCode } from "./crypto.js";

const SESSION_DAYS = 90;
const PAIRING_MINUTES = 10;
// Per email and per address, with very different limits. Eight wrong guesses
// at one account is somebody guessing. Eight wrong guesses from one address is
// a family, an office or a phone network — a building's worth of people behind
// one address must not be locked out because one of them keeps mistyping.
const MAX_PER_EMAIL = 8;
const MAX_PER_ADDRESS = 60;
const LOCKOUT_MINUTES = 15;
const MAX_BODY = 12 * 1024 * 1024;   // a 1,200-card push is about 2MB
const BATCH = 100;                   // statements per D1 batch
const MIN_PASSWORD = 10;

const now = () => Date.now();

// ---- plumbing --------------------------------------------------------------

function cors(env, extra = {}) {
  return {
    "Access-Control-Allow-Origin": env.ALLOWED_ORIGIN || "*",
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    "Access-Control-Allow-Headers": "content-type, authorization",
    "Access-Control-Max-Age": "86400",
    Vary: "Origin",
    ...extra,
  };
}

function json(env, body, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: cors(env, { "content-type": "application/json; charset=utf-8", "cache-control": "no-store" }),
  });
}

const fail = (env, status, message) => json(env, { error: message }, status);

async function body(request) {
  const length = Number(request.headers.get("content-length") || 0);
  if (length > MAX_BODY) throw new Error("That is more than this accepts in one go.");
  const text = await request.text();
  if (text.length > MAX_BODY) throw new Error("That is more than this accepts in one go.");
  try { return text ? JSON.parse(text) : {}; }
  catch { throw new Error("That was not JSON."); }
}

// ---- who is asking ---------------------------------------------------------

async function whoever(request, env) {
  const header = request.headers.get("authorization") || "";
  const token = header.startsWith("Bearer ") ? header.slice(7).trim() : "";
  if (!token) return null;
  const row = await env.DB.prepare(
    "SELECT user_id, expires FROM sessions WHERE token_hash = ?",
  ).bind(await tokenHash(token)).first();
  if (!row) return null;
  if (row.expires < now()) {
    // Expired sessions are cleared as they are met rather than on a timer:
    // there is no cron here, and the row is only in the way of the person it
    // belongs to.
    await env.DB.prepare("DELETE FROM sessions WHERE token_hash = ?")
      .bind(await tokenHash(token)).run();
    return null;
  }
  return row.user_id;
}

async function startSession(env, userId, device = "") {
  const token = newToken();
  await env.DB.prepare(
    "INSERT INTO sessions (token_hash, user_id, created, expires, device) VALUES (?, ?, ?, ?, ?)",
  ).bind(await tokenHash(token), userId, now(), now() + SESSION_DAYS * 86400000, String(device).slice(0, 60)).run();
  return token;
}

// ---- slowing down guessing -------------------------------------------------
//
// Counted per email and per address separately. Counting only by address lets
// one person on a shared network lock out everyone else; counting only by email
// lets somebody work through a list of emails from one machine unhindered.

const limitFor = (key) => (key.startsWith("ip:") ? MAX_PER_ADDRESS : MAX_PER_EMAIL);

async function blocked(env, keys) {
  for (const key of keys) {
    const row = await env.DB.prepare("SELECT n, until FROM attempts WHERE key = ?").bind(key).first();
    if (row && row.n >= limitFor(key) && row.until > now()) return true;
  }
  return false;
}

async function noteFailure(env, keys) {
  for (const key of keys) {
    const row = await env.DB.prepare("SELECT n, until FROM attempts WHERE key = ?").bind(key).first();
    const fresh = !row || row.until < now();
    const n = fresh ? 1 : row.n + 1;
    await env.DB.prepare(
      "INSERT INTO attempts (key, n, until) VALUES (?, ?, ?) " +
      "ON CONFLICT(key) DO UPDATE SET n = excluded.n, until = excluded.until",
    ).bind(key, n, now() + LOCKOUT_MINUTES * 60000).run();
  }
}

async function clearFailures(env, keys) {
  for (const key of keys) {
    await env.DB.prepare("DELETE FROM attempts WHERE key = ?").bind(key).run();
  }
}

// ---- accounts --------------------------------------------------------------

function tidyEmail(raw) {
  return String(raw || "").trim().toLowerCase();
}

function emailLooksReal(email) {
  return /^[^@\s]+@[^@\s.]+\.[^@\s]+$/.test(email) && email.length <= 254;
}

async function register(request, env) {
  const { email: raw, password } = await body(request);
  const email = tidyEmail(raw);
  if (!emailLooksReal(email)) return fail(env, 400, "That does not look like an email address.");
  if (String(password || "").length < MIN_PASSWORD) {
    return fail(env, 400, `A password needs at least ${MIN_PASSWORD} characters. Length is what matters; a short phrase beats a mangled word.`);
  }

  const { hash, salt, iterations } = await hashPassword(password);
  const userId = id();
  try {
    await env.DB.prepare(
      "INSERT INTO users (id, email, pw_hash, pw_salt, iterations, created) VALUES (?, ?, ?, ?, ?, ?)",
    ).bind(userId, email, hash, salt, iterations, now()).run();
  } catch (err) {
    if (/UNIQUE/i.test(String(err))) return fail(env, 409, "There is already an account with that email.");
    throw err;
  }
  const token = await startSession(env, userId, request.headers.get("user-agent") || "");
  return json(env, { token, email });
}

async function login(request, env) {
  const { email: raw, password } = await body(request);
  const email = tidyEmail(raw);
  const address = request.headers.get("cf-connecting-ip") || "unknown";
  const keys = [`email:${email}`, `ip:${address}`];

  if (await blocked(env, keys)) {
    return fail(env, 429, `Too many attempts. Try again in ${LOCKOUT_MINUTES} minutes.`);
  }
  if (!emailLooksReal(email)) return fail(env, 401, "That email and password do not match.");

  const user = await env.DB.prepare(
    "SELECT id, pw_hash, pw_salt, iterations FROM users WHERE email = ?",
  ).bind(email).first();

  // The same answer whether the email is unknown or the password is wrong:
  // telling them apart turns this into a way to find out who has an account.
  if (!user || !(await checkPassword(String(password || ""), user))) {
    await noteFailure(env, keys);
    return fail(env, 401, "That email and password do not match.");
  }

  await clearFailures(env, keys);
  const token = await startSession(env, user.id, request.headers.get("user-agent") || "");
  return json(env, { token, email });
}

async function logout(request, env) {
  const header = request.headers.get("authorization") || "";
  const token = header.startsWith("Bearer ") ? header.slice(7).trim() : "";
  if (token) {
    await env.DB.prepare("DELETE FROM sessions WHERE token_hash = ?").bind(await tokenHash(token)).run();
  }
  return json(env, { ok: true });
}

// ---- a second phone --------------------------------------------------------

async function pair(userId, env) {
  const code = pairingCode();
  await env.DB.prepare("INSERT INTO pairings (code, user_id, expires) VALUES (?, ?, ?)")
    .bind(code, userId, now() + PAIRING_MINUTES * 60000).run();
  return json(env, { code, minutes: PAIRING_MINUTES });
}

async function claim(request, env) {
  const { code } = await body(request);
  const tidy = String(code || "").trim().toUpperCase();
  const row = await env.DB.prepare("SELECT user_id, expires FROM pairings WHERE code = ?").bind(tidy).first();
  // One use only, expired or not: the row goes whether it worked or not, so a
  // code read off somebody's screen an hour later is worth nothing.
  if (row) await env.DB.prepare("DELETE FROM pairings WHERE code = ?").bind(tidy).run();
  if (!row || row.expires < now()) return fail(env, 401, "That code is not valid any more. Codes last ten minutes.");
  const token = await startSession(env, row.user_id, request.headers.get("user-agent") || "");
  return json(env, { token });
}

// ---- the deck --------------------------------------------------------------

async function pull(request, userId, env) {
  const url = new URL(request.url);
  const since = Number(url.searchParams.get("since") || 0) || 0;

  const cards = await env.DB.prepare(
    "SELECT json FROM cards WHERE user_id = ? AND mod > ? ORDER BY mod",
  ).bind(userId, since).all();

  // Reviews are append-only and small, so they come whole. Asking for "reviews
  // since" would mean trusting two clocks to agree about an ordering, and the
  // union does not need them to.
  const reviews = await env.DB.prepare(
    "SELECT json FROM reviews WHERE user_id = ? ORDER BY at",
  ).bind(userId).all();

  const top = await env.DB.prepare("SELECT MAX(mod) AS m FROM cards WHERE user_id = ?").bind(userId).first();

  return json(env, {
    cards: (cards.results || []).map((r) => JSON.parse(r.json)),
    reviews: (reviews.results || []).map((r) => JSON.parse(r.json)),
    now: top?.m || 0,
  });
}

async function push(request, userId, env) {
  const payload = await body(request);
  const cards = Array.isArray(payload.cards) ? payload.cards : [];
  const reviews = Array.isArray(payload.reviews) ? payload.reviews : [];

  let kept = 0, taken = 0;
  const batch = [];

  for (const card of cards) {
    if (!card || typeof card.id !== "string") continue;
    const mod = Number(card.mod) || 0;
    // The newer change wins, whichever device made it. This is the same rule
    // the app uses between two phones; the server holding a different one is
    // how an evening's answers disappear.
    batch.push(env.DB.prepare(
      "INSERT INTO cards (user_id, id, mod, json) VALUES (?, ?, ?, ?) " +
      "ON CONFLICT(user_id, id) DO UPDATE SET mod = excluded.mod, json = excluded.json " +
      "WHERE excluded.mod > cards.mod",
    ).bind(userId, card.id, mod, JSON.stringify(card)));
    kept += 1;
  }

  for (const entry of reviews) {
    if (!entry || typeof entry.at !== "string") continue;
    const undone = entry.undone ? 1 : 0;
    // A review is never rewritten, with one exception: an answer taken back
    // stays taken back, so the undone mark can go on and never comes off.
    batch.push(env.DB.prepare(
      "INSERT INTO reviews (user_id, at, undone, json) VALUES (?, ?, ?, ?) " +
      "ON CONFLICT(user_id, at) DO UPDATE SET undone = 1, json = excluded.json " +
      "WHERE excluded.undone = 1 AND reviews.undone = 0",
    ).bind(userId, entry.at, undone, JSON.stringify(entry)));
    taken += 1;
  }

  // In chunks, not all at once. The first sync of an existing deck is 1,177
  // statements in one go; that works against a local D1 and is a single
  // transaction holding the whole thing in memory on the other side of a
  // network, which is the shape of request that gets refused rather than
  // slowed down. A hundred at a time is unremarkable and still only a dozen
  // round trips for a whole collection.
  for (let i = 0; i < batch.length; i += BATCH) {
    await env.DB.batch(batch.slice(i, i + BATCH));
  }

  const counts = await env.DB.prepare(
    "SELECT (SELECT COUNT(*) FROM cards WHERE user_id = ?1) AS cards, " +
    "(SELECT COUNT(*) FROM reviews WHERE user_id = ?1) AS reviews",
  ).bind(userId).first();

  return json(env, { sent: { cards: kept, reviews: taken }, held: counts });
}

async function me(userId, env) {
  const user = await env.DB.prepare("SELECT email, created FROM users WHERE id = ?").bind(userId).first();
  const counts = await env.DB.prepare(
    "SELECT (SELECT COUNT(*) FROM cards WHERE user_id = ?1) AS cards, " +
    "(SELECT COUNT(*) FROM reviews WHERE user_id = ?1) AS reviews, " +
    "(SELECT COUNT(*) FROM sessions WHERE user_id = ?1) AS devices",
  ).bind(userId).first();
  return json(env, { email: user?.email || "", since: user?.created || 0, ...counts });
}

// Everything, in one file, for someone who wants to leave. A service that makes
// leaving hard is a service that has stopped competing on being good.
async function takeout(userId, env) {
  const cards = await env.DB.prepare("SELECT json FROM cards WHERE user_id = ?").bind(userId).all();
  const reviews = await env.DB.prepare("SELECT json FROM reviews WHERE user_id = ?").bind(userId).all();
  return json(env, {
    exported_at: new Date().toISOString(),
    cards: (cards.results || []).map((r) => JSON.parse(r.json)),
    reviews: (reviews.results || []).map((r) => JSON.parse(r.json)),
  });
}

async function forgetMe(userId, env) {
  await env.DB.batch([
    env.DB.prepare("DELETE FROM cards WHERE user_id = ?").bind(userId),
    env.DB.prepare("DELETE FROM reviews WHERE user_id = ?").bind(userId),
    env.DB.prepare("DELETE FROM sessions WHERE user_id = ?").bind(userId),
    env.DB.prepare("DELETE FROM pairings WHERE user_id = ?").bind(userId),
    env.DB.prepare("DELETE FROM vault WHERE user_id = ?").bind(userId),
    env.DB.prepare("DELETE FROM users WHERE id = ?").bind(userId),
  ]);
  return json(env, { ok: true });
}

// ---- the vault -------------------------------------------------------------
//
// Ciphertext in, ciphertext out. Nothing here inspects it, and nothing here
// could: the key is derived from the password in the browser, and what reaches
// this side is the result. A blob that will not decrypt is indistinguishable
// from one that will, so the wrong password fails in the browser and this never
// hears about it.

const MAX_VAULT = 64 * 1024;

async function readVault(userId, env) {
  const row = await env.DB.prepare("SELECT blob, mod FROM vault WHERE user_id = ?")
    .bind(userId).first();
  return json(env, { blob: row?.blob || "", mod: row?.mod || 0 });
}

async function writeVault(request, userId, env) {
  const { blob } = await body(request);
  if (typeof blob !== "string") return fail(env, 400, "A vault is a string.");
  // Empty means forget it, so that signing out everywhere and clearing the
  // stored keys is one call rather than a special case.
  if (!blob) {
    await env.DB.prepare("DELETE FROM vault WHERE user_id = ?").bind(userId).run();
    return json(env, { ok: true, mod: 0 });
  }
  if (blob.length > MAX_VAULT) return fail(env, 400, "That is larger than a vault should ever be.");
  const mod = Date.now();
  await env.DB.prepare(
    "INSERT INTO vault (user_id, blob, mod) VALUES (?, ?, ?) " +
    "ON CONFLICT(user_id) DO UPDATE SET blob = excluded.blob, mod = excluded.mod",
  ).bind(userId, blob, mod).run();
  return json(env, { ok: true, mod });
}

// ---- the router ------------------------------------------------------------

const PUBLIC = {
  "POST /api/register": register,
  "POST /api/login": login,
  "POST /api/logout": logout,
  "POST /api/pair/claim": claim,
};

const PRIVATE = {
  "GET /api/me": (req, user, env) => me(user, env),
  "GET /api/state": (req, user, env) => pull(req, user, env),
  "POST /api/state": (req, user, env) => push(req, user, env),
  "POST /api/pair": (req, user, env) => pair(user, env),
  "GET /api/takeout": (req, user, env) => takeout(user, env),
  "POST /api/forget-me": (req, user, env) => forgetMe(user, env),
  "GET /api/vault": (req, user, env) => readVault(user, env),
  "PUT /api/vault": (req, user, env) => writeVault(req, user, env),
};

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const route = `${request.method} ${url.pathname}`;

    if (request.method === "OPTIONS") return new Response(null, { status: 204, headers: cors(env) });
    if (url.pathname === "/api/health") return json(env, { ok: true });

    try {
      const open = PUBLIC[route];
      if (open) return await open(request, env);

      const guarded = PRIVATE[route];
      if (guarded) {
        const user = await whoever(request, env);
        if (!user) return fail(env, 401, "Sign in again.");
        return await guarded(request, user, env);
      }
      return fail(env, 404, "No such thing here.");
    } catch (err) {
      // The message is shown to a person, so it says what they can do about it;
      // anything unexpected says nothing about the inside of this.
      const said = String(err?.message || err);
      const theirs = /JSON|accepts in one go/.test(said);
      return fail(env, theirs ? 400 : 500, theirs ? said : "Something went wrong here. Nothing was changed.");
    }
  },
};
