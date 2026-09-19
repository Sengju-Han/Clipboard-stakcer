"""A deck that arrived without its review history has to say so.

This used to open the published deck and assert the notice appeared, because
the published deck really had been built from a copy with no scheduling in it:
every reviewed card carried the same made-up due date. That was a fact about
the data on one particular day, not about the app, and when the deck was
rebuilt from a real collection the suite went red while nothing was wrong.

So it makes its own broken deck now, and checks the published one is *not*
warned about — which is the more useful of the two, because a false warning
about a real schedule is the failure that would actually cost somebody
something.
"""

SAME_DATE = "2026-01-15T12:00:00.000Z"


def _reload(t):
    t.page.reload()
    t.page.wait_for_selector("#screen-home:not([hidden])", timeout=90000)
    t.page.wait_for_timeout(1200)


def _set_due(t, script):
    """Rewrite every reviewed card's due date, and un-dismiss the notice."""
    t.page.evaluate("""async (body) => {
      localStorage.removeItem('lexis:schedule-warned');
      const db = await new Promise(r => { const q = indexedDB.open('lexis'); q.onsuccess = () => r(q.result); });
      const all = await new Promise(r => {
        const g = db.transaction('cards').objectStore('cards').getAll(); g.onsuccess = () => r(g.result); });
      const store = db.transaction('cards', 'readwrite').objectStore('cards');
      const due = new Function('card', 'index', body);
      all.forEach((c, i) => {
        if (c.deleted || c.fsrs.state === 0) return;
        c.fsrs.due = due(c, i);
        store.put(c);
      });
      await new Promise(r => { store.transaction.oncomplete = () => r(); });
    }""", script)
    _reload(t)


def _reviewed(t):
    """How many cards carry a review history, which is what the notice is about."""
    return t.page.evaluate("""async () => {
      const db = await new Promise(r => { const q = indexedDB.open('lexis'); q.onsuccess = () => r(q.result); });
      const all = await new Promise(r => {
        const g = db.transaction('cards').objectStore('cards').getAll(); g.onsuccess = () => r(g.result); });
      return all.filter((c) => !c.deleted && c.fsrs.state !== 0).length;
    }""")


def run(t):
    t.open_app()

    # The deck as published. A real export has real dates in it, and warning
    # about those would be worse than the thing the warning is for.
    t.check("a real schedule is not warned about",
            t.page.locator("#no-schedule").is_hidden(), True)
    real_due = int(t.page.locator("#n-due").inner_text())
    reviewed = _reviewed(t)
    # Not a round number picked out of the air: what a lost schedule looks like
    # is every reviewed card owed at once, so the check is that this deck is
    # nothing like that. A backlog is allowed; a backlog of everything is not.
    t.truthy(f"and it is not owing everything at once ({real_due} of {reviewed})",
             real_due < reviewed * 0.75)

    # Now the thing the notice exists for: every reviewed card on one date,
    # which is what an export that lost its scheduling looks like.
    _set_due(t, f"return '{SAME_DATE}';")
    shown = not t.page.locator("#no-schedule").is_hidden()
    t.check("a deck where every card shares one due date is caught", shown, True)
    broken_due = int(t.page.locator("#n-due").inner_text())
    t.truthy(f"and that deck does owe nearly everything ({broken_due} of {reviewed})",
             broken_due > real_due)

    said = t.page.locator("#no-schedule-said").inner_text()
    t.truthy("and the notice names the file that would fix it", ".apkg" in said)
    t.truthy("and the workflow that would too", "Build the review deck" in said)

    t.page.locator("#no-schedule-ok").click()
    t.page.wait_for_timeout(500)
    t.check("it can be dismissed", t.page.locator("#no-schedule").is_hidden(), True)
    _reload(t)
    t.check("and stays dismissed", t.page.locator("#no-schedule").is_hidden(), True)

    # Spread them the way a real schedule is spread, and it goes quiet again
    # even though the notice was never dismissed for this version of the deck.
    _set_due(t, "return new Date(Date.now() + (index % 40) * 86400000).toISOString();")
    t.check("a spread of dates is not warned about",
            t.page.locator("#no-schedule").is_hidden(), True)
    spread = int(t.page.locator("#n-due").inner_text())
    t.truthy(f"and far less is owed than when they shared a date ({spread} against {broken_due})",
             spread < broken_due)
