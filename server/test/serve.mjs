// The Worker on a real port, so a browser can talk to it for real.
//
// The account suite drives the page against a stubbed server, and Playwright's
// route stubbing answers a request before the browser's own CORS check runs.
// That is how "Access-Control-Allow-Methods: GET, POST, OPTIONS" survived for a
// week while the vault was written with PUT: every browser refused the request
// before it left, reported "Failed to fetch", and the test suite saw a stub
// answering happily. The only way to catch that is a real request to a real
// server on a real origin, which is what this is for.
//
//   node server/test/serve.mjs <allowed-origin> [port]
//
// It prints one line of JSON when it is listening, and stops when stdin closes
// or it is killed - so a test that dies does not leave a Worker behind.

import { Miniflare } from "miniflare";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const origin = process.argv[2] || "*";
const port = Number(process.argv[3] || 0);

const mf = new Miniflare({
  modules: true,
  scriptPath: join(here, "..", "src", "index.js"),
  modulesRoot: join(here, "..", "src"),
  modulesRules: [{ type: "ESModule", include: ["**/*.js"] }],
  d1Databases: { DB: "lexis-browser-test" },
  bindings: { ALLOWED_ORIGIN: origin },
  compatibilityDate: "2026-01-01",
  host: "127.0.0.1",
  port,
});

// Same flattening as the unit tests: exec() wants one statement per line, and
// a `--` comment folded onto one line swallows the statement after it.
const schema = readFileSync(join(here, "..", "schema.sql"), "utf8");
const db = await mf.getD1Database("DB");
for (const statement of schema
  .split("\n").map((line) => line.replace(/--.*$/, "")).join("\n")
  .split(";").map((s) => s.replace(/\s+/g, " ").trim()).filter(Boolean)) {
  await db.exec(statement + ";");
}

const url = await mf.ready;
console.log(JSON.stringify({ ready: true, url: url.origin, origin }));

const stop = async () => { try { await mf.dispose(); } catch {} process.exit(0); };
process.on("SIGTERM", stop);
process.on("SIGINT", stop);
process.stdin.on("close", stop);
process.stdin.on("end", stop);
process.stdin.resume();
