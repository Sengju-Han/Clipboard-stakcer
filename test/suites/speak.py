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
    # The matcher itself, on the words that used to defeat it. Deterministic,
    # unlike the six drawn at random above: a hyphen survived in the word and
    # not in the sentence, so the regex could never match, and saying one of
    # these out loud never ticked it off.
    matched = t.page.evaluate("""async () => {
      const { spotted } = await import("./talk.js");
      const cases = ["tie-dye", "jam-packed", "litter-mates", "ne'er-do-well",
                     "hangers-on", "put off", "avow"];
      return cases.map((w) => [w, spotted(`Well I would say ${w} about that`, w)]);
    }""")
    t.check("every awkward word matches itself",
            [w for w, hit in matched if not hit], [])
    # A machine chooses how to spell an accent, and a card should not depend on
    # which way it chose: a recogniser writes "seance" where a subtitle writes
    # "séance". Before accents were folded the word tidied to "s ance", which
    # needed the sentence to contain a lone "s" — so the card ticked off or did
    # not according to how the transcription happened to come out.
    t.check("an accent is a spelling, not a different word",
            t.page.evaluate("""async () => {
              const { spotted } = await import("./talk.js");
              return ["seance", "s\u00e9ance"].map((w) => spotted("we went to a " + w, "s\u00e9ance"));
            }"""), [True, True])

    t.truthy("and a word that was not said does not match",
             not t.page.evaluate("""async () => {
               const { spotted } = await import("./talk.js");
               return spotted("Well I would say nothing about that", "tie-dye");
             }"""))

    # The word they used, not a count of one. The six are drawn at random from
    # the deck, and two of them can legitimately both be ticked by one sentence:
    # saying "chiseled" uses "chisel" as well, and both were targets. Counting
    # made that a failure about four runs in a hundred, which is exactly often
    # enough to look like something else.
    ticked = [x.strip().lstrip("\u2713").strip()
              for x in t.page.locator("#t-words .chip.on").all_inner_texts()]
    t.truthy(f"the word they used is ticked off ({target})", target in ticked)
    t.note("ticked", ", ".join(ticked) or "none")
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
