"""Finding a card, fixing it, and deleting it without it coming back."""


def run(t):
    t.open_app()
    total = len(t.cards())

    t.page.locator("#browse-btn").click()
    t.page.wait_for_timeout(700)
    t.check("browse opens", t.screen(), "screen-browse")
    t.truthy("and shows something", t.page.locator(".hit").count() > 0)

    first = t.page.locator(".hit .w").first.inner_text()
    t.page.locator("#find").fill(first)
    t.page.wait_for_timeout(700)
    t.check("searching a word finds it", t.page.locator(".hit .w").first.inner_text(), first)

    card = next(c for c in t.cards() if c["word"] == first)
    if card.get("clue"):
        t.page.locator("#find").fill(card["clue"][:6])
        t.page.wait_for_timeout(700)
        t.truthy("searching its clue finds it too — Korean included",
                 any(h == first for h in t.page.locator(".hit .w").all_inner_texts()))

    t.page.locator("#find").fill(first)
    t.page.wait_for_timeout(700)
    t.page.locator(".hit").first.click()
    t.page.wait_for_timeout(600)
    t.check("tapping one opens it", t.screen(), "screen-edit")
    t.check("with its word in the form", t.page.locator("#e-word").input_value(), first)

    before = card["fsrs"]
    t.page.locator("#e-word").fill(first + " (fixed)")
    t.page.locator("#e-hook").fill("a hook that was not there")
    t.page.locator("#edit-form button[type=submit]").click()
    t.page.wait_for_timeout(800)
    t.check("saving says so", t.page.locator("#edit-note").inner_text(), "Saved.")

    after = next(c for c in t.cards() if c["word"] == first + " (fixed)")
    t.check("the wording changed", after["hook"], "a hook that was not there")
    t.check("and the scheduling did not", after["fsrs"], before)

    t.page.on("dialog", lambda d: d.accept())
    t.page.locator("#edit-delete").click()
    t.page.wait_for_timeout(1200)
    t.check("deleting goes back to browse", t.screen(), "screen-browse")
    t.check("the deck is one smaller", len(t.cards()), total - 1)

    rows = t.rows()
    graves = [r for r in rows if r.get("deleted")]
    t.check("but the row is still there, as a tombstone", len(graves), 1)
    t.note("why", "a row simply removed is a row the other phone hands back on the next sync")
    t.truthy("with the time it happened", graves[0].get("mod", 0) > 0)
