// An account, for someone who has never heard of GitHub.
//
// Syncing through your own repository works and costs nothing, and it asks you
// to make a personal access token with the right scope, which is a sentence
// most people stop reading halfway through. So there is a server now, and this
// is the part of the app that talks to it: an email, a password, and the same
// deck on both phones.
//
// It is an alternative, not a replacement. The repository backend stays, the
// deck still lives in this browser, and the review loop still never touches
// the network. Everything here is optional.

import { derivedSecret, MIN_PASSWORD } from "./secret.js";

const KEY = "lexis:account";

export function saved() {
  try { return JSON.parse(localStorage.getItem(KEY) || "null"); } catch { return null; }
}

function keep(account) {
  try {
    if (account) localStorage.setItem(KEY, JSON.stringify(account));
    else localStorage.removeItem(KEY);
  } catch { /* blocked */ }
}

async function call(base, path, { token = "", method = "GET", body = null } = {}) {
  let res;
  try {
    res = await fetch(base.replace(/\/+$/, "") + path, {
      method,
      headers: {
        ...(body ? { "content-type": "application/json" } : {}),
        ...(token ? { authorization: `Bearer ${token}` } : {}),
      },
      body: body ? JSON.stringify(body) : undefined,
    });
  } catch {
    throw new Error("Could not reach the server. Check the address, or try again when you have signal.");
  }
  const payload = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(payload.error || `The server said ${res.status}.`);
  return payload;
}

// The password stops here. What crosses the network is derived from it, and
// the length check happens here too because this is the only side that can see
// what was typed.
export async function register(base, email, password) {
  if (String(password || "").length < MIN_PASSWORD) {
    throw new Error(`A password needs at least ${MIN_PASSWORD} characters. Length is what `
      + "matters; a short phrase beats a mangled word.");
  }
  const secret = await derivedSecret(email, password);
  const out = await call(base, "/api/register", { method: "POST", body: { email, secret } });
  keep({ base, token: out.token, email: out.email });
  return out;
}

export async function signIn(base, email, password) {
  const secret = await derivedSecret(email, password);
  const out = await call(base, "/api/login", { method: "POST", body: { email, secret } });
  keep({ base, token: out.token, email: out.email });
  return out;
}

export async function signOut() {
  const account = saved();
  keep(null);
  if (!account) return;
  // Best effort: the token is already gone from this device either way, and a
  // sign-out that fails because the train went into a tunnel should not leave
  // the person still signed in on the screen.
  try { await call(account.base, "/api/logout", { method: "POST", token: account.token }); } catch { /* gone anyway */ }
}

export async function pairCode() {
  const account = must();
  return call(account.base, "/api/pair", { method: "POST", token: account.token });
}

export async function usePairCode(base, code) {
  const out = await call(base, "/api/pair/claim", { method: "POST", body: { code } });
  keep({ base, token: out.token, email: "" });
  const who = await call(base, "/api/me", { token: out.token });
  keep({ base, token: out.token, email: who.email });
  return who;
}

export async function whoami() {
  const account = must();
  return call(account.base, "/api/me", { token: account.token });
}

export async function forgetMe() {
  const account = must();
  await call(account.base, "/api/forget-me", { method: "POST", token: account.token });
  keep(null);
}

function must() {
  const account = saved();
  if (!account?.token) throw new Error("Not signed in.");
  return account;
}

// ---- syncing -------------------------------------------------------------
//
// Only what changed, in both directions. Sending 1,177 cards every time works
// and is two megabytes of somebody's mobile data for an evening's six answers.
//
// The marker is the largest modification time the server has ever reported.
// A card is pushed when it has been touched since then, which is exactly the
// cards this device has changed; a review is pushed when it is newer than the
// last one sent, or when it has been taken back — an undone mark can land on a
// row from three months ago and still has to travel.

export async function syncAccount(store, onProgress = () => {}) {
  const account = must();
  const mark = (await store.meta("account-sync")) || { cards: 0, reviews: "" };

  onProgress("Asking what changed…");
  const remote = await call(account.base, `/api/state?since=${mark.cards || 0}`, { token: account.token });

  onProgress("Merging…");
  const { merge } = await import("./sync.js");
  const local = await store.allRows();          // tombstones travel too
  const merged = merge(local, { cards: remote.cards, reviews: [] });

  const reviews = new Map();
  for (const entry of remote.reviews || []) reviews.set(entry.at, entry);
  for (const entry of await store.wholeLog()) {
    const theirs = reviews.get(entry.at);
    // The mark only ever goes on.
    reviews.set(entry.at, theirs?.undone && !entry.undone ? { ...entry, undone: true } : entry);
  }
  const allReviews = [...reviews.values()].sort((a, b) => (a.at < b.at ? -1 : 1));

  await store.putCards(merged.cards);
  await store.putLog(allReviews);

  onProgress("Sending what this device changed…");
  const mine = merged.cards.filter((c) => (c.mod || 0) > (mark.cards || 0));
  const myReviews = allReviews.filter((r) => r.at > (mark.reviews || "") || r.undone);
  const sent = await call(account.base, "/api/state", {
    method: "POST", token: account.token, body: { cards: mine, reviews: myReviews },
  });

  // The new marker is the largest modification time either side now holds. It
  // is read back from what was merged rather than from the clock, so a phone
  // whose clock is wrong cannot skip its own cards next time.
  const highest = merged.cards.reduce((n, c) => Math.max(n, c.mod || 0), remote.now || 0);
  const lastReview = allReviews.length ? allReviews[allReviews.length - 1].at : (mark.reviews || "");
  await store.setMeta("account-sync", { cards: highest, reviews: lastReview, at: Date.now() });

  return {
    cards: merged.cards,
    reviews: allReviews,
    pushed: mine.length,
    pulled: (remote.cards || []).length,
    held: sent.held,
    email: account.email,
  };
}
