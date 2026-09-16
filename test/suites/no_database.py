"""A browser that will not let the app store anything has to say so.

Everything here is reads and writes to IndexedDB, so there is no version of
this app that works without one — Firefox's private windows and some Android
browsers refuse it outright. The failure was already caught and explained
carefully, and the explanation was rendered inside a block that is hidden when
there is nothing to click, so it was invisible exactly when it mattered.
"""


def run(t):
    page = t.browser.new_context(viewport={"width": 390, "height": 844}).new_page()
    try:
        page.add_init_script("""
          // What a private window does, without needing one.
          Object.defineProperty(window, 'indexedDB', {
            configurable: true,
            get() { const e = new Error('denied'); e.name = 'SecurityError'; throw e; },
          });
        """)
        page.goto(t.server.app_url)
        page.wait_for_selector("#screen-boot:not([hidden])", timeout=60000)
        page.wait_for_timeout(1500)

        t.check("it stays on the boot screen",
                page.evaluate("() => [...document.querySelectorAll('.screen')].find(s => !s.hidden).id"),
                "screen-boot")
        heading = page.locator("#boot-note").inner_text()
        detail = page.locator("#boot-detail").inner_text()
        t.note("it says", heading)
        t.check("the heading names what happened, not a missing deck",
                "store anything" in heading, True)
        t.check("the explanation is actually on screen",
                page.locator("#boot-detail").is_visible(), True)
        t.truthy("and names the likely cause", "Private browsing" in detail)
        t.truthy("and what would work instead", "normal window" in detail)
        t.check("nothing is offered that cannot work",
                page.locator("#boot-actions").is_hidden(), True)
        t.check("and the spinner stops", page.locator("#boot-spin").is_hidden(), True)
    finally:
        page.close()
