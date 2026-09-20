"""Connecting Google Drive, which now happens entirely in the browser.

It used to be two runs of a workflow: one to print a link, one to swap the code
for a refresh token and write it into the repository's secrets. Writing a secret
needs a fine-grained personal access token, and making one of those on a phone
is the most awkward step in the whole project — which is why the feature sat
unused for a day with the export still going to artifacts.

Google's token endpoint allows cross-origin requests from this page's origin, so
the swap happens here instead. Verified against Google directly: the same
request this page makes comes back `invalid_client` with the CORS header set,
which is a semantic refusal rather than a rejected shape.

Nothing reaches a log, which matters because this repository is public.
"""

import json

CLIENT = "1234-abc.apps.googleusercontent.com"
SECRET = "GOCSPX-not-a-real-secret"
REFRESH = "1//0g-a-refresh-token-that-lasts"


def _open(t, query=""):
    t.page.goto(f"http://127.0.0.1:{t.server.port}/connected.html{query}")
    # The script fills the redirect box the moment it runs, whichever step the
    # page decided to show, so that is the thing to wait for.
    t.page.wait_for_function(
        """() => (document.getElementById("redir")?.textContent || "").includes("connected.html")""",
        timeout=30000)
    t.page.wait_for_timeout(200)


def run(t):
    # ---- nothing in the address: it asks for the client id ----------------
    _open(t)
    t.check("it opens on the approve step", t.page.locator("#start").is_visible(), True)
    t.check("and says what to set up first", t.page.locator("#explain").is_visible(), True)
    t.truthy("including the exact redirect to paste into Google",
             "/connected.html" in t.page.locator("#redir").inner_text())
    # One character out and Google answers redirect_uri_mismatch and never
    # comes back here, so this is the one string that must not be retyped.
    t.check("with a button to copy it rather than type it",
            t.page.locator("#copy-redir").is_visible(), True)
    # Publishing is the difference between a connection that lasts and one that
    # stops after seven days, so the page has to say so.
    said = t.page.locator("#explain").inner_text().lower()
    t.truthy("and that the app has to be published", "publish app" in said)
    t.truthy("and why", "seven days" in said)

    # An empty client id is refused rather than sending you to Google with none.
    t.page.locator("#go").click()
    t.page.wait_for_timeout(300)
    t.truthy("an empty client id says so",
             "client ID" in t.page.locator("#start-say").inner_text())

    # ---- the link it builds ----------------------------------------------
    sent = {}
    t.page.route("https://accounts.google.com/**", lambda route: (
        sent.update({"url": route.request.url}),
        route.fulfill(status=200, content_type="text/html", body="<p>google</p>")))
    t.page.locator("#cid").fill(CLIENT)
    t.page.locator("#go").click()
    t.page.wait_for_timeout(900)

    t.truthy("approving goes to Google", "accounts.google.com" in sent.get("url", ""))
    url = sent.get("url", "")
    t.truthy("asking for the narrow scope", "drive.file" in url)
    t.truthy("and for a refresh token at all", "access_type=offline" in url)
    # Without prompt=consent a second attempt returns an access token and no
    # refresh token, and there is nothing to store.
    t.truthy("and for consent every time", "prompt=consent" in url)
    t.truthy("with the client id it was given", CLIENT.split(".")[0] in url)

    # ---- Google sends them back with a code ------------------------------
    _open(t, "?code=a-one-time-code")
    t.check("a code opens the swap step", t.page.locator("#swap").is_visible(), True)
    t.check("and the setup notes are out of the way",
            t.page.locator("#explain").is_visible(), False)
    t.truthy("it says the secret stays in this browser",
             "never leaves this tab" in t.page.locator("#swap").inner_text())

    # The real exchange, answered the way Google answers it.
    asked = {}

    def token(route):
        asked["body"] = route.request.post_data
        route.fulfill(status=200, content_type="application/json", body=json.dumps(
            {"access_token": "an-access-token", "refresh_token": REFRESH,
             "expires_in": 3599, "scope": "https://www.googleapis.com/auth/drive.file"}))

    t.page.route("https://oauth2.googleapis.com/token", token)
    t.page.locator("#sec").fill(SECRET)
    t.page.locator("#swapgo").click()
    t.page.wait_for_timeout(1200)

    t.truthy("the code is swapped with Google", "a-one-time-code" in asked.get("body", ""))
    t.truthy("using the client id it remembered", CLIENT in asked.get("body", ""))
    t.check("the refresh token is shown", t.page.locator("#rt").inner_text(), REFRESH)
    t.truthy("and named as the secret to make",
             "GOOGLE_REFRESH_TOKEN" in t.page.locator("#done").inner_text())
    # Pasting three secrets and hoping is where this was always going to end.
    # There is a thirty-second job that answers it, so point at it from here.
    t.truthy("and it says how to find out whether it worked",
             "Check Google Drive" in t.page.locator("#then").inner_text())
    t.truthy("with a link straight to it",
             "google-check.yml" in t.page.locator("#then a").get_attribute("href"))
    t.check("the secret box is emptied once it has been used",
            t.page.locator("#sec").input_value(), "")
    # A code works once. Left on the address, a reload - or Android restoring
    # the tab tomorrow - would come back to the swap step holding a dead code.
    t.check("and the used code is taken off the address",
            t.page.evaluate("() => location.search"), "")

    # Nothing about any of this is written down.
    kept = t.page.evaluate("""() => {
      const out = {};
      for (let i = 0; i < localStorage.length; i += 1) {
        const k = localStorage.key(i);
        out[k] = localStorage.getItem(k);
      }
      return out;
    }""")
    t.note("what the browser keeps", ", ".join(kept) or "nothing")
    t.check("the client secret is not written down anywhere",
            any(SECRET in v for v in kept.values()), False)
    t.check("nor the refresh token",
            any(REFRESH in v for v in kept.values()), False)
    t.truthy("only the client id, which is not a secret",
             any(v == CLIENT for v in kept.values()))

    _refusals(t)


def _refusals(t):
    """What it says when Google says no, which is where this gets abandoned."""
    _open(t, "?error=access_denied")
    t.check("a dismissed consent screen goes back to the start",
            t.page.locator("#start").is_visible(), True)
    said = t.page.locator("#start-say").inner_text()
    t.truthy("and says nothing was changed", "Nothing was changed" in said)
    t.check("and the client id is still there to try again with",
            t.page.locator("#cid").input_value(), CLIENT)

    # A code that has already been spent. Google answers with no refresh token,
    # which is the one failure that looks like success if it is not checked.
    _open(t, "?code=already-used")
    t.page.route("https://oauth2.googleapis.com/token", lambda route: route.fulfill(
        status=200, content_type="application/json",
        body=json.dumps({"access_token": "one", "expires_in": 3599})))
    t.page.locator("#sec").fill(SECRET)
    t.page.locator("#swapgo").click()
    t.page.wait_for_timeout(1000)
    t.truthy("a spent code says so rather than showing an empty token",
             "already been used" in t.page.locator("#swap-say").inner_text())
    t.check("and nothing is offered to copy", t.page.locator("#done").is_visible(), False)

    # "Start again from the top" is not an instruction anybody can follow here
    # unless there is something to press: the code is on the address, and
    # getting rid of it by hand means editing an address bar on a phone.
    t.check("and there is a way back that is not the address bar",
            t.page.locator("#restart").is_visible(), True)
    t.truthy("which says the client id is still there",
             "remembered" in t.page.locator("#restart").inner_text())
    t.page.locator("#restart-go").click()
    t.page.wait_for_function(
        """() => (document.getElementById("redir")?.textContent || "").includes("connected.html")""",
        timeout=30000)
    t.page.wait_for_timeout(300)
    t.check("which leaves the dead code behind",
            t.page.evaluate("() => location.search"), "")
    t.check("and opens on the approve step again",
            t.page.locator("#start").is_visible(), True)
    t.check("with the client id still remembered",
            t.page.locator("#cid").input_value(), CLIENT)

    # And an outright refusal from Google, verbatim: its wording is better than
    # anything this page could invent.
    _open(t, "?code=bad")
    t.page.route("https://oauth2.googleapis.com/token", lambda route: route.fulfill(
        status=401, content_type="application/json", body=json.dumps(
            {"error": "invalid_client", "error_description": "The OAuth client was not found."})))
    t.page.locator("#sec").fill(SECRET)
    t.page.locator("#swapgo").click()
    t.page.wait_for_timeout(1000)
    t.truthy("Google's own reason is shown",
             "OAuth client was not found" in t.page.locator("#swap-say").inner_text())
    t.check("and you can try again", t.page.locator("#swapgo").is_disabled(), False)
    t.check("and get out of it without editing the address",
            t.page.locator("#restart").is_visible(), True)

    # Arriving with a code in a browser that has never seen the client id: the
    # link opened on a different phone, or the tab restored after a clear-out.
    # There is nothing to swap with, and saying so beats a failed exchange.
    t.page.evaluate("() => localStorage.clear()")
    _open(t, "?code=orphan")
    t.page.locator("#sec").fill(SECRET)
    t.page.locator("#swapgo").click()
    t.page.wait_for_timeout(600)
    said = t.page.locator("#swap-say").inner_text()
    t.truthy("a code with no client id behind it says what is missing",
             "forgotten the client ID" in said)
    t.truthy("and that there is something to go and fetch",
             "Google console" in t.page.locator("#restart").inner_text())
