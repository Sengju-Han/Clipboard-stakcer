"""A conversation built out of the words you are in the middle of learning."""

import json

NOTHING = {"said": "", "better": "", "why": ""}


def reply(n):
    return {
        "reply": f"Turn {n}. And then what?",
        "used": ["avow"],                       # claimed whether or not it was said
        "correction": {"said": "I go to park", "better": "I went to the park",
                       "why": "past tense, and parks take 'the'"} if n == 1 else NOTHING,
    }


def run(t):
    sent = []

    def answer(route):
        body = json.loads(route.request.post_data)
        sent.append(body)
        n = len([m for m in body["messages"] if m["role"] == "assistant"])
        route.fulfill(status=200, content_type="application/json", body=json.dumps(
            {"stop_reason": "end_turn", "content": [{"type": "text", "text": json.dumps(reply(n))}]}))

    t.page.route("https://api.anthropic.com/**", answer)
    t.open_app()

    t.page.locator("#talk-btn").click()
    t.page.wait_for_timeout(1200)
    t.check("without a key it says so",
            "Anthropic key" in t.page.locator("#t-log .bubble.note").inner_text(), True)
    t.check("and offers nothing else", t.page.locator("#t-input").is_visible(), False)
    t.page.locator("#talk-close").click()
    t.page.wait_for_timeout(500)

    t.set_pref("anthropic", "sk-ant-test")
    t.page.reload()
    t.page.wait_for_selector("#screen-home:not([hidden])", timeout=90000)
    t.page.wait_for_timeout(900)

    t.page.locator("#talk-btn").click()
    t.page.wait_for_timeout(2500)
    targets = [x.strip() for x in t.page.locator("#t-words .chip").all_inner_texts()]
    t.check("six words are picked to practise", len(targets), 6)
    t.truthy("they are in the system prompt", "TARGET WORDS" in sent[0]["system"])
    t.truthy("Claude speaks first",
             t.page.locator("#t-log .bubble.them p").first.inner_text() != "")
    t.check("and the opener is not shown as something the learner said",
            t.page.locator("#t-log .bubble.me").count(), 0)

    target = targets[0]
    t.page.locator("#t-say").fill(f"Well I would say {target} about that")
    t.page.locator("#t-send").click()
    t.page.wait_for_timeout(2000)
    t.truthy("what they said is on screen",
             target in t.page.locator("#t-log .bubble.me").last.inner_text())
    t.check("a word they used is ticked off", t.page.locator("#t-words .chip.on").count(), 1)
    t.check("the correction is shown under the reply", t.page.locator("#t-log .fix").count(), 1)

    ticked = t.page.locator("#t-words .chip.on").count()
    t.page.locator("#t-say").fill("yes it was fine thank you")
    t.page.locator("#t-send").click()
    t.page.wait_for_timeout(2000)
    t.check("a word Claude credits but they never said is not ticked",
            t.page.locator("#t-words .chip.on").count(), ticked)
    t.note("why", "a word credited that was never said is a word that stops being practised")

    for _ in range(6):
        t.page.locator("#t-say").fill("and then something else happened")
        t.page.locator("#t-send").click()
        t.page.wait_for_timeout(1100)
    t.page.wait_for_timeout(800)
    t.check("eight exchanges end the session", t.page.locator("#t-recap").is_hidden(), False)
    t.check("and put the input away", t.page.locator("#t-input").is_hidden(), True)
    recap = t.page.locator("#t-recap").inner_text().lower()
    t.truthy("the recap says what was reached for", "reached for" in recap)
    t.truthy("what was not", "only on paper" in recap)
    t.truthy("and what to say differently", "differently" in recap)

    t.page.locator("#t-again").click()
    t.page.wait_for_timeout(2500)
    t.check("another conversation clears the recap", t.page.locator("#t-recap").is_hidden(), True)
    t.check("the ticks", t.page.locator("#t-words .chip.on").count(), 0)
    t.check("and brings the input back", t.page.locator("#t-input").is_visible(), True)
