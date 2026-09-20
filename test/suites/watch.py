"""Subtitles, the words in them you do not have, and taking one out."""


def run(t):
    t.open_app()
    t.page.locator("#watch-btn").click()
    t.page.wait_for_timeout(700)
    t.check("the watch screen opens", t.screen(), "screen-watch")
    t.truthy("and says what it takes",
             ".srt" in t.page.locator("#w-note").inner_text())

    t.page.locator("#w-subs-file").set_input_files(str(t.fixtures["episode"]))
    t.page.wait_for_selector("#w-lines .line", timeout=60000)
    t.page.wait_for_timeout(800)

    t.check("600 lines are read", "600 lines" in t.page.locator("#w-note").inner_text(), True)
    drawn = t.page.locator("#w-lines .line").count()
    t.truthy(f"they are drawn a page at a time, not all at once ({drawn})", 0 < drawn < 600)

    marks = t.page.evaluate("""() => {
      const out = {};
      for (const el of document.querySelectorAll('#w-lines .w')) {
        const s = [...el.classList].find(c => c.startsWith('s-')).slice(2);
        out[s] = (out[s] || 0) + 1;
      }
      return out;
    }""")
    t.note("how the words on screen are marked", marks)
    t.truthy("some words are marked as not in the deck", marks.get("new", 0) > 0)
    t.truthy("and some as everyday words", marks.get("common", 0) > 0)
    t.truthy("a phrase is marked as one thing, not two",
             t.page.locator("#w-lines .w.in-phrase").count() > 0)

    t.page.locator("#w-filter").click()
    t.page.wait_for_timeout(1200)
    shown = t.page.locator("#w-lines .line").count()
    wrong = t.page.evaluate(
        "() => [...document.querySelectorAll('#w-lines .line')].filter(l => l.dataset.new !== '1').length")
    t.truthy(f"the one-new-word filter shows fewer lines ({shown})", 0 < shown < 600)
    t.check("and every one of them has exactly one", wrong, 0)
    t.page.locator("#w-filter").click()
    t.page.wait_for_timeout(1200)

    t.page.locator("#w-lines .w.s-new").first.click()
    t.page.wait_for_timeout(600)
    word = t.page.locator("#w-sheet-word").inner_text()
    t.check("tapping a word opens it", t.page.locator("#w-sheet").is_hidden(), False)
    t.check("and says where it stands", t.page.locator("#w-sheet-state").inner_text(),
            "not in your deck")
    t.truthy("with the sentence it was said in",
             len(t.page.locator("#w-sheet-line").inner_text()) > 10)
    t.check("capture is not offered without a soundtrack",
            t.page.locator("#w-sheet-clip").is_visible(), False)

    sentence = t.page.locator("#w-sheet-line").inner_text()
    t.page.locator("#w-sheet-add").click()
    t.page.wait_for_timeout(900)
    t.check("making a card goes to the add screen", t.screen(), "screen-add")
    t.check("with the word in it", t.page.locator("#a-word").input_value(), word)
    t.check("and the sentence as its example", t.page.locator("#a-example").input_value(), sentence)

    t.page.locator("#a-clue").fill("a clue")
    t.page.locator("#add-form button[type=submit]").click()
    t.page.wait_for_timeout(1500)
    t.check("saving goes back to the transcript", t.screen(), "screen-watch")
    still = t.page.evaluate(
        """(w) => [...document.querySelectorAll('#w-lines .w.s-new')]
             .filter(e => e.dataset.word.toLowerCase() === w.toLowerCase()).length""", word)
    t.check("and the word is not marked as missing any more", still, 0)

    t.page.locator("#w-lines .w.s-new").first.click()
    t.page.wait_for_timeout(500)
    known = t.page.locator("#w-sheet-word").inner_text()
    t.page.locator("#w-sheet-known").click()
    t.page.wait_for_timeout(1200)
    left = t.page.evaluate(
        """(w) => [...document.querySelectorAll('#w-lines .w.s-new')]
             .filter(e => e.dataset.word.toLowerCase() === w.toLowerCase()).length""", known)
    t.check("saying you know a word silences it", left, 0)
    t.truthy("and that is remembered",
             known.lower() in t.page.evaluate(
                 "() => JSON.parse(localStorage.getItem('lexis:known') || '[]')"))

    # Captions cannot be fetched from a browser and Android runs no extensions,
    # so pasting YouTube's own transcript panel is the way in for a phone.
    t.page.locator("#w-paste-btn").click()
    t.page.wait_for_timeout(400)
    t.page.locator("#w-paste").fill(
        "0:03\nso here we are in front of the elephants\n\n"
        "0:09\nhe chimed in with an opinion nobody asked for\n")
    t.page.locator("#w-paste-use").click()
    t.page.wait_for_timeout(1200)
    t.check("a pasted transcript is read", t.page.locator("#w-lines .line").count(), 2)
    t.check("with its timestamps", t.page.locator("#w-lines .at").first.inner_text(), "0:03")

    t.page.locator("#w-paste-btn").click()
    t.page.wait_for_timeout(400)
    t.page.locator("#w-paste").fill("He avowed his commitment. She was bewildered! Was that it?")
    t.page.locator("#w-paste-use").click()
    t.page.wait_for_timeout(1200)
    t.check("so is plain prose, split into sentences",
            t.page.locator("#w-lines .line").count(), 3)
    t.check("with no timestamps, because they would be made up",
            t.page.locator("#w-lines .at").count(), 0)

    t.page.locator("#w-paste-btn").click()
    t.page.wait_for_timeout(400)
    t.page.locator("#w-paste").fill("   ")
    t.page.locator("#w-paste-use").click()
    t.page.wait_for_timeout(800)
    t.truthy("and nothing readable says so rather than throwing",
             "no words" in t.page.locator("#w-note").inner_text().lower()
             or "nothing readable" in t.page.locator("#w-note").inner_text().lower())

    _every_word_in_the_deck_is_seen(t)
    _encodings(t)
    _unplayable(t)


def _encodings(t):
    """A subtitle file is as likely to be Windows-1252 as UTF-8.

    File.text() decodes as UTF-8 whatever it is given, and hands back a page of
    replacement characters — which parses into cues full of nothing and marks
    every word as one you do not have.

    The obvious fix, trying each encoding strictly and taking the first that
    does not throw, was written and then measured and is wrong: given the bytes
    of "Ärger" a strict EUC-KR decoder does not complain, it returns "훣ger".
    So the choice is made on the byte pattern, and this is what holds it.
    """
    for name, case in sorted(t.fixtures["encoded"].items()):
        t.page.locator("#w-subs-file").set_input_files(case["path"])
        t.page.wait_for_timeout(1500)
        first = t.page.locator("#w-lines .said").first.inner_text()
        t.check(f"{name} ({case['encoding']}) reads as text and not as damage",
                case["expect"] in first, True)


def _unplayable(t):
    """A browser plays what it can decode and says nothing about the rest."""
    t.page.locator("#w-subs-file").set_input_files(str(t.fixtures["episode"]))
    t.page.wait_for_selector("#w-lines .line", timeout=60000)
    lines = t.page.locator("#w-lines .line").count()

    t.page.locator("#w-media-file").set_input_files(str(t.fixtures["unplayable"]))
    t.page.wait_for_timeout(3000)
    said = t.page.locator("#w-note").inner_text()
    t.truthy("a file the browser cannot decode says so", "cannot play" in said)
    t.truthy("and names the likely reason", "container" in said)
    t.truthy("and says what still works", "transcript is still here" in said)
    t.check("the dead player is taken off the screen",
            t.page.locator("#w-player").is_hidden(), True)
    t.check("and the transcript is untouched",
            t.page.locator("#w-lines .line").count(), lines)


def _every_word_in_the_deck_is_seen(t):
    """A word you already have must not read as one you do not.

    Fifteen of this deck's 1,240 words were invisible to the marker, each for
    its own reason and all with the same effect: watching a show, the word is
    greyed out or flagged new, and the obvious thing to do is mine a second
    card for a word already in the deck.

      tie-dye, jam-packed, hangers-on and ten more — the tokeniser kept the
      hyphen inside the word while the deck index split on it, so one side
      looked for "tie-dye" and the other held "tie" + "dye"

      shed, wither — the frequency list was consulted before the deck, and a
      speculative stem collided with an everyday word: shed strips to she,
      wither to with

      séance — the index filtered the card's own word down to [a-z], so the
      accent became a space and the card was stored as "s" + "ance"

    Measured against the real deck rather than a fixture, because the fixture
    would have had to be built out of the very words nobody knew to look for.
    """
    missed = t.page.evaluate("""async () => {
      const { index, read } = await import("./lex.js");
      const db = await new Promise(r => { const q = indexedDB.open('lexis'); q.onsuccess = () => r(q.result); });
      const cards = (await new Promise(r => {
        const g = db.transaction('cards').objectStore('cards').getAll(); g.onsuccess = () => r(g.result);
      })).filter(c => !c.deleted);
      const idx = index(cards, []);
      const out = [];
      for (const card of cards) {
        if (!card.word) continue;
        const { tokens } = read("We said " + card.word + " aloud.", idx);
        if (!tokens.some(tok => tok.word && tok.card)) out.push(card.word);
      }
      return { missed: out, total: cards.length };
    }""")
    t.note("checked", f"{missed['total']} cards")
    t.check("every word in the deck is recognised when it is said",
            missed["missed"][:10], [])

    # And however the accent was written. A subtitle file spells it "séance";
    # plenty spell it "seance". One card, either spelling.
    t.check("an accent is a spelling, not a different word",
            t.page.evaluate("""async () => {
              const { index, read } = await import("./lex.js");
              const idx = index([{ word: "s\u00e9ance", fsrs: { state: 2, stability: 30 } }], []);
              return ["s\u00e9ance", "seance"].map((w) =>
                read("We went to a " + w + " last night.", idx)
                  .tokens.some((tok) => tok.word && tok.card));
            }"""), [True, True])
