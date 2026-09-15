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
