// Which of these words do you already know.
//
// This is the whole trick behind watching a show and learning from it. A
// subtitle line is not interesting because it is a subtitle line; it is
// interesting because of the one word in it you do not have yet. So every word
// on screen is matched against the deck, and a sentence carrying exactly one
// unknown word is worth more than a sentence carrying six — the first one you
// can work out, the second one you can only look up.
//
// The matching is deliberately crude. A real lemmatiser is a dictionary and a
// megabyte; the endings below are about fifty lines and get "avowed" back to
// "avow", which is the entire job. Where it fails it fails towards "unknown",
// which shows you a word you already had rather than hiding one you did not.

// A hyphen ends a word here, and that is not a detail: the deck's own index
// splits on hyphens, so "tie-dye" is stored as the two-part phrase tie + dye.
// A tokeniser that kept the hyphen handed it back as one token, which matched
// neither the phrase nor any single word - and twelve of the thirteen
// hyphenated words in this deck read as "not in your deck" while watching,
// inviting a second card for a word already held. Both sides split now.
const WORD = /[\p{L}\p{M}'’]+/gu;

// Irregular forms, because no amount of suffix stripping turns "went" into
// "go". Only the ones that actually turn up in speech are here.
const IRREGULAR = new Map(Object.entries({
  am: "be", is: "be", are: "be", was: "be", were: "be", been: "be", being: "be",
  has: "have", had: "have", having: "have",
  does: "do", did: "do", done: "do", doing: "do",
  went: "go", gone: "go", goes: "go",
  said: "say", says: "say", made: "make", took: "take", taken: "take",
  came: "come", saw: "see", seen: "see", knew: "know", known: "know",
  got: "get", gotten: "get", gave: "give", given: "give", found: "find",
  thought: "think", told: "tell", became: "become", left: "leave",
  felt: "feel", brought: "bring", began: "begin", begun: "begin",
  kept: "keep", held: "hold", wrote: "write", written: "write",
  stood: "stand", heard: "hear", let: "let", meant: "mean", met: "meet",
  ran: "run", paid: "pay", sat: "sit", spoke: "speak", spoken: "speak",
  lay: "lie", led: "lead", grew: "grow", grown: "grow", lost: "lose",
  fell: "fall", fallen: "fall", sent: "send", built: "build", understood: "understand",
  drew: "draw", drawn: "draw", broke: "break", broken: "break",
  spent: "spend", cut: "cut", rose: "rise", risen: "rise", driven: "drive", drove: "drive",
  bought: "buy", wore: "wear", worn: "wear", chose: "choose", chosen: "choose",
  ate: "eat", eaten: "eat", threw: "throw", thrown: "throw", caught: "catch",
  dealt: "deal", won: "win", forgot: "forget", forgotten: "forget",
  laid: "lay", slept: "sleep", flew: "fly", flown: "fly",
  children: "child", men: "man", women: "woman", people: "person", feet: "foot",
  teeth: "tooth", mice: "mouse", geese: "goose", lives: "life", knives: "knife",
  wives: "wife", leaves: "leaf", halves: "half", selves: "self",
  better: "good", best: "good", worse: "bad", worst: "bad",
}));

// Words so common that marking them unknown is noise, not information. A
// learner at 1,000 cards is not going to mine "the".
const FUNCTION_WORDS = new Set(("a an the and or but so if then than that this these those " +
  "i you he she it we they me him her us them my your his its our their mine yours " +
  "is am are was were be been being have has had do does did will would shall should " +
  "can could may might must of in on at to for with by from up down out off over under " +
  "not no yes as too very just only also there here what when where who whom which how why " +
  "all any both each few more most other some such own same s t re ve ll d m " +
  "until while because after before since though although unless into onto upon " +
  "again still ever never always often sometimes now already yet even much many one two " +
  "about against between during through above below around near across " +
  "let us get go got going come came make made take took know knew think thought " +
  "want wanted need needed like liked look looked see saw say said tell told " +
  "thing things time times way ways day days year years people man woman " +
  "good bad big small new old right wrong sure okay ok well really quite rather " +
  "something anything nothing everything someone anyone everyone nobody " +
  "hi hey oh ah uh um yeah yep nope please thanks sorry").split(" "));

function stripSuffix(word) {
  const w = word;
  if (w.length <= 3) return [];
  const out = [];
  const add = (s) => { if (s && s.length > 2) out.push(s); };

  if (w.endsWith("ies")) add(w.slice(0, -3) + "y");
  if (w.endsWith("es")) { add(w.slice(0, -2)); add(w.slice(0, -1)); }
  if (w.endsWith("s") && !w.endsWith("ss")) add(w.slice(0, -1));
  if (w.endsWith("ied")) add(w.slice(0, -3) + "y");
  if (w.endsWith("ed")) {
    add(w.slice(0, -2));                       // walked -> walk
    add(w.slice(0, -1));                       // hoped  -> hope
    if (/([bdfglmnprt])\1ed$/.test(w)) add(w.slice(0, -3));   // stopped -> stop
  }
  if (w.endsWith("ing")) {
    add(w.slice(0, -3));                       // walking -> walk
    add(w.slice(0, -3) + "e");                 // hoping  -> hope
    if (/([bdfglmnprt])\1ing$/.test(w)) add(w.slice(0, -4)); // running -> run
  }
  if (w.endsWith("ly")) add(w.slice(0, -2));
  if (w.endsWith("er")) { add(w.slice(0, -2)); add(w.slice(0, -1)); }
  if (w.endsWith("est")) { add(w.slice(0, -3)); add(w.slice(0, -2)); }
  return out;
}

export function normalise(raw) {
  return String(raw || "").toLowerCase()
    .replace(/[’]/g, "'")
    .replace(/^['-]+|['-]+$/g, "");
}

// Every form of a word worth trying against the deck, best guess first.
export function forms(raw) {
  const word = normalise(raw);
  if (!word) return [];
  const out = [word];
  // "don't" is two words pretending to be one; the half before the apostrophe
  // is the half that carries the meaning.
  if (word.includes("'")) out.push(word.split("'")[0]);
  const irregular = IRREGULAR.get(word);
  if (irregular) out.push(irregular);
  out.push(...stripSuffix(word));
  return [...new Set(out.filter(Boolean))];
}

// ---- the deck, as an index -----------------------------------------------
//
// Built once per deck change: single words in one map, phrases in another
// keyed by their first word so a line only has to test the phrases that could
// possibly start where it is standing.

const MATURE_DAYS = 21;

function standing(card) {
  const state = card.fsrs?.state ?? 0;
  if (state === 0) return "fresh";                                   // in the deck, not yet met
  if (state === 1 || state === 3) return "learning";
  return (card.fsrs?.stability || 0) >= MATURE_DAYS ? "known" : "young";
}

// A word is worth showing as new only if it is new to this person, and the
// deck knows more about that than any frequency list does. Every card carries
// an example sentence, and a word this person has read twice across their own
// sentences is not the word they should be mining tonight.
//
// A general frequency list would be the obvious alternative and was the first
// thing tried. The good ones are all derived from the Google Trillion Word
// Corpus, whose licence says in as many words not to use it commercially
// without the LDC's permission — so this signal is personal, free, already in
// the deck, and gets better every time a card is added.
const FAMILIAR_AT = 2;

function familiarFrom(cards) {
  const seen = new Map();
  for (const card of cards) {
    for (const m of String(card.example || "").matchAll(WORD)) {
      const w = normalise(m[0]);
      if (w.length < 2) continue;
      seen.set(w, (seen.get(w) || 0) + 1);
    }
  }
  const out = new Set();
  for (const [word, n] of seen) if (n >= FAMILIAR_AT) out.add(word);
  return out;
}

export function index(cards, known = []) {
  const words = new Map();     // form -> card
  const phrases = new Map();   // first word -> [{ parts, card }]
  const rank = { known: 4, young: 3, learning: 2, fresh: 1 };

  for (const card of cards) {
    // Letters, not ASCII. The tokeniser above reads a line with \p{L}, so a
    // transcript hands back "séance" whole - while this threw the é away and
    // indexed the card as the two-part phrase "s" + "ance", which nothing can
    // ever match. Any word with an accent in it was invisible to the deck.
    const text = normalise(card.word).replace(/[^\p{L}\p{M}' -]/gu, " ").trim();
    if (!text) continue;
    const parts = text.split(/[\s-]+/).filter(Boolean);
    if (parts.length > 1) {
      const head = parts[0];
      if (!phrases.has(head)) phrases.set(head, []);
      phrases.get(head).push({ parts, card, state: standing(card) });
      continue;
    }
    for (const form of forms(parts[0])) {
      const already = words.get(form);
      // Two cards can share a form. Keep the one you know best, so a word is
      // not marked unknown because some other card mentioning it is new.
      if (!already || rank[standing(card)] > rank[already.state]) {
        words.set(form, { card, state: standing(card) });
      }
    }
  }
  return {
    words, phrases,
    familiar: familiarFrom(cards),
    // Words the person has said they already know. One tap while watching,
    // and that word stops shouting at them for good.
    known: new Set(known.map(normalise).filter(Boolean)),
  };
}

// ---- reading a line ------------------------------------------------------

// Where a phrase's words actually land. Adjacent is the normal case; a two-part
// phrasal verb is allowed to be split, because "put it off" and "put off" are
// the same verb and English speakers do not think of them as different. The gap
// is capped at two words — past that it is usually a coincidence, not the verb.
const PARTICLES = new Set(("off up out down in on over under away back through along " +
  "around about apart aside across by forward together").split(" "));
const MAX_GAP = 2;

function placeAt(plain, start, parts) {
  const matches = (at, part) => at < plain.length && (plain[at] === part || forms(plain[at]).includes(part));
  if (!matches(start, parts[0])) return null;
  const spots = [start];
  let at = start;
  for (let k = 1; k < parts.length; k += 1) {
    if (matches(at + 1, parts[k])) { at += 1; spots.push(at); continue; }
    // Only the last part of a two-part verb may drift, and only if it is a
    // particle: "put it off", never "beat quietly around the bush".
    if (parts.length === 2 && k === 1 && PARTICLES.has(parts[1])) {
      let found = -1;
      for (let gap = 1; gap <= MAX_GAP && at + 1 + gap < plain.length; gap += 1) {
        if (matches(at + 1 + gap, parts[1])) { found = at + 1 + gap; break; }
      }
      if (found > 0) { spots.push(found); at = found; continue; }
    }
    return null;
  }
  return spots;
}

export function tokenise(line) {
  const tokens = [];
  let last = 0;
  for (const m of String(line || "").matchAll(WORD)) {
    if (m.index > last) tokens.push({ text: line.slice(last, m.index), word: false });
    tokens.push({ text: m[0], word: true, at: m.index });
    last = m.index + m[0].length;
  }
  if (last < line.length) tokens.push({ text: line.slice(last), word: false });
  return tokens;
}

// A line, with every word placed: which card it belongs to, how well it is
// known, and which words are phrases that should be mined whole.
export function read(line, idx) {
  const tokens = tokenise(line);
  const wordAt = tokens.map((t, i) => (t.word ? i : -1)).filter((i) => i >= 0);
  const plain = wordAt.map((i) => normalise(tokens[i].text));

  // Phrases first and longest first, so "beat around the bush" wins over
  // "beat" and the four words are marked as one thing.
  const claimed = new Set();
  for (let i = 0; i < plain.length; i += 1) {
    const candidates = (idx.phrases.get(plain[i]) || [])
      .concat(forms(plain[i]).flatMap((f) => (f === plain[i] ? [] : idx.phrases.get(f) || [])))
      .sort((a, b) => b.parts.length - a.parts.length);
    for (const candidate of candidates) {
      const n = candidate.parts.length;
      if (i + n > plain.length) continue;
      const spots = placeAt(plain, i, candidate.parts);
      if (!spots) continue;
      if (spots.some((at) => claimed.has(at))) continue;
      spots.forEach((at, k) => {
        claimed.add(at);
        const t = tokens[wordAt[at]];
        t.state = candidate.state;
        t.card = candidate.card;
        t.phrase = candidate.card.word;
        t.head = k === 0;
      });
      i = spots[spots.length - 1];
      break;
    }
  }

  let unknown = 0;
  plain.forEach((word, i) => {
    const token = tokens[wordAt[i]];
    if (claimed.has(i)) return;
    const shapes = forms(word);
    // The deck first, because a card is this person saying "I am learning
    // this" and no frequency list outranks that. It used to be the other way
    // round, and a stem that happens to collide with an everyday word took
    // the card with it: `shed` strips to `she`, `wither` to `with`, and both
    // were greyed out as words nobody needs to mine while sitting in the deck
    // being learned.
    for (const form of shapes) {
      const hit = idx.words.get(form);
      if (hit) { token.state = hit.state; token.card = hit.card; return; }
    }
    // "I'll" is a function word wearing a contraction.
    if (word.length < 2 || shapes.some((f) => FUNCTION_WORDS.has(f))) { token.state = "common"; return; }
    if (shapes.some((f) => idx.known?.has(f))) { token.state = "common"; return; }
    if (shapes.some((f) => idx.familiar?.has(f))) { token.state = "familiar"; return; }
    token.state = "new";
    unknown += 1;
  });

  return { tokens, unknown };
}

// The sentences worth stopping on: one unknown word, nothing else in the way.
// Krashen called it i+1; every app in this space sells it, and it is a filter
// over data this app already holds.
export function mineable(cues, idx, { max = 1 } = {}) {
  const out = [];
  cues.forEach((cue, i) => {
    const { unknown } = read(cue.text, idx);
    if (unknown >= 1 && unknown <= max) out.push({ i, cue, unknown });
  });
  return out;
}
