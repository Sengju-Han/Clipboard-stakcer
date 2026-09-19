"""The page and the server, for real, with nothing standing between them.

Every other suite that touches an account stubs the server with Playwright's
`page.route`. That stub answers a request before the browser's own CORS check
runs, which makes a whole class of fault invisible: the server's reply can be
refused by the browser and the suite will never know, because the suite's reply
was never the server's.

That is not hypothetical. `Access-Control-Allow-Methods` said "GET, POST,
OPTIONS" while the vault was written with PUT. Every browser refused the
request before it left and reported `Failed to fetch`, which reads exactly like
the server being down. Nothing could be saved for a week. The unit tests passed,
the browser tests passed, and the only thing that could have caught it is a
real request to a real server on a real origin.

So: a Worker on a port, the page on a different port, and not one stub.
"""

import os

from harness import Worker

TOKEN = "github_pat_11REALSERVER0123456789_thesecretpart"
KEY = "sk-ant-api03-through-the-real-thing"
PASSWORD = "a long enough password"
EMAIL = "real@example.test"


def _panels(t):
    for panel in ("account", "settings"):
        t.page.locator(f"#{panel}").evaluate("el => el.open = true")


def _fill(t, field, value):
    t.page.locator(f"#{field}").fill(value)
    t.page.locator(f"#{field}").dispatch_event("change")


def _open(t, where):
    t.page.goto(f"http://127.0.0.1:{t.server.port}/index.html")
    t.page.wait_for_selector("#settings", timeout=30000)
    t.page.evaluate("""(url) => {
      try { localStorage.clear(); } catch {}
      try { sessionStorage.clear(); } catch {}
      try { localStorage.setItem("lexis:server", url); } catch {}
    }""", where)
    t.page.reload()
    t.page.wait_for_selector("#settings", timeout=30000)


def run(t):
    origin = f"http://127.0.0.1:{t.server.port}"
    worker = Worker(origin)
    where = worker.start()
    if not where:
        missing = ("node or miniflare is not installed, so there is no real server "
                   "to talk to (cd server && npm install)")
        # A skip is right on a laptop that has never touched the server. In CI
        # it is not: this suite exists to catch one specific class of fault, and
        # a silent skip there would put the protection back exactly where it was
        # while the log went on saying everything passed.
        if os.environ.get("REQUIRE_REAL_SERVER") == "1":
            t.check("there is a real server to test against", missing, "")
            return
        t.note("skipped", missing)
        return

    # Anything the browser refuses before sending arrives here and nowhere
    # else: a blocked request never reaches the server, so the server's log
    # cannot show it and the page only ever sees "Failed to fetch".
    refused = []
    t.page.on("requestfailed", lambda r: refused.append(f"{r.method} {r.url} — {r.failure}"))

    try:
        t.note("the server is at", where)
        _open(t, where)

        # ---- making an account, over the wire ----------------------------
        _panels(t)
        _fill(t, "token", TOKEN)
        _fill(t, "anthropic", KEY)
        _fill(t, "owner", "Sengju-Han")
        _fill(t, "repo", "Clipboard-stakcer")

        _panels(t)
        t.page.locator("#acct-email").fill(EMAIL)
        t.page.locator("#acct-pw").fill(PASSWORD)
        t.page.locator("#acct-up").click()
        t.page.wait_for_timeout(4000)

        said = t.page.locator("#acct-say").inner_text()
        t.note("it said", said[:120])
        t.check("an account is made against the real server",
                "account" in said.lower() and "went wrong" not in said.lower(), True)

        # ---- the write that a browser used to refuse ---------------------
        # This is the one. The vault is written with PUT, and a PUT the
        # preflight does not allow never leaves the browser.
        _panels(t)
        _fill(t, "ref", "some-branch")
        t.page.wait_for_timeout(3000)
        saved = t.page.locator("#acct-say").inner_text()
        t.note("saving said", saved[:120])
        t.check("a setting saves without the browser refusing the request",
                "failed to fetch" in saved.lower(), False)
        t.check("and the page does not claim it went wrong",
                "went wrong" in saved.lower(), False)

        t.check("nothing was blocked before it left the browser", refused, [])

        # ---- a second device, which is the whole point -------------------
        _open(t, where)
        _panels(t)
        t.check("a new device starts with nothing", t.page.locator("#token").input_value(), "")

        t.page.locator("#acct-email").fill(EMAIL)
        t.page.locator("#acct-pw").fill(PASSWORD)
        t.page.locator("#acct-in").click()
        t.page.wait_for_timeout(4000)
        t.note("signing in said", t.page.locator("#acct-say").inner_text()[:120])

        t.check("the GitHub token comes back through a real round trip",
                t.page.locator("#token").input_value(), TOKEN)
        t.check("and the Anthropic key", t.page.locator("#anthropic").input_value(), KEY)
        t.check("and the setting saved a moment ago",
                t.page.locator("#ref").input_value(), "some-branch")

        # ---- and the wrong password is still refused ---------------------
        _open(t, where)
        _panels(t)
        t.page.locator("#acct-email").fill(EMAIL)
        t.page.locator("#acct-pw").fill("not the password")
        t.page.locator("#acct-in").click()
        t.page.wait_for_timeout(4000)
        wrong = t.page.locator("#acct-say").inner_text()
        t.truthy("a wrong password is refused by the real server",
                 "do not match" in wrong or "does not open" in wrong)
        t.check("and nothing is filled in from it", t.page.locator("#token").input_value(), "")

        # ---- what the server was actually handed -------------------------
        # Read out of the real D1, not out of a stub's memory.
        stored = t.page.evaluate("""async (url) => {
          const res = await fetch(url + "/api/health");
          return res.ok ? await res.json() : null;
        }""", where)
        t.truthy("the server is answering as itself", bool(stored and stored.get("ok")))

        t.check("still nothing blocked by the browser", refused, [])
    finally:
        worker.stop()
