// Passwords, sessions and the small amount of care they need.
//
// Nothing clever here on purpose. PBKDF2 is what the platform gives a Worker
// — no argon2, no scrypt, no native module — and used properly it is fine.
// Everything that can be got wrong in a way that does not show up in testing
// is done explicitly: the iteration count is stored per user so it can be
// raised later without locking anyone out, comparisons are constant time, and
// the session token itself is never written down.

const ITERATIONS = 210000;          // OWASP's 2023 figure for PBKDF2-HMAC-SHA256
const KEY_BITS = 256;
const SALT_BYTES = 16;
const TOKEN_BYTES = 32;

export function base64(bytes) {
  let binary = "";
  const view = new Uint8Array(bytes);
  for (let i = 0; i < view.length; i += 1) binary += String.fromCharCode(view[i]);
  return btoa(binary);
}

export function unbase64(text) {
  return Uint8Array.from(atob(text), (c) => c.charCodeAt(0));
}

export function randomBytes(n) {
  return crypto.getRandomValues(new Uint8Array(n));
}

export async function derive(password, salt, iterations = ITERATIONS) {
  const key = await crypto.subtle.importKey(
    "raw", new TextEncoder().encode(password), "PBKDF2", false, ["deriveBits"],
  );
  const bits = await crypto.subtle.deriveBits(
    { name: "PBKDF2", hash: "SHA-256", salt, iterations }, key, KEY_BITS,
  );
  return new Uint8Array(bits);
}

export async function hashPassword(password) {
  const salt = randomBytes(SALT_BYTES);
  const hash = await derive(password, salt, ITERATIONS);
  return { hash: base64(hash), salt: base64(salt), iterations: ITERATIONS };
}

// Constant time: a comparison that returns early tells an attacker how much of
// the hash they got right, one byte at a time.
export function sameBytes(a, b) {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i += 1) diff |= a[i] ^ b[i];
  return diff === 0;
}

export async function checkPassword(password, user) {
  const hash = await derive(password, unbase64(user.pw_salt), user.iterations);
  return sameBytes(hash, unbase64(user.pw_hash));
}

// The token goes to the browser once and is never stored anywhere on this side.
// What the database holds is its SHA-256, so the table is useless to anyone who
// reads it.
export function newToken() {
  return base64(randomBytes(TOKEN_BYTES)).replace(/[+/=]/g, (c) => ({ "+": "-", "/": "_", "=": "" }[c]));
}

export async function tokenHash(token) {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(token));
  return base64(digest);
}

export function id() {
  return base64(randomBytes(12)).replace(/[+/=]/g, (c) => ({ "+": "-", "/": "_", "=": "" }[c]));
}

// A pairing code is read off one screen and typed into another, so it avoids
// the characters people get wrong: no 0/O, no 1/I/l, no 5/S.
const ALPHABET = "ABCDEFGHJKMNPQRTUVWXY2346789";
export function pairingCode() {
  const bytes = randomBytes(8);
  let out = "";
  for (let i = 0; i < 8; i += 1) {
    out += ALPHABET[bytes[i] % ALPHABET.length];
    if (i === 3) out += "-";
  }
  return out;
}
