"""What the charts say, and whether it is true."""


def run(t):
    t.open_app()
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
    t.check("four of five held, so retention is 80%",
            t.page.locator("#s-retention").inner_text(), "80%")
    t.truthy("and it says what it left out",
             "learning are left out" in t.page.locator("#s-retention-note").inner_text())
    t.check("the tallest activity bar is labelled",
            t.page.locator("#s-activity svg text.peak").count(), 1)
