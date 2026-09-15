"""Why a word will not stick, at the moment it did not."""

import json

ANSWER = {
    "recognised": True, "word": "x", "meaning": "m", "pronunciation": "/x/",
    "korean": "K", "tone": "neutral",
    "nuance": "Only used when somebody is being evasive.",
    "collocations": ["stop x-ing"],
    "confusables": [{"word": "hedge", "difference": "hedge is noncommittal"}],
    "examples": [], "memory_hook": "picture it",
}


def run(t):
    calls = []
    t.page.route("https://api.anthropic.com/**", lambda route: (
        calls.append(1),
        route.fulfill(status=200, content_type="application/json", body=json.dumps(
            {"stop_reason": "end_turn",
             "content": [{"type": "text", "text": json.dumps(ANSWER)}]})),
    ))

    t.open_app()
    # "put off" is one of the words this repository has already explained, so
    # the cheapest source is a static file and the API is never touched.
    t.put_card(None, {"word": "put off",
                      "fsrs": {"due": "2020-01-01T00:00:00.000Z", "state": 2}})

    t.page.locator("#start-btn").click()
    t.page.wait_for_timeout(800)
    t.page.locator("#reveal-btn").click()
    t.page.wait_for_timeout(400)
    t.page.locator("#explain-btn").click()
    t.page.wait_for_timeout(3000)

    panel = t.page.locator("#brain").inner_text()
    t.truthy("an explained word explains itself", "when you would use it" in panel.lower())
    t.check("without an API call", len(calls), 0)
    t.note("where it came from", "the explanations committed to this repository")
    # What it deliberately leaves out matters as much as what it shows: the
    # word, the meaning and an example are already on the card, and repeating
    # them pushes the part worth reading off a phone screen.
    headings = [h.lower() for h in t.page.locator("#brain h4").all_inner_texts()]
    t.note("what it chose to say", " / ".join(headings))
    t.truthy("it explains when you would reach for it", "when you would use it" in headings)
    t.check("and never restates the meaning",
            [h for h in headings if h in ("meaning", "pronunciation", "korean", "example")], [])

    t.page.locator(".grade.again").click()
    t.page.wait_for_timeout(2500)
    t.check("Again hides the grades", t.page.locator(".grade.again").is_visible(), False)
    t.check("offers a way on", t.page.locator("#next-btn").is_visible(), True)
    t.check("keeps the card up", t.page.locator("#card-word").inner_text(), "put off")
    t.truthy("and opens the explanation by itself",
             "when you would use it" in t.page.locator("#brain").inner_text().lower())

    t.page.locator("#next-btn").click()
    t.page.wait_for_timeout(900)
    t.check("moving on clears it", t.page.locator("#brain").inner_text().strip(), "")
    t.check("and hides the way on again", t.page.locator("#next-btn").is_visible(), False)
    t.check("the next card is not revealed", t.page.locator(".grade.again").is_visible(), False)
    t.check("it is offered", t.page.locator("#reveal-btn").is_visible(), True)
