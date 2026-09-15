"""A deck that arrived without its review history has to say so."""


def run(t):
    t.open_app()

    # The published deck was built from a copy with no scheduling in it: every
    # reviewed card carries the same made-up due date, which reads as a
    # thousand cards being owed.
    shown = not t.page.locator("#no-schedule").is_hidden()
    t.check("the published deck is caught", shown, True)
    if shown:
        said = t.page.locator("#no-schedule-said").inner_text()
        t.truthy("and the notice names the file that would fix it", ".apkg" in said)
        t.truthy("and the workflow that would too", "Build the review deck" in said)

        t.page.locator("#no-schedule-ok").click()
        t.page.wait_for_timeout(500)
        t.check("it can be dismissed", t.page.locator("#no-schedule").is_hidden(), True)
        t.page.reload()
        t.page.wait_for_selector("#screen-home:not([hidden])", timeout=90000)
        t.page.wait_for_timeout(1200)
        t.check("and stays dismissed", t.page.locator("#no-schedule").is_hidden(), True)

    # Spread the due dates the way a real schedule is spread.
    t.page.evaluate("""async () => {
      localStorage.removeItem('lexis:schedule-warned');
      const db = await new Promise(r => { const q = indexedDB.open('lexis'); q.onsuccess = () => r(q.result); });
      const all = await new Promise(r => {
        const g = db.transaction('cards').objectStore('cards').getAll(); g.onsuccess = () => r(g.result); });
      const store = db.transaction('cards', 'readwrite').objectStore('cards');
      all.forEach((c, i) => {
        if (c.deleted || c.fsrs.state === 0) return;
        c.fsrs.due = new Date(Date.now() + (i % 40) * 86400000).toISOString();
        store.put(c);
      });
      await new Promise(r => { store.transaction.oncomplete = () => r(); });
    }""")
    t.page.reload()
    t.page.wait_for_selector("#screen-home:not([hidden])", timeout=90000)
    t.page.wait_for_timeout(1200)
    t.check("a deck with real dates is not warned about",
            t.page.locator("#no-schedule").is_hidden(), True)
    due = int(t.page.locator("#n-due").inner_text())
    t.truthy(f"and far less is owed today ({due})", due < 200)
