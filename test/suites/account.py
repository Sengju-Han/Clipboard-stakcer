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
    t.page.evaluate("() => { try { localStorage.clear(); } catch {} }")
    t.page.reload()
    t.page.wait_for_selector("#settings", timeout=30000)


def _fill(t, field, value):
    t.page.locator(f"#{field}").fill(value)
    t.page.locator(f"#{field}").dispatch_event("change")


def run(t):
    state = _fake_server(t)
    _open(t)

    # ---- first device ----------------------------------------------------
    t.page.locator("#settings").evaluate("el => el.open = true")
    _fill(t, "token", TOKEN)
    _fill(t, "anthropic", KEY)
    _fill(t, "owner", "Sengju-Han")
    _fill(t, "repo", "Clipboard-stakcer")

    t.page.locator("#account").evaluate("el => el.open = true")
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
    t.check("what it got is a versioned, salted box", blob.startswith("v1."), True)
    t.check("with a salt, a nonce and the ciphertext", len(blob.split(".")), 4)

    # A second write must not reuse the nonce, or two versions of the same
    # settings leak their difference.
    _fill(t, "ref", "main")
    t.page.wait_for_timeout(1800)
    t.truthy("a second save is a different box", len(state["seen"]) >= 2
             and state["seen"][-1] != state["seen"][0])

    # ---- second device ---------------------------------------------------
    # Same page, nothing remembered: exactly what a new phone sees.
    _open(t)
    t.page.locator("#settings").evaluate("el => el.open = true")
    t.check("the new device starts with no token", t.page.locator("#token").input_value(), "")

    t.page.locator("#account").evaluate("el => el.open = true")
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
