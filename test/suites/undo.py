"""Taking back an answer you did not mean to give."""


def run(t):
    t.open_app()
    before = {c["word"]: c for c in t.cards()}

    t.page.locator("#start-btn").click()
    t.page.wait_for_timeout(700)
    t.check("undo is not offered before anything is answered",
            t.page.locator("#undo-btn").is_visible(), False)

    t.page.locator("#reveal-btn").click()
    t.page.wait_for_timeout(250)
    word = t.page.locator("#card-word").inner_text()
    left = int(t.page.locator("#left-count").inner_text())
    t.page.locator(".grade.good").click()
    t.page.wait_for_timeout(700)

    t.check("answering offers it", t.page.locator("#undo-btn").is_visible(), True)
    t.check("and the answer was written down", len(t.log()), 1)

    t.page.locator("#undo-btn").click()
    t.page.wait_for_timeout(1000)

    card = next(c for c in t.cards() if c["word"] == word)
    t.check("undo puts the schedule back exactly", card["fsrs"], before[word]["fsrs"])
    t.check("the count comes back", int(t.page.locator("#left-count").inner_text()), left)
    t.check("the card is on screen again", t.page.locator("#card-word").inner_text(), word)
    t.check("with its answer showing", t.page.locator("#card-word").is_visible(), True)
    t.check("and the buttons live", t.page.locator(".grade.good").is_visible(), True)
    t.check("nothing left to undo", t.page.locator("#undo-btn").is_visible(), False)

    rows = t.log()
    t.check("the log row stays", len(rows), 1)
    t.check("marked rather than deleted", rows[0].get("undone"), True)

    # Again requeues the card; undoing has to take that copy with it.
    left = int(t.page.locator("#left-count").inner_text())
    t.page.locator(".grade.again").click()
    t.page.wait_for_timeout(900)
    t.check("Again holds the card up", t.page.locator("#next-btn").is_visible(), True)
    t.page.locator("#undo-btn").click()
    t.page.wait_for_timeout(1000)
    t.check("undoing an Again leaves the session the size it was",
            int(t.page.locator("#left-count").inner_text()), left)
    t.check("and the same card on screen", t.page.locator("#card-word").inner_text(), word)

    t.page.locator("#quit-btn").click()
    t.page.wait_for_timeout(600)
    t.page.locator("#stats-btn").click()
    t.page.wait_for_timeout(1500)
    t.check("and the statistics count none of it",
            t.page.locator("#s-done").inner_text(), "0")
