#!/usr/bin/env python3
"""Run the browser tests against the app in docs/.

    python3 test/run.py                 everything
    python3 test/run.py review undo     just those

Needs Playwright and a Chromium. On the machine this was written for the
browser is already at /opt/pw-browsers; elsewhere, `playwright install
chromium` puts one where Playwright can find it.
"""

import argparse
import importlib
import sys
import tempfile
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from harness import Server, Test, chromium_path, free_port   # noqa: E402
import fixtures                                              # noqa: E402

SUITES = [
    "review",            # the loop, and that an answer survives a reload
    "scheduling",        # one card through ten reviews and a failure
    "undo",              # taking an answer back
    "browse",            # find, edit, delete, and the tombstone it leaves
    "add_export",        # adding a card, and every way out of the app
    "account",           # a second device, and what the server is handed
    "progress",          # the charts and what they count
    "leeches",           # the cards that keep winning
    "schedule_honesty",  # a deck that arrived without its history says so
    "watch",             # subtitles, marking, mining
    "explain",           # why a word will not stick
    "speak",             # a conversation out of this week's words
    "apkg_roundtrip",    # somebody else's file, read and written
    "real_server",       # the page and the server, no stub between them
    "full_storage",      # a phone with no room left, answering a card
    "study_ahead",       # an evening with nothing due is not a dead end
    "timezones",         # a card due today is due today, wherever you are
    "markup",            # a word containing a < is a word, not markup
    "no_database",       # a browser that will not let it store anything
    "offline",           # with the server genuinely stopped
]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("suites", nargs="*", default=[])
    args = parser.parse_args()

    chosen = args.suites or SUITES
    unknown = [s for s in chosen if s not in SUITES]
    if unknown:
        print(f"No such suite: {', '.join(unknown)}\nThere is: {', '.join(SUITES)}")
        return 2

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Playwright is not installed. Try: pip install -r test/requirements.txt")
        return 2

    browser_path = chromium_path()

    work = Path(tempfile.mkdtemp(prefix="lexis-test-"))
    built = fixtures.build_all(work / "fixtures")
    server = Server(free_port())
    server.start()

    passed = failed = broke = 0
    trouble = []

    with sync_playwright() as pw:
        launch = {"args": ["--no-sandbox", "--autoplay-policy=no-user-gesture-required"]}
        if browser_path:
            launch["executable_path"] = browser_path
        try:
            browser = pw.chromium.launch(**launch)
        except Exception as why:
            print(f"Could not start Chromium: {why}\nTry: playwright install chromium")
            server.stop()
            return 2
        for name in chosen:
            print(f"\n— {name} —")
            suite = importlib.import_module(f"suites.{name}")
            # A fresh profile per suite: no shared IndexedDB, no shared
            # service worker, no suite able to depend on another having run.
            context = browser.new_context(viewport={"width": 390, "height": 844},
                                          accept_downloads=True)
            page = context.new_page()
            t = Test(name, page, server, built, browser)
            try:
                suite.run(t)
            except Exception:
                broke += 1
                trouble.append(f"{name} (raised)")
                print(f"    BROKE {name}")
                traceback.print_exc()
            finally:
                server.start()          # a suite may have stopped it on purpose
                if t.page_errors:
                    print(f"    page errors: {t.page_errors[:2]}")
                    failed += 1
                    trouble.append(f"{name} (page error)")
                passed += t.passed
                failed += len(t.failed)
                trouble += [f"{name}: {w}" for w in t.failed]
                context.close()
        browser.close()

    server.stop()

    print(f"\n{passed} passed, {failed} failed"
          + (f", {broke} could not run" if broke else ""))
    if trouble:
        print("\nWhat went wrong:")
        for line in trouble:
            print(f"  {line}")
    return 1 if (failed or broke) else 0


if __name__ == "__main__":
    sys.exit(main())
