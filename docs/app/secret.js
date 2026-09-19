// Turning a password into something safe to send.
//
// The server never receives the password. It receives this: 256 bits derived
// from the password with 210,000 rounds of PBKDF2-HMAC-SHA256, salted with the
// email address so that two people who pick the same password do not send the
// same thing.
//
// The rounds happen here because they cannot happen there — Workers refuse
// PBKDF2 above 100,000 iterations, and the cost of even that exceeds a free
// plan's CPU budget for one request. But it is also simply the better place
// for them. A server that has never seen a password cannot leak one, and the
// difference shows up on the day somebody reads the database.
//
// The email is the salt rather than something random because the salt has to
// be known before signing in, on a device that has never spoken to the server.
// It is not secret and does not need to be: its job is to stop one precomputed
// table from working against every account at once.

const ITERATIONS = 210000;   // OWASP's figure for PBKDF2-HMAC-SHA256
const BITS = 256;

// Long enough that the derivation is the slow part rather than the guessing.
// Checked here, because here is the only place the password exists.
export const MIN_PASSWORD = 10;

function base64(bytes) {
  let binary = "";
  const view = new Uint8Array(bytes);
  for (let i = 0; i < view.length; i += 1) binary += String.fromCharCode(view[i]);
  return btoa(binary);
}

/** The 44-character value to send in place of a password. */
export async function derivedSecret(email, password) {
  const key = await crypto.subtle.importKey(
    "raw", new TextEncoder().encode(password), "PBKDF2", false, ["deriveBits"],
  );
  const bits = await crypto.subtle.deriveBits(
    {
      name: "PBKDF2",
      hash: "SHA-256",
      // Tidied the same way the server tidies it, or the same person typing
      // their address with a capital letter derives a different secret and
      // cannot sign in.
      salt: new TextEncoder().encode(String(email || "").trim().toLowerCase()),
      iterations: ITERATIONS,
    },
    key, BITS,
  );
  return base64(bits);
}
