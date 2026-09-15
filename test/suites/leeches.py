"""The cards that keep winning, and the two ways out of one."""

LAPSES = 9


def run(t):
    t.open_app()
    word = t.page.evaluate("""async () => {
      const db = await new Promise(r => { const q = indexedDB.open('lexis'); q.onsuccess = () => r(q.result); });
      const all = await new Promise(r => {
        const g = db.transaction('cards').objectStore('cards').getAll(); g.onsuccess = () => r(g.result); });
      const c = all[0];
      c.fsrs.lapses = 9; c.fsrs.state = 2; c.fsrs.stability = 2;
      c.fsrs.due = new Date(Date.now() - 864000000).toISOString(); c.mod = Date.now();
      await new Promise(r => {
        const p = db.transaction('cards', 'readwrite').objectStore('cards').put(c); p.onsuccess = () => r(); });
      return c.word;
    }""")
    t.page.reload()
    t.page.wait_for_selector("#screen-home:not([hidden])", timeout=90000)
    t.page.wait_for_timeout(900)
    t.note("the card set to nine lapses", word)

    t.page.locator("#stats-btn").click()
    t.page.wait_for_timeout(1800)
    t.check("progress lists it", t.page.locator("#s-leech-box").is_hidden(), False)
    t.check("by name", t.page.locator("#s-leeches .hit .w").first.inner_text(), word)
    t.truthy("with how many times it has gone",
             f"{LAPSES} lapses" in t.page.locator("#s-leeches .hit .m").first.inner_text())

    t.page.locator("#s-leeches .hit").first.click()
    t.page.wait_for_timeout(700)
    t.check("tapping it opens the editor", t.screen(), "screen-edit")
    t.check("on that card", t.page.locator("#e-word").input_value(), word)
    t.page.locator("#edit-close").click()
    t.page.wait_for_timeout(900)
    t.check("and closing goes back to progress", t.screen(), "screen-stats")

    t.page.locator("#stats-close").click()
    t.page.wait_for_timeout(700)
    due_before = int(t.page.locator("#n-due").inner_text())
    t.page.locator("#start-btn").click()
    t.page.wait_for_timeout(800)
    t.page.locator("#reveal-btn").click()
    t.page.wait_for_timeout(300)
    on = t.page.locator("#card-word").inner_text()
    t.page.locator(".grade.again").click()
    t.page.wait_for_timeout(1500)
    t.check("answering it Again says so", t.page.locator("#leech").is_hidden(), False)
    t.truthy("with the count",
             "forgotten this one" in t.page.locator("#leech-said").inner_text())

    t.page.locator("#leech-fix").click()
    t.page.wait_for_timeout(900)
    t.check("fix the card opens the editor mid-session", t.screen(), "screen-edit")
    t.page.locator("#e-clue").fill("a clue that points at the answer")
    t.page.locator("#edit-form button[type=submit]").click()
    t.page.wait_for_timeout(700)
    t.page.locator("#edit-close").click()
    t.page.wait_for_timeout(1000)
    t.check("and closing returns to the session", t.screen(), "screen-review")
    t.check("on the same card", t.page.locator("#card-word").inner_text(), on)
    t.check("with its answer still showing", t.page.locator("#card-word").is_visible(), True)
    t.check("the new clue on it", t.page.locator("#card-clue").inner_text(), "a clue that points at the answer")
    t.check("and the buttons live", t.page.locator(".grade.good").is_visible(), True)

    t.page.locator(".grade.again").click()
    t.page.wait_for_timeout(1200)
    t.page.locator("#leech-rest").click()
    t.page.wait_for_timeout(1500)
    rested = next(c for c in t.cards() if c["word"] == on)
    t.truthy("resting it writes a date to wake it on", bool(rested.get("restUntil")))
    t.truthy("it is not the card on screen any more",
             t.page.locator("#card-word").inner_text() != on or
             t.page.locator("#card-word").is_visible() is False)

    t.page.locator("#quit-btn").click()
    t.page.wait_for_timeout(900)
    due_after = int(t.page.locator("#n-due").inner_text())
    t.truthy(f"a resting card is not counted as due ({due_before} owed, now {due_after})",
             due_after < due_before)

    # And it is not handed out either: the count on the home screen has to
    # agree with the session it gives you.
    t.page.locator("#start-btn").click()
    t.page.wait_for_timeout(900)
    words = []
    for _ in range(3):
        if t.page.locator("#reveal-btn").is_visible():
            words.append(t.answer("good"))
    t.check("and it is not in the next session either", on in words, False)
