// The two keys, kept once and readable from either page.
//
// A GitHub token and an Anthropic key are needed in two places: the Add to
// Anki page, which dispatches workflows and looks words up, and the review
// app, which looks words up and can sync through a repository. They were typed
// into both, separately, on every device — which is most of the reason a page
// gets opened somewhere new and then abandoned.
//
// So they live in one box per account. It is locked here, in the browser, with
// a key derived from the password, and the server is handed something it has
// no way to open. That is also why a forgotten password cannot be recovered:
// there is nothing on the other side that knows what is inside.
//
// The salt travels inside the box rather than being remembered anywhere, so a
// new device fetches exactly one thing and that one thing is enough.

const VERSION = "v1";
const ITERATIONS = 210000;   // the same figure the server uses for passwords

const b64 = (bytes) => btoa(String.fromCharCode(...new Uint8Array(bytes)));
const unb64 = (text) => Uint8Array.from(atob(text), (c) => c.charCodeAt(0));

// One shape, so the two pages cannot disagree about what a field is called.
// Each page maps its own names onto these on the way in and out.
export const EMPTY = {
  githubToken: "", githubOwner: "", githubRepo: "", githubRef: "main",
  anthropic: "", voice: "en-US-AvaNeural",
};

/** Only the known keys, only strings: whatever else is in there is not ours. */
export function tidy(values) {
  const out = { ...EMPTY };
  for (const key of Object.keys(EMPTY)) {
    if (typeof values?.[key] === "string") out[key] = values[key];
  }
  // The first version of this stored the Add to Anki page's own field names.
  // Reading them is four lines; not reading them means somebody who already
  // made an account finds it empty and has to set it up again.
  const older = {
    token: "githubToken", owner: "githubOwner",
    repo: "githubRepo", ref: "githubRef", anthropic: "anthropic", voice: "voice",
  };
  for (const [was, now] of Object.entries(older)) {
    if (!out[now] && typeof values?.[was] === "string") out[now] = values[was];
  }
  return out;
}

async function keyFrom(password, salt) {
  const base = await crypto.subtle.importKey(
    "raw", new TextEncoder().encode(password), "PBKDF2", false, ["deriveKey"],
  );
  return crypto.subtle.deriveKey(
    { name: "PBKDF2", hash: "SHA-256", salt, iterations: ITERATIONS },
    base, { name: "AES-GCM", length: 256 }, false, ["encrypt", "decrypt"],
  );
}

export async function lock(values, password) {
  const salt = crypto.getRandomValues(new Uint8Array(16));
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const key = await keyFrom(password, salt);
  const plain = new TextEncoder().encode(JSON.stringify(tidy(values)));
  const sealed = await crypto.subtle.encrypt({ name: "AES-GCM", iv }, key, plain);
  return [VERSION, b64(salt), b64(iv), b64(sealed)].join(".");
}

export async function unlock(blob, password) {
  const [version, salt, iv, sealed] = String(blob).split(".");
  if (version !== VERSION) throw new Error("This was locked by a newer version of the page.");
  const key = await keyFrom(password, unb64(salt));
  // AES-GCM refuses rather than returning nonsense, so a wrong password throws
  // here and can be told apart from an empty or damaged vault.
  const plain = await crypto.subtle.decrypt({ name: "AES-GCM", iv: unb64(iv) }, key, unb64(sealed));
  return tidy(JSON.parse(new TextDecoder().decode(plain)));
}

// ---- talking to the server -------------------------------------------------

async function call(base, path, { token, method = "GET", body = null } = {}) {
  const res = await fetch(base.replace(/\/+$/, "") + path, {
    method,
    headers: {
      ...(body ? { "content-type": "application/json" } : {}),
      ...(token ? { authorization: `Bearer ${token}` } : {}),
    },
    body: body ? JSON.stringify(body) : undefined,
  });
  let payload = null;
  try { payload = await res.json(); } catch { /* an error page, not JSON */ }
  if (!res.ok) throw new Error(payload?.error || `The server said ${res.status}.`);
  return payload || {};
}

/** What is stored, opened. Null when there is nothing stored yet. */
export async function fetchVault(base, token, password) {
  const { blob } = await call(base, "/api/vault", { token });
  if (!blob) return null;
  return unlock(blob, password);
}

export async function saveVault(base, token, password, values) {
  await call(base, "/api/vault", {
    token, method: "PUT", body: { blob: await lock(values, password) },
  });
}
