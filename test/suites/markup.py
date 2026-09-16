"""A word containing a < has to render as a word containing a <.

Most of what this app holds is somebody's own collection, which is not a reason
to skip escaping it: a word mined out of a subtitle file came from a download,
a deck can be named anything, and an error message can carry text a server
wrote. Three places built markup out of that text without escaping it.
"""

NASTY = '<img src=x onerror="window.__ranAnyway = 1">bush'
DECK = 'duo<img src=y onerror="window.__deckRan = 1">'


def run(t):
    t.open_app()
    t.page.evaluate("""async ({ word, deck }) => {
      const db = await new Promise(r => { const q = indexedDB.open('lexis'); q.onsuccess = () => r(q.result); });
      const all = await new Promise(r => {
        const g = db.transaction('cards').objectStore('cards').getAll(); g.onsuccess = () => r(g.result); });
      const c = all.find(x => !x.deleted);
      c.word = word; c.deck = deck; c.mod = Date.now();
      c.fsrs.due = new Date(Date.now() - 86400000).toISOString();
      await new Promise(r => {
        const p = db.transaction('cards', 'readwrite').objectStore('cards').put(c); p.onsuccess = () => r(); });
    }""", {"word": NASTY, "deck": DECK})
    t.page.reload()
    t.page.wait_for_selector("#screen-home:not([hidden])", timeout=90000)
    t.page.wait_for_timeout(900)

    t.page.locator("#browse-btn").click()
    t.page.wait_for_timeout(600)
    t.page.locator("#find").fill("bush")
    t.page.wait_for_timeout(800)

    t.check("the browser finds it", t.page.locator(".hit").count() >= 1, True)
    t.check("and shows the word as written, tags and all",
            t.page.locator(".hit .w").first.inner_text(), NASTY)
    t.check("the deck name too",
            DECK in t.page.locator(".hit .m").first.inner_text(), True)
    t.check("and the deck name did not run either",
            t.page.evaluate("() => window.__deckRan || 0"), 0)
    t.check("nothing in it was treated as markup",
            t.page.evaluate("() => document.querySelectorAll('#results img').length"), 0)
    t.check("and nothing in it ran",
            t.page.evaluate("() => window.__ranAnyway || 0"), 0)

    t.page.locator("#browse-close").click()
    t.page.wait_for_timeout(600)
    t.page.locator("#start-btn").click()
    t.page.wait_for_timeout(800)
    t.page.locator("#reveal-btn").click()
    t.page.wait_for_timeout(400)
    # The panel puts the word on screen before it goes looking, and that line
    # is built as markup. Read it before anything has had time to replace it.
    t.page.locator("#explain-btn").click()
    looking = t.page.evaluate("() => document.getElementById('brain').innerHTML")
    if "Looking up" in looking:
        t.check("the word is escaped on its way into the panel",
                "&lt;img" in looking and "<img" not in looking, True)
    else:
        t.note("the looking-up line had already been replaced", "not checked here")

    t.page.wait_for_timeout(2500)
    t.check("nothing was built out of it",
            t.page.evaluate("() => document.querySelectorAll('#brain img').length"), 0)
    t.check("and still nothing ran", t.page.evaluate("() => window.__ranAnyway || 0"), 0)
    t.truthy("the panel says something a person can read",
             len(t.page.locator("#brain").inner_text().strip()) > 10)
