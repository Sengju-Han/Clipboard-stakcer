"""With the server genuinely stopped.

This is the suite that needs the runner to own the server. Playwright's own
offline flag does not reach service-worker fetches: an earlier version of this
test set it, watched every screen open, and reported everything working. The
server has to actually be turned off.
"""


def run(t):
    t.open_app(wait_for_worker=True)
    t.page.wait_for_timeout(1200)

    cached = t.page.evaluate("""async () => {
      const names = await caches.keys();
      const c = await caches.open(names[0]);
      return (await c.keys()).map(r => r.url.split('/').pop()).sort();
    }""")
    t.note("what the worker keeps", len(cached))

    # cache.add swallows a 404, so a path that has gone stale would otherwise
    # never be noticed. Check the worker's own list against what it got.
    listed = t.page.evaluate("""async () => {
      const src = await (await fetch('sw.js')).text();
      const from = src.indexOf('const SHELL = [');
      const body = src.slice(from, src.indexOf('];', from));
      return [...body.matchAll(/"([^"]+)"/g)].map(m => m[1]);
    }""")
    missing = [u for u in listed if u != "./" and u.split("/")[-1] not in cached]
    t.check("every file the worker lists is a file it has", missing, [])

    t.server.stop()
    try:
        t.page.reload()
        t.page.wait_for_selector("#screen-home:not([hidden])", timeout=60000)
        t.page.wait_for_timeout(1500)
        t.check("the app opens with nothing to reach", t.screen(), "screen-home")
        t.truthy("and knows what is due", int(t.page.locator("#n-due").inner_text()) >= 0)

        for name, button, screen in [("watch", "#watch-btn", "screen-watch"),
                                     ("speak", "#talk-btn", "screen-talk"),
                                     ("progress", "#stats-btn", "screen-stats")]:
            t.page.locator(button).click(timeout=15000)
            t.page.wait_for_timeout(2500)
            t.check(f"{name} opens offline", t.screen(), screen)
            for close in ["#watch-close", "#talk-close", "#stats-close"]:
                if t.page.locator(close).is_visible():
                    t.page.locator(close).click()
                    t.page.wait_for_timeout(500)
                    break

        t.page.locator("#start-btn").click()
        t.page.wait_for_timeout(800)
        t.page.locator("#reveal-btn").click()
        t.page.wait_for_timeout(300)
        word = t.page.locator("#card-word").inner_text()
        t.page.locator(".grade.again").click()
        t.page.wait_for_timeout(2500)
        panel = t.page.locator("#brain").inner_text()
        t.check("the card stays up after an Again", t.page.locator("#card-word").inner_text(), word)
        t.truthy("and the panel says something a person can act on, not a module error",
                 "dynamically imported module" not in panel)
        t.note("it said", panel[:70])

        t.page.locator("#next-btn").click()
        t.page.wait_for_timeout(700)
        t.page.locator("#reveal-btn").click()
        t.page.wait_for_timeout(300)
        t.page.locator(".grade.good").click()
        t.page.wait_for_timeout(700)
        t.truthy("and answering offline is still written down", len(t.log()) >= 1)
    finally:
        t.server.start()
