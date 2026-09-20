"""A conversation built out of the words you are in the middle of learning."""

import io
import json
import math
import struct
import wave

NOTHING = {"said": "", "better": "", "why": ""}


def playable(seconds=0.2):
    """Audio a browser will really decode.

    What the server sends is an mp3; what matters on this side is only that it
    plays, and that when it does the phone's own voice stays quiet. A wav is
    made here rather than an mp3 because it can be written honestly in nine
    lines, and because the format is the server's business - its own suite
    checks that what comes back is what Microsoft sent.
    """
    rate = 8000
    out = io.BytesIO()
    with wave.open(out, "w") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"".join(
            struct.pack("<h", int(6000 * math.sin(2 * math.pi * 440 * i / rate)))
            for i in range(int(rate * seconds))))
    return out.getvalue()


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


    _voice(t)


def _voice(t):
    """The voice itself, which is the whole difference between this screen
    sounding like the cards and sounding like a satnav.

    The neural voice cannot be fetched by the page - the service wants an Origin
    header of `chrome-extension://...` and a browser will not let a page set one
    - so it comes from the server. Everything here is about what happens when it
    does not: the phone's own voice has to take over, out loud, rather than the
    screen going quiet.
    """
    # What the phone's own voice was asked to say, if anything.
    t.page.evaluate("""() => {
      window.__robot = [];
      const real = speechSynthesis.speak.bind(speechSynthesis);
      speechSynthesis.speak = (utter) => { window.__robot.push(utter.text); real(utter); };
    }""")

    asked = []

    def voice_route(route):
        url = route.request.url
        asked.append(url)
        route.fulfill(status=200, content_type="audio/wav", body=playable())

    t.page.route("**/api/say*", voice_route)

    picker = t.page.locator("#t-voice")
    t.check("the screen offers a voice", picker.is_visible(), True)
    t.check("and starts on the one the cards use", picker.input_value(), "en-US-AvaNeural")
    t.truthy("with the phone's own still on the list",
             "phone" in picker.evaluate("el => [...el.options].map(o => o.value).join(',')"))

    asked.clear()
    t.page.evaluate("() => { window.__robot = []; }")
    t.page.locator("#t-repeat").click()
    t.page.wait_for_timeout(1500)

    t.truthy("saying a line again asks the server for it", len(asked) >= 1)
    t.truthy("by name", "voice=en-US-AvaNeural" in (asked[0] if asked else ""))
    t.truthy("and with the words", "text=" in (asked[0] if asked else ""))
    # The point of all of it. If both spoke, the reply would be said twice.
    t.check("and the phone's own voice stays out of it",
            t.page.evaluate("() => window.__robot.length"), 0)

    # ---- when the server cannot help -------------------------------------
    t.page.unroute("**/api/say*")
    t.page.route("**/api/say*", lambda route: route.fulfill(
        status=502, content_type="application/json",
        body=json.dumps({"error": "The voice service answered 403."})))

    t.page.evaluate("() => { window.__robot = []; }")
    t.page.locator("#t-repeat").click()
    t.page.wait_for_timeout(1500)
    t.check("a server that cannot speak hands over to the phone",
            t.page.evaluate("() => window.__robot.length"), 1)
    t.truthy("saying the same words",
             t.page.evaluate("() => window.__robot[0] || ''") != "")

    # A 200 with an empty body is the failure that looks like success: it would
    # play as silence and pass for a turn.
    t.page.unroute("**/api/say*")
    t.page.route("**/api/say*", lambda route: route.fulfill(
        status=200, content_type="audio/mpeg", body=b""))
    t.page.evaluate("() => { window.__robot = []; }")
    t.page.locator("#t-repeat").click()
    t.page.wait_for_timeout(1500)
    t.check("and so does an answer with no audio in it",
            t.page.evaluate("() => window.__robot.length"), 1)

    # ---- choosing the phone on purpose -----------------------------------
    t.page.unroute("**/api/say*")
    asked.clear()
    t.page.route("**/api/say*", voice_route)
    t.page.evaluate("() => { window.__robot = []; }")
    picker.select_option("phone")
    t.page.wait_for_timeout(1200)
    t.check("picking the phone's voice speaks straight away",
            t.page.evaluate("() => window.__robot.length"), 1)
    t.check("and does not trouble the server at all", len(asked), 0)

    t.page.reload()
    t.page.wait_for_selector("#screen-home:not([hidden])", timeout=90000)
    t.page.wait_for_timeout(900)
    t.page.locator("#talk-btn").click()
    t.page.wait_for_timeout(1500)
    t.check("and the choice is still there next time",
            t.page.locator("#t-voice").input_value(), "phone")
