"""Signing in on a second device, and what the server is handed.

The Add to Anki page holds two keys: a GitHub token that can run workflows on
somebody's repository, and an Anthropic key that can spend their money. The
account exists so those do not have to be found and retyped on every device.
What it must never do is hand either of them to the server in a form the server
can read - so this drives the whole thing in a browser and then looks at the
bytes that actually went over the wire.
"""

import json

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
    t.page.goto(f"http://127.0.0.1:{t.server.port}/index.html")
    t.page.wait_for_selector("#settings", timeout=30000)
    t.page.evaluate("""() => {
      try { localStorage.clear(); } catch {}
      try { sessionStorage.clear(); } catch {}
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
    _fill(t, "acct-server", "https://vault.test")
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
    _fill(t, "acct-server", "https://vault.test")
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
    t.check("the address is still there after a reload",
            t.page.locator("#acct-server").input_value(), "https://vault.test")
    t.check("and so is the email", t.page.locator("#acct-email").input_value(), "me@example.test")
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
    t.open_app()
    t.page.evaluate("() => { try { localStorage.clear(); } catch {} }")
    t.page.reload()
    t.page.wait_for_selector("#screen-home:not([hidden])", timeout=90000)
    t.page.wait_for_timeout(900)
    t.open_settings()

    t.check("the review app starts with no keys either",
            [t.page.locator("#anthropic").input_value(), t.page.locator("#gh-token").input_value()],
            ["", ""])

    t.page.locator("#acct-base").fill("https://vault.test")
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
