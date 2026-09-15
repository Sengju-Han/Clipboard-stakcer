// The line, in the voice that said it.
//
// A card that says "put off" in a synthetic voice teaches you a word. A card
// that plays the two seconds of the show where somebody actually said it
// teaches you the word, the stress, the speed, and the fact that the "t"
// disappears — and it does it with a scene attached, which is most of why it
// sticks. This is the single feature the desktop mining tools are really for,
// and it has never worked on a phone because it needed an add-on.
//
// It works here because it needs nothing but the file already open: the media
// element is asked for its own audio as a stream, the line is played once, and
// what comes out is recorded. That costs exactly as long as the line lasts,
// which is two or three seconds, and it is two or three seconds of hearing the
// line again — which is what you wanted anyway.
//
// The other route — decode the whole file and cut the range out of it — is
// instant and is not used. A forty-minute episode decodes to the better part
// of a gigabyte of raw samples, and the phone this is written for would stop.

const PREFERRED = [
  "audio/webm;codecs=opus",
  "audio/ogg;codecs=opus",
  "audio/mp4",
  "audio/webm",
];

export function canCapture(media) {
  return Boolean(media && typeof media.captureStream === "function" &&
    typeof window.MediaRecorder === "function" && pickType());
}

function pickType() {
  return PREFERRED.find((t) => {
    try { return MediaRecorder.isTypeSupported(t); } catch { return false; }
  }) || "";
}

function extensionFor(type) {
  if (type.startsWith("audio/ogg")) return "ogg";
  if (type.startsWith("audio/mp4")) return "m4a";
  return "webm";
}

// A little air either side: subtitle timings are cut tight and a clip that
// starts exactly on the timestamp loses the first consonant.
const LEAD = 0.25;
const TAIL = 0.35;
const CEILING = 20;        // a line longer than this is a scene, not a line

export async function captureLine(media, start, end, { onProgress = () => {} } = {}) {
  if (!canCapture(media)) throw new Error("This browser cannot capture audio from a video.");
  const type = pickType();
  const from = Math.max(0, start - LEAD);
  const to = Math.min(end + TAIL, from + CEILING, media.duration || end + TAIL);
  const seconds = Math.max(0.4, to - from);

  const stream = media.captureStream();
  if (!stream.getAudioTracks().length) {
    throw new Error("That file has no audio track to capture.");
  }

  const rec = new MediaRecorder(stream, { mimeType: type });
  const parts = [];
  rec.ondataavailable = (e) => { if (e.data && e.data.size) parts.push(e.data); };

  const wasPaused = media.paused;
  const wasAt = media.currentTime;

  const finished = new Promise((resolve, reject) => {
    rec.onstop = resolve;
    rec.onerror = () => reject(new Error("The recording stopped unexpectedly."));
  });

  media.currentTime = from;
  await once(media, "seeked", 4000);
  onProgress(`Listening to ${seconds.toFixed(1)}s…`);
  await media.play();
  rec.start();

  await new Promise((resolve) => setTimeout(resolve, seconds * 1000));
  rec.stop();
  await finished;

  // Put the film back where it was. Mining a line should not also be a way to
  // lose your place in the episode.
  media.currentTime = wasAt;
  if (wasPaused) media.pause();

  const blob = new Blob(parts, { type });
  if (!blob.size) throw new Error("Nothing was recorded. The video may be muted at the system level.");
  return { blob, extension: extensionFor(type), seconds: Number(seconds.toFixed(2)) };
}

function once(target, event, timeout) {
  return new Promise((resolve) => {
    let done = false;
    const finish = () => { if (!done) { done = true; target.removeEventListener(event, finish); resolve(); } };
    target.addEventListener(event, finish);
    setTimeout(finish, timeout);
  });
}

// A name that cannot collide with a file from the person's own Anki collection
// and says where it came from.
export function nameFor(word, extension) {
  const stem = String(word).toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 40);
  return `lexis-${stem || "line"}-${Date.now().toString(36)}.${extension}`;
}
