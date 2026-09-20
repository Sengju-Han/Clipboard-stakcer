// The voice, for the Speak screen.
//
// The cards already sound like a person: anki/build_tts_apkg.py generates their
// audio with Microsoft Edge's neural voices, and en-US-AvaNeural is what plays
// when you tap a card. The Speak screen did not - it used the phone's own
// speechSynthesis, which on Android is a flat, clipped, unmistakably synthetic
// voice. The same app in the same session, one half human and one half robot.
//
// The browser cannot fetch the neural voice itself. The service checks an
// Origin header of `chrome-extension://...`, and a page is not allowed to set
// Origin on a WebSocket - the browser writes it. A Worker can, so the swap
// happens here: text in, mp3 out, and the app plays it.
//
// Unofficial, and reverse-engineered by the edge-tts project rather than
// documented by Microsoft. anki/TTS.md already says what that means and it is
// worth repeating: if this stops working, change provider rather than debug it.
// Nothing depends on it - the app falls back to the phone's own voice, which is
// exactly what it used before.

const TRUSTED_CLIENT_TOKEN = "6A5AA1D4EAFF4E9FB37E23D68491D6F4";
const BASE = "speech.platform.bing.com/consumer/speech/synthesize/readaloud";
const CHROMIUM = "143.0.3650.75";
// Windows counts from 1601 and in 100-nanosecond ticks, which overflows a
// double: 1.4e17 is past Number.MAX_SAFE_INTEGER, so this is BigInt throughout.
const WIN_EPOCH = 11644473600n;
const TICKS_PER_SECOND = 10000000n;
const ROUND_TO = 300n;              // the token changes every five minutes

const FORMAT = "audio-24khz-48kbitrate-mono-mp3";

// A turn of conversation, not a chapter. The Speak screen sends one or two
// sentences; anything much longer is a mistake or somebody else's traffic.
export const MAX_TEXT = 600;
// Long enough for a slow sentence, short enough that a hung socket does not
// hold a request open. Generation is usually well under a second.
const TIMEOUT_MS = 15000;

// The ones anki/TTS.md recommends, so the app and the cards can sound the same.
// Anything else is refused rather than passed through: this is a public route,
// and an unchecked voice name is an unchecked string going into SSML.
export const VOICES = [
  "en-US-AvaNeural",
  "en-US-AndrewNeural",
  "en-US-EmmaNeural",
  "en-US-BrianNeural",
  "en-GB-SoniaNeural",
  "en-GB-RyanNeural",
];
export const DEFAULT_VOICE = "en-US-AvaNeural";

const hex = (buffer) => [...new Uint8Array(buffer)]
  .map((b) => b.toString(16).padStart(2, "0")).join("").toUpperCase();

/** The Sec-MS-GEC token: SHA-256 of the current Windows file time and the token. */
export async function secMsGec(atMs = Date.now()) {
  let seconds = BigInt(Math.floor(atMs / 1000)) + WIN_EPOCH;
  seconds -= seconds % ROUND_TO;
  const filetime = seconds * TICKS_PER_SECOND;
  const digest = await crypto.subtle.digest(
    "SHA-256", new TextEncoder().encode(`${filetime}${TRUSTED_CLIENT_TOKEN}`),
  );
  return hex(digest);
}

const DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
  "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const pad = (n) => String(n).padStart(2, "0");

/** The date format the service expects, which is a JavaScript Date.toString(). */
export function timestamp(atMs = Date.now()) {
  const d = new Date(atMs);
  return `${DAYS[d.getUTCDay()]} ${MONTHS[d.getUTCMonth()]} ${pad(d.getUTCDate())} `
    + `${d.getUTCFullYear()} ${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}:`
    + `${pad(d.getUTCSeconds())} GMT+0000 (Coordinated Universal Time)`;
}

/** A connection id: a UUID with the dashes taken out. */
const connectionId = () => crypto.randomUUID().replace(/-/g, "");

// The service refuses a few control characters outright - a vertical tab in
// pasted text is the usual way one arrives - so they become spaces rather than
// a failed request with no explanation.
const printable = (text) => [...String(text)]
  .map((ch) => {
    const code = ch.codePointAt(0);
    const control = (code <= 8) || (code >= 11 && code <= 12) || (code >= 14 && code <= 31);
    return control ? " " : ch;
  }).join("");

const escapeXml = (text) => printable(text)
  .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

/**
 * The long name the service wants.
 *
 * `en-US-AvaNeural` is the name everywhere else in this project - the workflow
 * input, TTS.md, the file names - but what goes into the SSML is
 * `Microsoft Server Speech Text to Speech Voice (en-US, AvaNeural)`. Edge sends
 * the long form and edge-tts follows it, so this does too. Found by comparing
 * the bytes rather than by reading, which is the only way it would have been
 * found before the first real request.
 */
export function longVoiceName(short) {
  const match = /^([a-z]{2,})-([A-Z]{2,})-(.+Neural)$/.exec(short);
  if (!match) return short;
  let [, lang, region, name] = match;
  // A name with a hyphen in it carries a sub-region: fil-PH-AngeloNeural, and
  // the handful of voices like en-US-Ava-something.
  const dash = name.indexOf("-");
  if (dash !== -1) {
    region = `${region}-${name.slice(0, dash)}`;
    name = name.slice(dash + 1);
  }
  return `Microsoft Server Speech Text to Speech Voice (${lang}-${region}, ${name})`;
}

export function ssmlFor(text, voice) {
  return "<speak version='1.0' xmlns='http://www.w3.org/2001/10/synthesis' xml:lang='en-US'>"
    + `<voice name='${longVoiceName(voice)}'>`
    + "<prosody pitch='+0Hz' rate='+0%' volume='+0%'>"
    + escapeXml(text)
    + "</prosody></voice></speak>";
}

export function configFrame(at = Date.now()) {
  return `X-Timestamp:${timestamp(at)}\r\n`
    + "Content-Type:application/json; charset=utf-8\r\n"
    + "Path:speech.config\r\n\r\n"
    // Sentence boundaries on and word boundaries off is exactly what edge-tts
    // sends, and edge-tts is the only evidence that any of this is accepted.
    // The metadata frames it produces are ignored below; matching a request
    // that is known to work beats sending a tidier one that is not.
    + '{"context":{"synthesis":{"audio":{"metadataoptions":{'
    + '"sentenceBoundaryEnabled":"true","wordBoundaryEnabled":"false"},'
    + `"outputFormat":"${FORMAT}"`
    + "}}}}\r\n";
}

export function ssmlFrame(text, voice, requestId, at = Date.now()) {
  return `X-RequestId:${requestId}\r\n`
    + "Content-Type:application/ssml+xml\r\n"
    // The trailing Z is wrong and deliberate: the service sends its own
    // timestamps that way and rejects the corrected form.
    + `X-Timestamp:${timestamp(at)}Z\r\n`
    + "Path:ssml\r\n\r\n"
    + ssmlFor(text, voice);
}

/** The Path header of a frame, text or binary, or "" when there is none. */
export function pathOf(frame) {
  if (typeof frame === "string") {
    const end = frame.indexOf("\r\n\r\n");
    return (/(?:^|\r\n)Path:(.*)(?:\r\n|$)/.exec(frame.slice(0, end < 0 ? undefined : end))
      || [, ""])[1].trim();
  }
  const bytes = new Uint8Array(frame);
  if (bytes.length < 2) return "";
  const headerLength = (bytes[0] << 8) | bytes[1];
  if (headerLength > bytes.length) return "";
  const headers = new TextDecoder().decode(bytes.slice(2, 2 + headerLength));
  return (/(?:^|\r\n)Path:(.*)(?:\r\n|$)/.exec(headers) || [, ""])[1].trim();
}

/** The audio out of a binary frame: everything after its headers. */
export function audioOf(frame) {
  const bytes = new Uint8Array(frame);
  if (bytes.length < 2) return new Uint8Array(0);
  const headerLength = (bytes[0] << 8) | bytes[1];
  if (headerLength > bytes.length) return new Uint8Array(0);
  // Two for the length itself, then the headers, then the blank line.
  return bytes.slice(2 + headerLength);
}

export function endpoint(base, token, id) {
  return `${base}?TrustedClientToken=${TRUSTED_CLIENT_TOKEN}`
    + `&ConnectionId=${id}&Sec-MS-GEC=${token}&Sec-MS-GEC-Version=1-${CHROMIUM}`;
}

function headers() {
  const agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    + " (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36 Edg/143.0.0.0";
  return {
    Upgrade: "websocket",
    Pragma: "no-cache",
    "Cache-Control": "no-cache",
    // The header a page cannot set, and the whole reason this lives here.
    Origin: "chrome-extension://jdiccldimpdaibmpdkjnbmckianbfold",
    "User-Agent": agent,
    "Accept-Language": "en-US,en;q=0.9",
  };
}

/**
 * The mp3 for one short piece of text, spoken by one of the voices above.
 *
 * Throws with a sentence rather than a code: whatever goes wrong here, the app
 * falls back to the phone's own voice, and the sentence is for whoever is
 * reading the Worker's log afterwards wondering why.
 */
export async function speak(text, voice, { wss = `wss://${BASE}/edge/v1`, at = Date.now() } = {}) {
  const said = String(text || "").trim();
  if (!said) throw new Error("Nothing to say.");
  if (said.length > MAX_TEXT) throw new Error(`Longer than the ${MAX_TEXT} characters this speaks in one go.`);
  if (!VOICES.includes(voice)) throw new Error(`${voice} is not one of the voices this offers.`);

  const id = connectionId();
  const url = endpoint(wss, await secMsGec(at), id);
  // fetch speaks http, and an Upgrade header is what makes it a socket. ws:
  // is here for the tests, which point this at a server on this machine.
  const over = url.replace(/^wss:/, "https:").replace(/^ws:/, "http:");
  const response = await fetch(over, { headers: headers() });
  const socket = response.webSocket;
  if (!socket) {
    // 403 here is the service refusing the connection outright, which is what
    // a stale Sec-MS-GEC or a retired client token looks like.
    throw new Error(`The voice service answered ${response.status} rather than opening a connection.`);
  }
  socket.accept();

  const chunks = [];
  let total = 0;
  try {
    await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error("The voice service did not finish in time.")), TIMEOUT_MS);
      const done = (err) => { clearTimeout(timer); err ? reject(err) : resolve(); };

      socket.addEventListener("message", (event) => {
        try {
          const frame = event.data;
          if (typeof frame === "string") {
            if (pathOf(frame) === "turn.end") done();
            return;
          }
          if (pathOf(frame) !== "audio") return;
          const audio = audioOf(frame);
          if (audio.length) { chunks.push(audio); total += audio.length; }
        } catch (err) {
          done(err instanceof Error ? err : new Error(String(err)));
        }
      });
      socket.addEventListener("error", () => done(new Error("The connection to the voice service failed.")));
      socket.addEventListener("close", () => {
        // turn.end already resolved this in the ordinary case; a close before
        // it means the service hung up, and no audio is the only way to tell.
        done(total ? undefined : new Error("The voice service closed without sending any audio."));
      });

      socket.send(configFrame(at));
      socket.send(ssmlFrame(said, voice, id, at));
    });
  } finally {
    try { socket.close(); } catch { /* already gone */ }
  }

  if (!total) throw new Error("The voice service sent no audio.");
  const mp3 = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) { mp3.set(chunk, offset); offset += chunk.length; }
  return mp3;
}
