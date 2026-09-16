"""An evening with nothing due should not be an app that will not open."""


def run(t):
    t.open_app()

    # Push everything into next week, and take the new cards off the table too,
    # so that nothing at all is owed.
    t.page.evaluate("""async () => {
      const db = await new Promise(r => { const q = indexedDB.open('lexis'); q.onsuccess = () => r(q.result); });
      const all = await new Promise(r => {
        const g = db.transaction('cards').objectStore('cards').getAll(); g.onsuccess = () => r(g.result); });
      const store = db.transaction('cards', 'readwrite').objectStore('cards');
      all.forEach((c, i) => {
        if (c.deleted) return;
        c.fsrs.state = 2;
        c.fsrs.stability = 20;
        c.fsrs.due = new Date(Date.now() + (3 + (i % 7)) * 86400000).toISOString();
        store.put(c);
      });
      await new Promise(r => { store.transaction.oncomplete = () => r(); });
    }""")
    t.page.reload()
    t.page.wait_for_selector("#screen-home:not([hidden])", timeout=90000)
    t.page.wait_for_timeout(1000)

    t.check("nothing is owed", t.page.locator("#n-due").inner_text(), "0")
    t.check("so the usual button says so",
            t.page.locator("#start-btn").inner_text(), "Nothing due right now")
    t.check("and is not a button", t.page.locator("#start-btn").is_disabled(), True)

    t.check("studying ahead is offered instead", t.page.locator("#ahead-btn").is_hidden(), False)
    offered = t.page.locator("#ahead-btn").inner_text()
    t.truthy(f"with how many ({offered})", "Study ahead" in offered)

    # It is not free, and the screen has to say so rather than let somebody
    # find out from their intervals a fortnight later.
    said = t.page.locator("#ahead-note").inner_text()
    t.truthy("and with what it costs", "shorter interval" in said)
    t.truthy("and when the deck would come back on its own", "Nothing is owed until" in said)

    t.page.locator("#ahead-btn").click()
    t.page.wait_for_timeout(900)
    t.check("it opens a session", t.screen(), "screen-review")

    first = t.page.locator("#card-clue").inner_text()
    t.page.locator("#reveal-btn").click()
    t.page.wait_for_timeout(300)
    word = t.page.locator("#card-word").inner_text()
    t.page.locator(".grade.good").click()
    t.page.wait_for_timeout(800)

    card = next(c for c in t.cards() if c["word"] == word)
    t.truthy("answering one schedules it", card["fsrs"]["reps"] >= 1)
    t.check("and it is written down", len(t.log()), 1)

    t.page.locator("#quit-btn").click()
    t.page.wait_for_timeout(900)
    t.page.locator("#ahead-btn").click()
    t.page.wait_for_timeout(900)
    t.page.locator("#quit-btn").click()
    t.page.wait_for_timeout(700)

    # Finishing an early session says what it did, because the intervals will
    # look mean otherwise and nothing else would explain why.
    t.page.locator("#ahead-btn").click()
    t.page.wait_for_timeout(900)
    for _ in range(2):
        if t.page.locator("#reveal-btn").is_visible():
            t.answer("good")
    t.note("started ahead on", first)
    t.truthy("a session started ahead is still a session", t.screen() == "screen-review")
