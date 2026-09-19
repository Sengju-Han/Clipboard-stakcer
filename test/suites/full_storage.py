"""A phone with no room left, answering a card.

Answering writes the log row and then the card. Both can fail — a browser whose
storage is full refuses with QuotaExceededError — and the grade buttons call
grade() without awaiting it, so the rejection went nowhere. No message, no
console line anybody would read, and a Good that does not advance. Then another
that does not either. The card is still there, the count has not moved, and
nothing on screen admits why.

That is the worst version of today's recurring mistake: not a wrong number in a
report, but the app quietly refusing to record an evening's reviews.
"""


def _fail_writes_to(t, store_name):
    """Make every put() to one object store throw the way a full disk does."""
    t.page.evaluate("""(name) => {
      window.__realPut = window.__realPut || IDBObjectStore.prototype.put;
      IDBObjectStore.prototype.put = function (...args) {
        if (this.name === name) {
          const err = new Error("The quota has been exceeded.");
          err.name = "QuotaExceededError";
          throw err;
        }
        return window.__realPut.apply(this, args);
      };
    }""", store_name)


def _stop_failing(t):
    t.page.evaluate("""() => {
      if (window.__realPut) IDBObjectStore.prototype.put = window.__realPut;
    }""")


def run(t):
    t.open_app()
    t.page.locator("#start-btn").click()
    t.page.wait_for_selector("#screen-review:not([hidden])", timeout=30000)
    t.page.wait_for_timeout(500)

    left = t.page.locator("#left-count").inner_text()
    word_before = t.page.locator("#card-clue").inner_text()
    t.page.locator("#reveal-btn").click()
    t.page.wait_for_timeout(300)

    # The disk fills between showing the word and tapping a grade.
    _fail_writes_to(t, "log")
    t.page.locator(".grade.good").click()
    t.page.wait_for_timeout(1200)

    said = t.page.locator("#write-note")
    t.check("a failed write says so", said.is_visible(), True)
    t.note("it said", said.inner_text()[:110])
    t.truthy("and names what fills the storage", "clips" in said.inner_text())
    t.check("the card is still on screen", t.page.locator("#card-clue").inner_text(), word_before)
    t.check("and still owed", t.page.locator("#left-count").inner_text(), left)

    # Nothing was written, so nothing is half-done: the log has no row for it.
    logged = t.page.evaluate("""async () => {
      const db = await new Promise(r => { const q = indexedDB.open('lexis'); q.onsuccess = () => r(q.result); });
      return await new Promise(r => {
        const g = db.transaction('log').objectStore('log').getAll(); g.onsuccess = () => r(g.result.length); });
    }""")
    t.check("and no answer was recorded", logged, 0)

    # Room again. The same tap now works, and the warning goes.
    _stop_failing(t)
    t.page.locator(".grade.good").click()
    t.page.wait_for_timeout(1200)

    t.check("answering works once there is room", said.is_visible(), False)
    t.check("the card moved on",
            t.page.locator("#card-clue").inner_text() != word_before, True)
    after = t.page.evaluate("""async () => {
      const db = await new Promise(r => { const q = indexedDB.open('lexis'); q.onsuccess = () => r(q.result); });
      return await new Promise(r => {
        const g = db.transaction('log').objectStore('log').getAll(); g.onsuccess = () => r(g.result.length); });
    }""")
    t.check("and the answer was written down this time", after, 1)
