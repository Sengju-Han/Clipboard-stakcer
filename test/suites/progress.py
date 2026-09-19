"""What the charts say, and whether it is true."""


def _five_review_cards(t):
    """Exactly five cards due, all of them already reviewed, nothing else.

    Retention counts answers on review cards and leaves learning ones out, so
    a session that happens to open on a new card answers five and counts four.
    Which card comes first is a fact about the deck, and the deck changes; this
    makes the five the suite is about the only five on offer.
    """
    t.page.evaluate("""async () => {
      const db = await new Promise(r => { const q = indexedDB.open('lexis'); q.onsuccess = () => r(q.result); });
      const all = await new Promise(r => {
        const g = db.transaction('cards').objectStore('cards').getAll(); g.onsuccess = () => r(g.result); });
      const live = all.filter((c) => !c.deleted);
      const store = db.transaction('cards', 'readwrite').objectStore('cards');
      const far = new Date(Date.now() + 400 * 86400000).toISOString();
      live.forEach((c, i) => {
        // Review state with a real stability throughout: a new card is offered
        // however far away its due date is, and FSRS refuses a reviewed card
        // whose stability is under 1.
        c.fsrs.state = 2;
        c.fsrs.stability = 30; c.fsrs.difficulty = 5; c.fsrs.reps = 1;
        c.fsrs.last_review = new Date(Date.now() - 30 * 86400000).toISOString();
        c.fsrs.due = i < 5 ? new Date(Date.now() - 86400000).toISOString() : far;
        c.mod = Date.now();
        store.put(c);
      });
      await new Promise(r => { store.transaction.oncomplete = () => r(); });
    }""")
    t.page.reload()
    t.page.wait_for_selector("#screen-home:not([hidden])", timeout=90000)
    t.page.wait_for_timeout(1000)


def run(t):
    t.open_app()
    _five_review_cards(t)
    t.page.locator("#stats-btn").click()
    t.page.wait_for_timeout(1800)
    t.check("progress opens", t.screen(), "screen-stats")

    t.check("retention says nothing when nothing has been reviewed",
            t.page.locator("#s-retention").inner_text(), "—")
    t.check("the streak starts at zero", t.page.locator("#s-streak").inner_text(), "0")
    t.check("thirty days of forecast are drawn",
            t.page.locator("#s-forecast svg rect.bar").count(), 30)
    t.check("and the deck is broken into four states",
            t.page.locator("#s-maturity tr").count(), 4)

    t.page.locator("#stats-close").click()
    t.page.wait_for_timeout(600)
    t.page.locator("#start-btn").click()
    t.page.wait_for_timeout(700)
    for rating in ["good", "again", "easy", "hard", "good"]:
        t.answer(rating)
    t.page.locator("#quit-btn").click()
    t.page.wait_for_timeout(700)

    t.page.locator("#stats-btn").click()
    t.page.wait_for_timeout(1800)
    t.check("five answers are counted", t.page.locator("#s-done").inner_text(), "5")
    t.check("a day's work is a streak of one", t.page.locator("#s-streak").inner_text(), "1")
    t.check("four of the five held, so retention is 80%",
            t.page.locator("#s-retention").inner_text(), "80%")
    t.truthy("and it says what it left out",
             "learning are left out" in t.page.locator("#s-retention-note").inner_text())
    t.check("the tallest activity bar is labelled",
            t.page.locator("#s-activity svg text.peak").count(), 1)
