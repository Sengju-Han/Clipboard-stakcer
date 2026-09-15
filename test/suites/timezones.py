"""A card due today has to be due today, wherever you are.

A review card is owed on a day, not at an instant, and Anki's day begins at
four in the morning. Getting either wrong is invisible in one timezone and
obvious in another: the deck used to write midnight UTC, which is nine in the
morning in Seoul, so somebody reviewing before breakfast opened the app and was
told nothing was owed.
"""

ZONES = ["Asia/Seoul", "UTC", "America/New_York", "Europe/London", "Pacific/Auckland"]

# Local times on the day a card is due, and whether Anki would hand it over.
MOMENTS = [
    ("2026-09-15T12:00:00", False, "the day before"),
    ("2026-09-15T23:30:00", False, "late the night before"),
    ("2026-09-16T00:30:00", False, "after midnight, still yesterday's session"),
    ("2026-09-16T03:59:00", False, "a minute before the rollover"),
    ("2026-09-16T04:01:00", True, "a minute after it"),
    ("2026-09-16T07:00:00", True, "before breakfast"),
    ("2026-09-16T21:00:00", True, "that evening"),
]


def run(t):
    t.open_app()

    for zone in ZONES:
        page = t.browser.new_context(viewport={"width": 390, "height": 844},
                                     timezone_id=zone).new_page()
        try:
            page.goto(t.server.app_url)
            page.wait_for_selector("#screen-home:not([hidden])", timeout=90000)
            page.wait_for_timeout(700)

            wrong = []
            for when, owed, why in MOMENTS:
                got = page.evaluate("""async ({ due, now }) => {
                  const { counts } = await import('./review.js');
                  const card = { id: 'a', deck: 'd', word: 'a', clue: '', example: '',
                    fsrs: { due, stability: 5, difficulty: 5, elapsed_days: 0,
                            scheduled_days: 5, learning_steps: 0, reps: 3, lapses: 0,
                            state: 2, last_review: null } };
                  return counts([card], new Date(now)).due;
                }""", {"due": "2026-09-16T12:00:00+00:00", "now": when})
                if bool(got) != owed:
                    wrong.append(f"{when} ({why}): said {got}")
            t.check(f"{zone} hands the card over at four in the morning and not before", wrong, [])
        finally:
            page.close()

    # And a learning card is still timed in minutes, because ten minutes means
    # ten minutes and not "some time after four tomorrow".
    early = t.page.evaluate("""async () => {
      const { isDue } = await import('./review.js');
      const soon = new Date(Date.now() + 10 * 60000).toISOString();
      const card = { id: 'b', fsrs: { due: soon, state: 1, stability: 0, difficulty: 5,
        elapsed_days: 0, scheduled_days: 0, learning_steps: 1, reps: 1, lapses: 0, last_review: null } };
      return [isDue(card), isDue(card, Date.now() + 11 * 60000)];
    }""")
    t.check("a card due in ten minutes is not due now", early[0], False)
    t.check("and is due in eleven", early[1], True)

    # The deck this repository publishes has to read as a real schedule too.
    written = t.page.evaluate("""async () => {
      const res = await fetch('../deck/deck.json');
      const deck = await res.json();
      const reviewed = deck.cards.filter(c => c.fsrs.state === 2);
      return reviewed.length ? reviewed[0].fsrs.due : '';
    }""")
    t.note("the published deck writes its due dates as", written)
    t.check("at noon, so the day survives being read anywhere", "T12:00" in written, True)
