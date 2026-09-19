// Where the server is.
//
// This used to be a box on both pages. There is one server and one person
// using it, so asking which one every time was a question with a single
// possible answer — and getting it wrong, or losing it on a sign-out, looked
// exactly like the whole account being broken.
//
// It is not a secret. It is a public address behind a login, and the server
// only answers requests from the page this is served from.

const DEFAULT = "https://lexis.eolgulnae.workers.dev";

// An override, with no box for it. It exists so the tests can point somewhere
// else, and so a moved Worker can be pointed at without a deploy — not as
// something anybody is expected to set.
const OVERRIDE = "lexis:server";

export function serverUrl() {
  let set = "";
  try { set = localStorage.getItem(OVERRIDE) || ""; } catch { /* blocked */ }
  return (set || DEFAULT).replace(/\/+$/, "");
}
