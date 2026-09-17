// The server, exercised against a real D1 (miniflare runs the same SQLite and
// the same Workers runtime), because auth code that has only been read is auth
// code that has not been checked.

import { Miniflare } from "miniflare";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const schema = readFileSync(join(here, "..", "schema.sql"), "utf8");

let passed = 0, failed = 0;
function check(what, got, want) {
  const ok = JSON.stringify(got) === JSON.stringify(want);
  if (ok) { passed += 1; console.log(`  ok   ${what}`); }
  else { failed += 1; console.log(`  FAIL ${what}\n       got  ${JSON.stringify(got)}\n       want ${JSON.stringify(want)}`); }
}
function note(what, value) { console.log(`       ${what}: ${value}`); }

const mf = new Miniflare({
  modules: true,
  scriptPath: join(here, "..", "src", "index.js"),
  modulesRoot: join(here, "..", "src"),
  // Without this the runtime reads the imported files as CommonJS and refuses
  // the first export it meets.
  modulesRules: [{ type: "ESModule", include: ["**/*.js"] }],
  d1Databases: { DB: "lexis-test" },
  bindings: { ALLOWED_ORIGIN: "https://example.test" },
  compatibilityDate: "2026-01-01",
});

const db = await mf.getD1Database("DB");
// Comments go first: exec() wants one statement per line, and a `--` comment
// flattened onto one line swallows the statement after it.
const statements = schema
  .split("\n").map((line) => line.replace(/--.*$/, "")).join("\n")
  .split(";").map((s) => s.replace(/\s+/g, " ").trim()).filter(Boolean);
for (const stmt of statements) await db.exec(stmt + ";");

const call = async (method, path, { token = "", body = null, headers = {} } = {}) => {
  const res = await mf.dispatchFetch(`http://server${path}`, {
    method,
    headers: {
      "content-type": "application/json",
      ...(token ? { authorization: `Bearer ${token}` } : {}),
      ...headers,
    },
    body: body === null ? undefined : JSON.stringify(body),
  });
  let payload = null;
  try { payload = await res.json(); } catch { payload = null; }
  return { status: res.status, payload, headers: res.headers };
};

console.log("\n— registering —");
let r = await call("POST", "/api/register", { body: { email: "A@Example.test ", password: "correct horse battery" } });
check("a new account is created", r.status, 200);
const token = r.payload.token;
check("the email is stored tidied", r.payload.email, "a@example.test");
note("token length", token.length);

r = await call("POST", "/api/register", { body: { email: "a@example.test", password: "another long one" } });
check("the same email twice is refused", [r.status, r.payload.error], [409, "There is already an account with that email."]);

r = await call("POST", "/api/register", { body: { email: "b@example.test", password: "short" } });
check("a short password is refused", r.status, 400);
note("said", r.payload.error);

r = await call("POST", "/api/register", { body: { email: "not-an-email", password: "correct horse battery" } });
check("a bad address is refused", r.status, 400);

console.log("\n— what is stored —");
const row = await db.prepare("SELECT email, pw_hash, pw_salt, iterations FROM users WHERE email = ?")
  .bind("a@example.test").first();
check("the password itself is not in the database", row.pw_hash.includes("correct"), false);
check("the iteration count is recorded", row.iterations, 210000);
note("hash", row.pw_hash.slice(0, 24) + "…");
const sess = await db.prepare("SELECT token_hash FROM sessions").first();
check("the session token is not stored either", sess.token_hash === token, false);

console.log("\n— signing in —");
r = await call("POST", "/api/login", { body: { email: "a@example.test", password: "correct horse battery" } });
check("the right password works", r.status, 200);
const second = r.payload.token;
check("and gives a different token to the first device", second === token, false);

r = await call("POST", "/api/login", { body: { email: "a@example.test", password: "wrong" } });
check("the wrong password is refused", r.status, 401);
const unknownSaid = (await call("POST", "/api/login", { body: { email: "nobody@example.test", password: "wrong" } })).payload.error;
check("an unknown email says exactly the same thing", unknownSaid, r.payload.error);

console.log("\n— slowing down guessing —");
for (let i = 0; i < 8; i += 1) {
  await call("POST", "/api/login", { body: { email: "c@example.test", password: "no" } });
}
r = await call("POST", "/api/login", { body: { email: "c@example.test", password: "no" } });
check("nine wrong tries at one account are stopped", r.status, 429);
note("said", r.payload.error);

// The reason the two limits are different. In this test every request arrives
// from the same address, which is exactly the shape of a family or an office.
r = await call("POST", "/api/login", { body: { email: "a@example.test", password: "correct horse battery" } });
check("and somebody else on the same connection is not locked out", r.status, 200);

console.log("\n— the deck —");
const cards = [
  { id: "one", mod: 1000, word: "avow", fsrs: { state: 2, stability: 3 } },
  { id: "two", mod: 1000, word: "chime in", fsrs: { state: 0, stability: 0 } },
];
const reviews = [{ at: "2026-09-15T10:00:00.000Z", id: "one", rating: 3 }];
r = await call("POST", "/api/state", { token, body: { cards, reviews } });
check("a push is accepted", r.status, 200);
check("and it says what it now holds", r.payload.held, { cards: 2, reviews: 1 });

r = await call("GET", "/api/state", { token: second });
check("the other device pulls both cards", r.payload.cards.length, 2);
check("and the review", r.payload.reviews.length, 1);

console.log("\n— the newer change wins —");
await call("POST", "/api/state", { token, body: { cards: [{ id: "one", mod: 5000, word: "avow", note: "newer" }] } });
await call("POST", "/api/state", { token: second, body: { cards: [{ id: "one", mod: 2000, word: "avow", note: "older" }] } });
r = await call("GET", "/api/state", { token });
check("the older push does not overwrite the newer one",
  r.payload.cards.find((c) => c.id === "one").note, "newer");

await call("POST", "/api/state", { token: second, body: { cards: [{ id: "one", mod: 9000, word: "avow", note: "newest" }] } });
r = await call("GET", "/api/state", { token });
check("but a genuinely newer one does",
  r.payload.cards.find((c) => c.id === "one").note, "newest");

console.log("\n— only what changed —");
r = await call("GET", "/api/state?since=8000", { token });
check("since= returns just the card that moved", r.payload.cards.map((c) => c.id), ["one"]);

console.log("\n— an answer taken back stays taken back —");
await call("POST", "/api/state", { token, body: { reviews: [{ at: "2026-09-15T10:00:00.000Z", id: "one", rating: 3, undone: true }] } });
r = await call("GET", "/api/state", { token });
check("the mark goes on", r.payload.reviews[0].undone, true);
await call("POST", "/api/state", { token: second, body: { reviews: [{ at: "2026-09-15T10:00:00.000Z", id: "one", rating: 3 }] } });
r = await call("GET", "/api/state", { token });
check("and a device that never heard about it cannot take it off", r.payload.reviews[0].undone, true);

console.log("\n— a whole collection in one push —");
// The first sync of an existing deck is over a thousand cards. It goes in
// chunks rather than as one batch, and the point of this is that the count
// coming back is right whatever the chunking does.
const many = Array.from({ length: 250 }, (_, i) => ({
  id: `bulk-${i}`, mod: 10000 + i, word: `word${i}`, fsrs: { state: 2, stability: i + 1 },
}));
r = await call("POST", "/api/state", { token, body: { cards: many } });
check("a 250-card push is accepted", r.status, 200);
check("and every one of them is held", r.payload.held.cards, 252);
r = await call("GET", "/api/state?since=10248", { token });
check("and the last two come back on their own", r.payload.cards.map((c) => c.id).sort(),
  ["bulk-249"]);

console.log("\n— the second phone —");
r = await call("POST", "/api/pair", { token });
check("a code is issued", r.status, 200);
const code = r.payload.code;
note("code", code);
r = await call("POST", "/api/pair/claim", { body: { code: code.toLowerCase() } });
check("typed back in any case, it signs the phone in", r.status, 200);
const third = r.payload.token;
r = await call("GET", "/api/me", { token: third });
check("as the same person", r.payload.email, "a@example.test");
r = await call("POST", "/api/pair/claim", { body: { code } });
check("and the code cannot be used twice", r.status, 401);

console.log("\n— sessions —");
r = await call("GET", "/api/me", { token });
// Four: the one registration made, the second sign-in, the extra sign-in the
// lockout test needed, and the phone that used the pairing code.
check("the account knows how many devices", r.payload.devices, 4);
await call("POST", "/api/logout", { token: third });
r = await call("GET", "/api/me", { token: third });
check("signing out ends that one", r.status, 401);
r = await call("GET", "/api/me", { token });
check("and leaves the others alone", r.status, 200);

await db.prepare("UPDATE sessions SET expires = 1 WHERE user_id = (SELECT id FROM users WHERE email = ?)")
  .bind("a@example.test").run();
r = await call("GET", "/api/me", { token });
check("an expired session is refused", r.status, 401);
check("and is cleared out as it is met",
  (await db.prepare("SELECT COUNT(*) AS n FROM sessions").first()).n, 2);

console.log("\n— nobody else's deck —");
r = await call("GET", "/api/state");
check("no token, no deck", r.status, 401);
r = await call("GET", "/api/state", { token: "made-up-token" });
check("a made-up token, no deck", r.status, 401);

const outsider = await call("POST", "/api/register", { body: { email: "z@example.test", password: "a quite long password" } });
r = await call("GET", "/api/state", { token: outsider.payload.token });
check("a different account sees an empty deck, not this one", r.payload.cards.length, 0);

console.log("\n— the vault —");
// Ciphertext in, ciphertext out. The server's job is to hold it and to keep it
// away from everybody else; it is not supposed to be able to read it, and the
// test asserts the shape of that promise rather than the promise itself.
// The first device was signed out further up, so this needs a session of its own.
const vaultToken = (await call("POST", "/api/login",
  { body: { email: "a@example.test", password: "correct horse battery" } })).payload.token;
r = await call("GET", "/api/vault", { token: vaultToken });
check("a person with no vault gets an empty one", r.payload, { blob: "", mod: 0 });

const cipher = "v1." + "x".repeat(400);
r = await call("PUT", "/api/vault", { token: vaultToken, body: { blob: cipher } });
check("a vault can be written", r.status, 200);
r = await call("GET", "/api/vault", { token: vaultToken });
check("and comes back byte for byte", r.payload.blob, cipher);

r = await call("PUT", "/api/vault", { token: vaultToken, body: { blob: "v1.later" } });
r = await call("GET", "/api/vault", { token: vaultToken });
check("writing again replaces it rather than adding a second", r.payload.blob, "v1.later");
check("one row per person",
  (await db.prepare("SELECT COUNT(*) AS n FROM vault").first()).n, 1);

// The whole point: what is on disk is not the token.
check("what is stored is the ciphertext and nothing else",
  (await db.prepare("SELECT blob FROM vault").first()).blob, "v1.later");

r = await call("GET", "/api/vault", { token: outsider.payload.token });
check("nobody else can read it", r.payload.blob, "");
r = await call("GET", "/api/vault");
check("and nor can somebody signed out", r.status, 401);

r = await call("PUT", "/api/vault", { token: vaultToken, body: { blob: "x".repeat(70000) } });
check("a vault larger than any real one is refused", r.status, 400);
r = await call("PUT", "/api/vault", { token: vaultToken, body: { blob: 42 } });
check("and so is something that is not text", r.status, 400);

r = await call("PUT", "/api/vault", { token: vaultToken, body: { blob: "" } });
check("writing nothing forgets it", r.status, 200);
r = await call("GET", "/api/vault", { token: vaultToken });
check("and it is gone", r.payload.blob, "");
r = await call("PUT", "/api/vault", { token: vaultToken, body: { blob: "v1.back again" } });

console.log("\n— leaving —");
const back = await call("POST", "/api/login", { body: { email: "a@example.test", password: "correct horse battery" } });
r = await call("GET", "/api/takeout", { token: back.payload.token });
// Two from the earlier merge checks plus the 250 pushed in bulk.
check("everything comes out in one file", r.payload.cards.length, 252);
r = await call("POST", "/api/forget-me", { token: back.payload.token });
check("and the account can be deleted", r.status, 200);
check("with nothing left behind",
  (await db.prepare("SELECT (SELECT COUNT(*) FROM users WHERE email='a@example.test') AS u, (SELECT COUNT(*) FROM cards) AS c, (SELECT COUNT(*) FROM vault) AS v").first()),
  { u: 0, c: 0, v: 0 });

console.log("\n— the edges —");
r = await call("POST", "/api/login", { body: null, headers: { "content-type": "application/json" } });
check("an empty body does not crash it", r.status, 401);
r = await mf.dispatchFetch("http://server/api/login", { method: "POST", body: "{ not json" });
check("nonsense in the body is a 400", r.status, 400);
r = await call("GET", "/api/health");
check("health answers", r.payload.ok, true);
r = await call("GET", "/api/nothing-here");
check("an unknown path is a 404", r.status, 404);
const pre = await mf.dispatchFetch("http://server/api/state", { method: "OPTIONS" });
check("preflight is answered", pre.status, 204);
check("and only for the origin the app is served from",
  pre.headers.get("access-control-allow-origin"), "https://example.test");

console.log(`\n${passed} passed, ${failed} failed\n`);
await mf.dispose();
process.exit(failed ? 1 : 0);
