"""The loop itself: a clue, an answer, a grade, and does any of it survive."""


def run(t):
    t.open_app()

    t.check("it opens on the home screen", t.screen(), "screen-home")
    total = len(t.cards())
    t.note("cards in the deck", total)
    t.truthy("there is a deck at all", total > 0)
    t.truthy("the start button offers a session",
             t.page.locator("#start-btn").inner_text().startswith("Review"))

    t.page.locator("#start-btn").click()
    t.page.wait_for_timeout(700)
    t.check("a session opens on the review screen", t.screen(), "screen-review")

    t.truthy("the clue is showing", t.page.locator("#card-clue").inner_text().strip() != "")
    t.check("the answer is not", t.page.locator("#card-word").is_visible(), False)
    t.check("neither are the grade buttons", t.page.locator(".grade.again").is_visible(), False)

    t.page.locator("#reveal-btn").click()
    t.page.wait_for_timeout(300)
    t.check("revealing shows the answer", t.page.locator("#card-word").is_visible(), True)
    t.check("and the grades", t.page.locator(".grade.again").is_visible(), True)
    t.check("and takes its own button away", t.page.locator("#reveal-btn").is_visible(), False)

    intervals = [t.page.locator(f"#i-{n}").inner_text() for n in (1, 2, 3, 4)]
    t.note("what each grade would cost", " · ".join(intervals))
    t.truthy("every grade says when it would come back", all(i.strip() for i in intervals))

    left_before = int(t.page.locator("#left-count").inner_text())
    word = t.page.locator("#card-word").inner_text()
    t.page.locator(".grade.good").click()
    t.page.wait_for_timeout(700)
    t.check("answering moves on", int(t.page.locator("#left-count").inner_text()), left_before - 1)

    rows = t.log()
    t.check("the answer is written down", len(rows), 1)
    entry = rows[0]
    t.check("against the card that was on screen", entry["word"], word)
    t.check("with the rating given", entry["rating"], 3)
    t.truthy("and a stability it did not have before", entry["stability"] > 0)
    t.truthy("and a next interval", entry["scheduled_days"] >= 0)

    # The point of writing it down: it has to still be there tomorrow.
    t.page.reload()
    t.page.wait_for_selector("#screen-home:not([hidden])", timeout=90000)
    t.page.wait_for_timeout(900)
    t.check("and it is still there after a reload", len(t.log()), 1)
    card = next(c for c in t.cards() if c["word"] == word)
    t.truthy("the card kept the schedule it was given", card["fsrs"]["reps"] >= 1)
