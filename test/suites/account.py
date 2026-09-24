"""Signing in on a second device, and what the server is handed.

The Add to Anki page holds two keys: a GitHub token that can run workflows on
somebody's repository, and an Anthropic key that can spend their money. The
account exists so those do not have to be found and retyped on every device.
What it must never do is hand either of them to the server in a form the server
can read - so this drives the whole thing in a browser and then looks at the
bytes that actually went over the wire.
"""

import json
import re
from pathlib import Path

TOKEN = "github_pat_11ABCDEFG0123456789_secretpartnobodyshouldsee"
KEY = "sk-ant-api03-thisisnottherealkeyobviously"
PASSWORD = "a long enough password"


def _fake_server(t):
    """A vault server in the test, so the page meets a real one, not a mock of itself."""
    state = {"blob": "", "seen": [], "email": "", "secret": "", "bodies": []}

    def handle(route):
        request = route.request
        path = request.url.split("/api/", 1)[1].split("?")[0]
        body = json.loads(request.post_data or "{}") if request.post_data else {}
        if request.post_data:
            state["bodies"].append(request.post_data)

        if path in ("register", "login"):
            if path == "register":
                state["email"], state["secret"] = body.get("email", ""), body.get("secret", "")
            elif body.get("secret") != state["secret"]:
                return route.fulfill(status=401, content_type="application/json",
                                     body=json.dumps({"error": "That email and password do not match."}))
            return route.fulfill(status=200, content_type="application/json",
                                 body=json.dumps({"token": "session-token", "email": state["email"]}))
        if path == "logout":
            return route.fulfill(status=200, content_type="application/json", body="{}")
        if path == "vault" and request.method == "GET":
            return route.fulfill(status=200, content_type="application/json",
                                 body=json.dumps({"blob": state["blob"], "mod": 1}))
        if path == "vault" and request.method == "PUT":
            state["blob"] = body.get("blob", "")
            state["seen"].append(state["blob"])
            return route.fulfill(status=200, content_type="application/json",
                                 body=json.dumps({"ok": True, "mod": 2}))
        return route.fulfill(status=404, content_type="application/json", body="{}")

    t.page.route("https://vault.test/api/**", handle)
    return state


def _open(t):
    """A fresh device, pointed at the test's own server.

    There is no box for the address any more - there is one server and asking
    which one was a question with a single answer. The override the tests use
    is the same one a moved Worker would use, and there is no UI for it.
    """
    t.page.goto(f"http://127.0.0.1:{t.server.port}/index.html")
    t.page.wait_for_selector("#settings", timeout=30000)
    t.page.evaluate("""() => {
      try { localStorage.clear(); } catch {}
      try { sessionStorage.clear(); } catch {}
      try { localStorage.setItem("lexis:server", "https://vault.test"); } catch {}
    }""")
    t.page.reload()
    t.page.wait_for_selector("#settings", timeout=30000)


def _panels(t):
    """Both open. The keys live with the sign-in; the rest lives in Settings."""
    for panel in ("account", "settings"):
        t.page.locator(f"#{panel}").evaluate("el => el.open = true")


def _fill(t, field, value):
    t.page.locator(f"#{field}").fill(value)
    t.page.locator(f"#{field}").dispatch_event("change")


def run(t):
    state = _fake_server(t)
    _open(t)

    # ---- first device ----------------------------------------------------
    _panels(t)
    _fill(t, "token", TOKEN)
    _fill(t, "anthropic", KEY)
    _fill(t, "owner", "Sengju-Han")
    _fill(t, "repo", "Clipboard-stakcer")

    _panels(t)
    t.page.locator("#acct-email").fill("me@example.test")
    t.page.locator("#acct-pw").fill(PASSWORD)
    t.page.locator("#acct-up").click()
    t.page.wait_for_timeout(2500)

    t.check("the account is made and the page says so",
            "account" in t.page.locator("#acct-say").inner_text().lower(), True)
    t.truthy("something was stored", bool(state["blob"]))

    # The server is not supposed to be able to hash a password, and is not
    # supposed to see one. Both halves are checked by reading what was sent.
    everything = " ".join(state["bodies"])
    t.check("the password itself never crossed the wire",
            PASSWORD in everything, False)
    t.check("what was sent instead is 32 bytes of derived secret",
            len(state["secret"]), 44)
    t.truthy("and it is not the password in disguise",
             PASSWORD not in state["secret"] and state["secret"] != "")

    # The whole point of the exercise.
    blob = state["blob"]
    t.check("the GitHub token is not in what the server got", TOKEN in blob, False)
    t.check("nor the Anthropic key", KEY in blob, False)
    t.check("nor any recognisable part of them",
            any(part in blob for part in ("github_pat", "sk-ant", "Sengju-Han")), False)
    t.check("what it got is a versioned box", blob.startswith("v2."), True)
    t.check("with a nonce and the ciphertext", len(blob.split(".")), 3)

    # A second write must not reuse the nonce, or two versions of the same
    # settings leak their difference.
    _fill(t, "ref", "main")
    t.page.wait_for_timeout(1800)
    t.truthy("a second save is a different box", len(state["seen"]) >= 2
             and state["seen"][-1] != state["seen"][0])

    # ---- second device ---------------------------------------------------
    # Same page, nothing remembered: exactly what a new phone sees.
    _open(t)
    _panels(t)
    t.check("the new device starts with no token", t.page.locator("#token").input_value(), "")

    _panels(t)
    t.page.locator("#acct-email").fill("me@example.test")
    t.page.locator("#acct-pw").fill("the wrong password")
    t.page.locator("#acct-in").click()
    t.page.wait_for_timeout(2500)

    said = t.page.locator("#acct-say").inner_text()
    # Two ways it can be wrong, and both have to say so: the server refuses the
    # sign-in, or the sign-in works and the box will not open - which is what a
    # changed password against an older vault looks like.
    t.truthy("a wrong password is refused",
             "do not match" in said or "does not open" in said)
    t.check("and nothing is filled in from a failed attempt",
            t.page.locator("#token").input_value(), "")
    t.note("it said", said)

    t.page.locator("#acct-pw").fill(PASSWORD)
    t.page.locator("#acct-in").click()
    t.page.wait_for_timeout(2500)

    t.check("the right password brings the token back", t.page.locator("#token").input_value(), TOKEN)
    t.check("and the Anthropic key", t.page.locator("#anthropic").input_value(), KEY)
    t.check("and the repository", t.page.locator("#repo").input_value(), "Clipboard-stakcer")
    t.check("and the branch saved after signing in", t.page.locator("#ref").input_value(), "main")
    t.check("the password box is emptied once it has been used",
            t.page.locator("#acct-pw").input_value(), "")

    # ---- after a reload --------------------------------------------------
    # The session outlives a reload and the password does not, so a change made
    # after one used to be dropped without a word: the page looked signed in,
    # said nothing, and saved nothing. That reads as the whole thing not
    # working, and it is the case somebody actually meets.
    before = len(state["seen"])
    t.page.reload()
    t.page.wait_for_selector("#settings", timeout=30000)
    t.page.wait_for_timeout(900)
    _panels(t)
    t.check("there is no address to fill in at all",
            t.page.locator("#acct-server").count(), 0)
    t.check("and the email is still there", t.page.locator("#acct-email").input_value(),
            "me@example.test")
    t.check("and it is not asking to be unlocked",
            "locked" in t.page.locator("#acct-who").inner_text(), False)

    _panels(t)
    _fill(t, "anthropic", "sk-ant-api03-typed-after-a-reload")
    t.page.wait_for_timeout(2200)
    t.check("a key changed after a reload is still saved", len(state["seen"]) > before, True)
    t.note("it said", t.page.locator("#acct-say").inner_text())

    # And the state where it genuinely cannot save says so out loud.
    t.page.evaluate("() => { try { sessionStorage.clear(); } catch {} }")
    t.page.reload()
    t.page.wait_for_selector("#settings", timeout=30000)
    t.page.wait_for_timeout(900)
    _panels(t)
    stuck = len(state["seen"])
    _fill(t, "anthropic", "sk-ant-api03-typed-while-locked")
    t.page.wait_for_timeout(2200)
    t.check("but a locked device does not pretend to have saved",
            len(state["seen"]), stuck)
    t.truthy("and says it is locked rather than nothing at all",
             "locked" in t.page.locator("#acct-say").inner_text())

    # Put it back for the rest of the suite.
    _panels(t)
    t.page.locator("#acct-pw").fill(PASSWORD)
    t.page.locator("#acct-in").click()
    t.page.wait_for_timeout(2500)
    _fill(t, "anthropic", KEY)
    t.page.wait_for_timeout(1800)


    # ---- what is left behind --------------------------------------------
    stored = t.page.evaluate("""() => {
      const out = {};
      for (let i = 0; i < localStorage.length; i += 1) {
        const k = localStorage.key(i);
        out[k] = localStorage.getItem(k);
      }
      return out;
    }""")
    t.check("the password is not written down anywhere",
            any(PASSWORD in v for v in stored.values()), False)
    t.truthy("the session is", any(k.endswith("acct.token") for k in stored))

    t.page.locator("#acct-out").click()
    t.page.wait_for_timeout(900)
    after = t.page.evaluate("() => localStorage.getItem('addcard.acct.token') || ''")
    t.check("signing out drops the session", after, "")
    t.check("but leaves this device usable", t.page.locator("#token").input_value(), TOKEN)

    # ---- the other page --------------------------------------------------
    # The whole point of one vault rather than two: the review app needs the
    # same GitHub token and the same Anthropic key, and used to ask for them
    # again, separately, on every device.
    # The same device, not a scrubbed one: this is somebody who set the other
    # page up a minute ago and now opens the review app.
    t.open_app()
    t.open_settings()

    t.check("the review app starts with no keys either",
            [t.page.locator("#anthropic").input_value(), t.page.locator("#gh-token").input_value()],
            ["", ""])
    # No box here either: the review app knows where the server is for the same
    # reason the other page does.
    t.check("and no address to fill in here either",
            t.page.locator("#acct-base").count(), 0)

    t.page.locator("#acct-email").fill("me@example.test")
    t.page.locator("#acct-pw").fill(PASSWORD)
    t.page.locator("#acct-in-btn").click()
    t.page.wait_for_timeout(3000)

    t.note("it said", t.page.locator("#acct-note").inner_text())
    t.check("signing in there brings the GitHub token across",
            t.page.locator("#gh-token").input_value(), TOKEN)
    t.check("and the Anthropic key", t.page.locator("#anthropic").input_value(), KEY)
    t.check("and the repository", t.page.locator("#gh-repo").input_value(), "Clipboard-stakcer")

    # It was saved from the other page, so it is the one box, not a second one.
    t.check("both pages wrote to one vault", len([b for b in state["seen"]]) >= 2, True)

    # A key changed here has to go back, or the two pages disagree from now on.
    t.page.locator("#anthropic").fill("sk-ant-api03-changed-on-the-review-app")
    t.page.locator("#anthropic").dispatch_event("change")
    t.page.wait_for_timeout(2000)
    t.truthy("and a key changed here is sent back", state["seen"][-1] != blob)

    _an_empty_submit_says_so(t)
    _the_sentence_is_read_as_you_write_it(t)
    _the_meaning_goes_onto_the_card(t)


def _an_empty_submit_says_so(t):
    """A tap that does nothing reads as a page that is broken.

    Submitting with no field filled in returned early and silently. On the page
    that holds the GitHub token and the Anthropic key — the one that took three
    goes to get right — "I tapped Add card and nothing happened" is
    indistinguishable from the account being broken again.
    """
    t.page.goto(f"http://127.0.0.1:{t.server.port}/index.html")
    t.page.wait_for_selector("#settings", timeout=30000)
    t.page.wait_for_timeout(600)

    # The deck and note type are required, so the browser itself objects when
    # they are empty - that case was never silent. The silent one is both of
    # them filled and every field left blank, which is what a mis-tap looks
    # like.
    t.page.locator("#deck").fill("Steve Jobs")
    for box in t.page.locator("#fields textarea").all():
        box.fill("")
    t.page.locator("#go").click()
    t.page.wait_for_timeout(700)

    said = t.page.locator("#feed").inner_text()
    t.truthy("an empty card says what is missing", "fill in at least one field" in said.lower())
    t.note("it said", said.strip().replace("\n", " ")[:110])


# ---- the example sentence -------------------------------------------------

NATURAL = {"verdict": "natural", "natural": "", "why": "", "words": [], "slang": []}

FIXED = {
    "verdict": "understandable",
    "natural": "I recommend the restaurant I went to yesterday.",
    "why": "it already happened, so it takes -ed",
    "words": [{
        "typed": "reccomend", "meant": "recommend",
        "meaning": "to say something is good and worth choosing",
        "korean": "추천하다",
    }],
    "slang": [{
        "phrase": "hit the spot", "meaning": "was exactly what you wanted",
        "register": "casual",
    }],
}


def _the_sentence_is_read_as_you_write_it(t):
    """The example field is where the learner writes English of their own, and
    until now it was the one field nothing read.

    What is checked here is not that a sentence gets corrected - it is that a
    correction never arrives alone. A verdict on its own is worthless to
    somebody who does not yet know what right looks like, so the natural
    version, the meaning of every word they got wrong, and any slang all have
    to come with it. And that nothing is ever replaced without being asked.

    Every sentence below is different on purpose. Two guards make a repeat
    free - the cache, and a check that the text actually changed - and reusing
    a sentence means testing those instead of the thing in hand.
    """
    asked = []

    def claude(route):
        body = json.loads(route.request.post_data)
        asked.append(body)
        said = body["messages"][0]["content"]
        out = FIXED if "reccomend" in said else NATURAL
        route.fulfill(status=200, content_type="application/json", body=json.dumps(
            {"stop_reason": "end_turn", "content": [{"type": "text", "text": json.dumps(out)}]}))

    t.page.route("https://api.anthropic.com/**", claude)
    t.page.goto(f"http://127.0.0.1:{t.server.port}/index.html")
    t.page.wait_for_selector("#settings", timeout=30000)
    # A fresh device. Without this the key from earlier in this suite is still
    # in storage, and the no-key case below silently tests the opposite.
    t.page.evaluate("""() => {
      try { localStorage.clear(); } catch {}
      try { sessionStorage.clear(); } catch {}
    }""")
    t.page.reload()
    t.page.wait_for_selector("#settings", timeout=30000)
    t.page.wait_for_timeout(800)

    box = t.page.locator("#f-Example")
    panel = t.page.locator("#said")
    t.check("the example field has somewhere to be read", panel.count(), 1)

    # ---- no key: it says so once, and asks Claude nothing -----------------
    asked.clear()
    box.fill("She go to the market every mornings.")
    t.page.wait_for_timeout(2400)
    t.truthy("with no key it says where to put one",
             "Anthropic key" in panel.inner_text())
    t.check("and spends nothing finding that out", len(asked), 0)

    _panels(t)
    _fill(t, "anthropic", KEY)

    # ---- a fragment is not a sentence -------------------------------------
    asked.clear()
    box.fill("the cat")
    t.page.wait_for_timeout(2400)
    t.check("a fragment is left alone", len(asked), 0)
    t.check("and says nothing about it", panel.inner_text().strip(), "")

    # ---- a sentence that needs work ---------------------------------------
    box.fill("I reccomend the restaurant I go yesterday.")
    t.page.wait_for_timeout(2800)
    t.check("a real sentence is read once", len(asked), 1)
    t.truthy("and the sentence is what was sent",
             "reccomend" in asked[0]["messages"][0]["content"])

    said = panel.inner_text()
    t.truthy("it offers the natural version",
             "I recommend the restaurant I went to yesterday." in said)
    t.truthy("and says what changed in plain words", "already happened" in said)
    # The whole point of the exercise: a word you got wrong is a word you do
    # not know yet, so it comes back with what it means.
    t.truthy("a word typed wrong comes back with what it means",
             "to say something is good and worth choosing" in said)
    t.truthy("and its Korean", "\uCD94\uCC9C\uD558\uB2E4" in said)
    t.truthy("slang is explained rather than removed",
             "was exactly what you wanted" in said)
    t.truthy("with how casual it is", "casual" in said)

    # ---- and nothing is taken without being asked -------------------------
    t.check("the field is still exactly what they typed",
            box.input_value(), "I reccomend the restaurant I go yesterday.")
    t.check("both ways out are offered",
            [t.page.locator("#said button.take").count(),
             t.page.locator("#said button.keep").count()], [1, 1])

    t.page.locator("#said button.keep").click()
    t.page.wait_for_timeout(500)
    t.check("keeping yours leaves the sentence alone",
            box.input_value(), "I reccomend the restaurant I go yesterday.")
    t.check("and puts the panel away", panel.inner_text().strip(), "")

    # ---- taking the suggestion --------------------------------------------
    asked.clear()
    box.fill("I reccomend this place to everybody I meet.")
    t.page.wait_for_timeout(2800)
    t.check("a different sentence is read", len(asked), 1)
    t.page.locator("#said button.take").click()
    t.page.wait_for_timeout(600)
    t.check("taking it puts it in the field",
            box.input_value(), "I recommend the restaurant I went to yesterday.")
    t.check("and does not then ask about its own suggestion", len(asked), 1)

    # ---- a sentence that is already fine ----------------------------------
    asked.clear()
    box.fill("The train was jam-packed this morning.")
    t.page.wait_for_timeout(2800)
    t.check("a natural sentence is read", len(asked), 1)
    t.truthy("and told so", "reads naturally" in panel.inner_text())
    t.check("and is offered no rewrite", t.page.locator("#said button.take").count(), 0)

    # ---- half a sentence is not a sentence --------------------------------
    # A pause is a poor signal on a phone: thinking about the next word looks
    # exactly like having finished. Paid for, and the answer is about a
    # sentence nobody was trying to write.
    asked.clear()
    box.fill("I was walking home when I saw")
    t.page.wait_for_timeout(2200)
    t.check("a sentence with no ending is left alone for longer", len(asked), 0)
    t.page.wait_for_timeout(2600)
    t.check("and then read anyway, rather than never", len(asked), 1)

    asked.clear()
    box.fill("Leaving the field is finishing too")
    t.page.wait_for_timeout(400)
    box.blur()
    t.page.wait_for_timeout(1200)
    t.check("moving on says it is finished without waiting", len(asked), 1)

    # ---- the same sentence twice is free ----------------------------------
    box.fill("Something else entirely, just for a moment here.")
    t.page.wait_for_timeout(2800)
    spent = len(asked)
    box.fill("The train was jam-packed this morning.")
    t.page.wait_for_timeout(2800)
    t.check("a sentence already read is not paid for twice", len(asked), spent)
    t.truthy("and comes straight back", "reads naturally" in panel.inner_text())

    t.page.unroute("https://api.anthropic.com/**")


# ---- the meaning, onto the card -------------------------------------------

# The same answer, and the same markup, that anki/fold_meaning.py is pinned
# against. One block of HTML has two authors - this page writes it in the
# browser when a card is sent, and the workflow writes it in Python for the
# cards that already exist - and a card folded on the phone has to be the same
# card as one folded by the workflow. This is the half of that pin that runs
# the real page.
FOLDED = json.loads(
    (Path(__file__).resolve().parents[1] / "folded-meaning.json")
    .read_text(encoding="utf-8"))
EXPLAINED = FOLDED["info"]

# The first block tag in a field is where every reader in this project stops
# looking for the word: anki/proofread.py, the voice, this app's own importer.
# Written out here rather than imported so the test fails if the page starts
# writing the block somewhere the rest of the project will not skip.
BLOCK_TAG = re.compile(r"<\s*(?:div|br|p|li|tr|h[1-6]|details|summary)\b[^>]*>", re.I)


def _the_meaning_goes_onto_the_card(t):
    """Everything the page works out about a word used to stop at the page.

    You read the meaning once, while making the card, and then met that card a
    hundred times with nothing on it but the word and your own sentence - while
    the answer sat in a file in this repository the whole time. So it goes onto
    the back of the card, folded, and this checks the two halves of that: that
    it arrives, and that folding it in front of the word would have broken
    everything downstream, so it does not.
    """
    sent = []
    read = []

    def claude(route):
        body = json.loads(route.request.post_data)
        # One URL, two contracts. The sentence checker names itself in its
        # system prompt; anything else is the word lookup.
        sentence = "example sentence" in (body.get("system") or "")
        if sentence:
            read.append(body["messages"][0]["content"])
        out = NATURAL if sentence else EXPLAINED
        route.fulfill(status=200, content_type="application/json", body=json.dumps(
            {"stop_reason": "end_turn", "content": [{"type": "text", "text": json.dumps(out)}]}))

    def github(route):
        request = route.request
        if request.method == "POST" and "/dispatches" in request.url:
            sent.append(json.loads(request.post_data or "{}"))
            return route.fulfill(status=204, body="")
        # Everything else - the explanation this page writes back to the
        # repository, the run list it polls - answers politely and says
        # nothing, so the page falls through to the API for the lookup.
        if request.method == "GET" and "/runs" in request.url:
            return route.fulfill(status=200, content_type="application/json",
                                 body=json.dumps({"workflow_runs": []}))
        return route.fulfill(status=404, content_type="application/json",
                             body=json.dumps({"message": "not here"}))

    t.page.route("https://api.anthropic.com/**", claude)
    t.page.route("https://api.github.com/**", github)

    t.page.goto(f"http://127.0.0.1:{t.server.port}/index.html")
    t.page.wait_for_selector("#settings", timeout=30000)
    t.page.evaluate("""() => {
      try { localStorage.clear(); } catch {}
      try { sessionStorage.clear(); } catch {}
    }""")
    t.page.reload()
    t.page.wait_for_selector("#settings", timeout=30000)
    t.page.wait_for_timeout(800)

    _panels(t)
    _fill(t, "token", TOKEN)
    _fill(t, "anthropic", KEY)
    _fill(t, "owner", "Sengju-Han")
    _fill(t, "repo", "Clipboard-stakcer")
    t.page.locator("#deck").fill("Steve Jobs")

    t.check("the choice is offered", t.page.locator("#detail").count(), 1)
    t.check("and taken by default", t.page.locator("#detail").is_checked(), True)

    # ---- a word that has been looked up ----------------------------------
    t.page.locator("#f-Back").fill("chagrin")
    t.page.wait_for_timeout(3200)
    brain = t.page.locator("#brain").inner_text()
    t.truthy("the word is explained while it is typed", "embarrassment" in brain)
    # Otherwise the fold is invisible until a card has been made and reviewed,
    # which is the wrong moment to find out about it.
    t.truthy("and the panel says where it is about to end up",
             "on the back of the card" in brain)

    t.page.locator("#f-Front").fill("분함")
    # Typed and sent immediately, with the field still focused. Tapping Add
    # card blurs it on the way, and reading the sentence then is a request for
    # a card that has already gone - and a panel growing underneath the button
    # at the moment it is being tapped, which is how a tap lands on the wrong
    # thing.
    read.clear()
    t.page.locator("#f-Example").fill("Much to my chagrin, I had left it at home.")
    t.page.locator("#go").click()
    t.page.wait_for_timeout(1500)

    t.check("the card was sent", len(sent), 1)
    t.check("and sending it did not pay to read the sentence on the way out",
            len(read), 0)
    fields = json.loads(sent[0]["inputs"]["fields"])
    back = fields["Back"]

    t.truthy("the meaning rides along on the back of the card",
             "embarrassment or annoyance at having failed" in back)
    t.truthy("and is folded rather than sitting there open", "<details" in back)
    t.truthy("with something to tap", "<summary" in back and "in detail" in back)

    # The whole reason it is a <details> and not more text. A definition that is
    # simply there is a definition you read instead of remembering.
    t.check("the word is still the first thing on the card",
            BLOCK_TAG.split(back)[0].strip(), "chagrin")

    # It is on the line above. Restating it inside costs a phone screen.
    inside = re.sub(r"<[^>]+>", "", back.split("</summary>", 1)[1]).strip()
    t.truthy("and the fold opens on the meaning rather than on the word again",
             inside.startswith("a feeling of embarrassment"))

    # The pin. If this fails, the page and anki/fold_meaning.py have stopped
    # writing the same block, and cards folded either way no longer match.
    t.check("and it is the block the workflow writes for older cards",
            back, "chagrin" + FOLDED["html"])

    t.truthy("the Korean comes with it", "분함" in back)
    t.truthy("and the nuance", "your own failure" in back)
    t.truthy("and what it goes with", "much to my chagrin" in back)
    t.truthy("and what it is not", "chagrined" in back)
    t.truthy("and the hook", "sha-GRIN" in back)
    t.note("what went on the card", f"{len(back)} characters, {back.count('<div')} lines")

    # The note type is the user's, and changing it is the schema change this
    # project refuses, so the fold styles itself. Which means it has to
    # survive Anki's night mode: a grey that reads well on a white card is
    # invisible on a black one, so it names no colour at all.
    t.check("and nothing in it assumes a light card",
            re.findall(r"(?<!current)color:\s*[^;\"]+", back), [])

    # ---- the way back out -------------------------------------------------
    sent.clear()
    t.page.locator("#detail").uncheck()
    t.page.locator("#f-Back").fill("halcyon")
    t.page.wait_for_timeout(3200)
    t.truthy("unticked, the panel stops claiming it goes on the card",
             "on the back of the card" not in t.page.locator("#brain").inner_text())
    t.page.locator("#f-Example").fill("She talks about her halcyon days at school.")
    t.page.locator("#go").click()
    t.page.wait_for_timeout(1500)

    t.check("a second card was sent", len(sent), 1)
    t.check("unticked, the back is exactly what was typed",
            json.loads(sent[0]["inputs"]["fields"])["Back"], "halcyon")

    # ---- a word nobody looked up -----------------------------------------
    sent.clear()
    t.page.locator("#detail").check()
    t.page.locator("#f-Back").fill("zzzz")     # too unlike a word to be asked about
    t.page.locator("#f-Example").fill("Nothing was ever looked up for this one.")
    t.page.wait_for_timeout(3200)
    t.page.locator("#go").click()
    t.page.wait_for_timeout(1500)

    t.check("a word with no explanation gets no fold",
            json.loads(sent[0]["inputs"]["fields"])["Back"], "zzzz")

    _the_sentence_cache_does_not_grow_forever(t)

    t.page.unroute("https://api.anthropic.com/**")
    t.page.unroute("https://api.github.com/**")


def _the_sentence_cache_does_not_grow_forever(t):
    """The explanation cache is keyed by word, so it is capped by how much
    vocabulary one person has. This one is keyed by the sentence, and there is
    no end to sentences.

    What a full quota eventually breaks is not the cache - a failed write there
    loses an answer nobody was waiting for - but everything sharing the origin,
    which on this page is the settings and the locked GitHub token.
    """
    keep = t.page.evaluate("""() => {
      const found = document.documentElement.innerHTML.match(/const SENTENCE_KEEP = (\d+)/);
      return found ? Number(found[1]) : 0;
    }""")
    t.check("there is a limit at all", keep > 0, True)

    # Written straight in rather than typed: a hundred real sentences is forty
    # minutes of test, and what is being checked is the pruning, not the typing.
    left = t.page.evaluate("""(keep) => {
      const KEY = "said:";
      for (let i = 0; i < keep * 2; i += 1) {
        localStorage.setItem(KEY + "sentence number " + i,
          JSON.stringify({ at: Date.now() - (keep * 2 - i) * 1000, info: { verdict: "natural" } }));
      }
      // One more through the page's own writer, which is what prunes.
      const box = document.getElementById("f-Example");
      box.value = "One more sentence, written last of all.";
      box.dispatchEvent(new Event("input", { bubbles: true }));
      return null;
    }""", keep)
    t.page.wait_for_timeout(3000)

    after = t.page.evaluate("""() => {
      const keys = [];
      for (let i = 0; i < localStorage.length; i += 1) {
        const k = localStorage.key(i);
        if (k && k.startsWith("said:")) keys.push(k);
      }
      return keys;
    }""")
    t.check("and it holds", len(after) <= keep, True)
    t.note("how many it kept", f"{len(after)} of {keep * 2 + 1} written, limit {keep}")
    # The newest survive: the oldest go first, which is the only ordering that
    # makes a cache worth having.
    t.truthy("keeping the newest rather than whichever came to hand",
             "said:One more sentence, written last of all." in after)
    t.truthy("and dropping the oldest", "said:sentence number 0" not in after)
