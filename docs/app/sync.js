// Your cards on both devices.
//
// "A server" is what this needs, but a server is an account, a bill and a thing
// to keep running. You already have a private repository and a token that
// reaches it, so that is the store: one JSON file, written with the GitHub
// contents API, read back on the other device. No signup, no hosting, and the
// data stays somewhere you already control.
//
// The backend is behind one small interface (read, write) so that when this
// outgrows a repository — several users, a real login — only the adapter is
// replaced and the merge below stays exactly as it is. The merge is the hard
// part and it is not GitHub-specific.

const PATH = "lexis/state.json";
const API = "https://api.github.com";

// ---- the merge -----------------------------------------------------------
//
// Two devices, one word, two answers. Last write wins is wrong here: it keeps
// whichever device pushed most recently, which can be the one that answered
// first. Every card carries the time it was actually changed, so the newer
// change wins wherever it happened.
//
// Reviews are different: they are events, not state. Nothing overwrites a
// review, so the two logs are unioned by their timestamp and the history ends
// up complete on both devices even if the card state disagrees.
export function merge(local, remote) {
  const cards = new Map();
  const detail = { kept: 0, taken: 0, added: 0, unchanged: 0 };

  for (const card of remote.cards || []) cards.set(card.id, card);

  for (const card of local) {
    const theirs = cards.get(card.id);
    if (!theirs) { cards.set(card.id, card); detail.added += 1; continue; }

    const mine = card.mod || 0;
    const yours = theirs.mod || 0;
    if (mine > yours) { cards.set(card.id, card); detail.kept += 1; }
    else if (yours > mine) { detail.taken += 1; }
    else { detail.unchanged += 1; }
  }

  // A review is keyed by the instant it happened, so the union is free of
  // duplicates without comparing anything.
  const reviews = new Map();
  for (const entry of remote.reviews || []) reviews.set(entry.at, entry);
  return {
    cards: [...cards.values()],
    reviews,
    detail,
  };
}

// ---- the GitHub adapter --------------------------------------------------
function headers(token) {
  return {
    Accept: "application/vnd.github+json",
    Authorization: "Bearer " + token,
    "X-GitHub-Api-Version": "2022-11-28",
  };
}

function decode(base64) {
  const bytes = Uint8Array.from(atob(String(base64).replace(/\s/g, "")), (c) => c.charCodeAt(0));
  return JSON.parse(new TextDecoder().decode(bytes));
}

function encode(text) {
  const bytes = new TextEncoder().encode(text);
  let binary = "";
  // Chunked: a 1,177-card state is megabytes, and String.fromCharCode applied
  // to the whole array at once overflows the argument limit and throws.
  for (let i = 0; i < bytes.length; i += 0x8000) {
    binary += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
  }
  return btoa(binary);
}

export function github({ token, owner, repo, branch = "main" }) {
  const base = `${API}/repos/${owner}/${repo}/contents/${PATH}`;

  return {
    describe: `${owner}/${repo}`,

    async read() {
      const res = await fetch(`${base}?ref=${encodeURIComponent(branch)}&t=${Date.now()}`,
        { headers: headers(token), cache: "no-store" });
      // Nothing there yet is the normal first run, not a failure.
      if (res.status === 404) return { state: { cards: [], reviews: [] }, sha: null };
      if (!res.ok) throw new Error(await reason(res));
      const payload = await res.json();
      return { state: decode(payload.content), sha: payload.sha };
    },

    async write(state, sha) {
      const body = {
        message: `Sync ${state.cards.length} cards`,
        content: encode(JSON.stringify(state)),
        branch,
      };
      // The sha is what makes this safe: if the other device wrote while this
      // one was merging, GitHub refuses rather than overwriting their work.
      if (sha) body.sha = sha;
      const res = await fetch(base, { method: "PUT", headers: headers(token), body: JSON.stringify(body) });
      if (res.status === 409) {
        throw new Error("The other device wrote while this one was syncing. Try again — nothing was lost.");
      }
      if (!res.ok) throw new Error(await reason(res));
      return true;
    },
  };
}

async function reason(res) {
  let said = "";
  try { said = (await res.json()).message || ""; } catch { /* not JSON */ }
  if (res.status === 401) return "GitHub rejected the token. Check it has not expired.";
  if (res.status === 403) return "The token cannot write here. It needs Contents: read and write on this repository.";
  if (res.status === 404) return "Repository not found, or the token cannot see it.";
  return said || `GitHub returned ${res.status}.`;
}

// ---- the round trip ------------------------------------------------------
export async function sync(backend, { cards, reviews }, onProgress = () => {}) {
  onProgress("Reading what the other device left…");
  const { state, sha } = await backend.read();

  onProgress("Merging…");
  const merged = merge(cards, state);
  for (const entry of reviews) merged.reviews.set(entry.at, entry);
  const reviewList = [...merged.reviews.values()].sort((a, b) => (a.at < b.at ? -1 : 1));

  const next = {
    version: 1,
    synced_at: new Date().toISOString(),
    cards: merged.cards,
    reviews: reviewList,
  };

  onProgress("Sending…");
  await backend.write(next, sha);

  return {
    cards: merged.cards,
    reviews: reviewList,
    detail: merged.detail,
    remoteCards: (state.cards || []).length,
  };
}
