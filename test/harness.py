"""What every suite needs, so that no suite has to arrange it.

A suite is a module with `run(t)`. It gets a fresh browser profile — which
means a fresh IndexedDB, a fresh localStorage and no service worker — and the
app served from the repository's own `docs/` directory. It asserts with
`t.check(what, got, want)` and says things with `t.note(...)`.

The runner owns the server, which is what makes the offline suite possible: a
test that wants nothing to be reachable can stop it and mean it. Playwright's
own offline flag does not reach service-worker fetches and reports everything
working, which is worse than no test at all.
"""

import http.server
import socket
import socketserver
import subprocess
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


class Server:
    """The app, served from docs/, and stoppable on purpose."""

    def __init__(self, port):
        self.port = port
        self.httpd = None
        self.thread = None

    def start(self):
        if self.httpd:
            return
        handler = lambda *a, **kw: Quiet(*a, directory=str(DOCS), **kw)  # noqa: E731
        socketserver.TCPServer.allow_reuse_address = True
        self.httpd = socketserver.TCPServer(("127.0.0.1", self.port), handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def stop(self):
        if not self.httpd:
            return
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)
        self.httpd = None
        self.thread = None

    @property
    def app_url(self):
        return f"http://127.0.0.1:{self.port}/app/index.html"


PREINSTALLED = Path("/opt/pw-browsers")


def chromium_path():
    """A browser this machine already has, or None to let Playwright find its own.

    Some environments ship Chromium somewhere Playwright does not look and set
    PLAYWRIGHT_BROWSERS_PATH to point at it; everywhere else, `playwright
    install chromium` puts one exactly where Playwright expects. Returning None
    for the second case is the whole point — an earlier version of this
    hard-coded the first, and the workflow failed in twenty-nine seconds with
    "No Chromium found" on a runner that had just downloaded one.
    """
    if PREINSTALLED.is_dir():
        for candidate in sorted(PREINSTALLED.glob("chromium*/chrome-linux/chrome")):
            if candidate.exists():
                return str(candidate)
    return None


class Test:
    """One suite's view of the world."""

    def __init__(self, name, page, server, fixtures, browser):
        self.name = name
        self.page = page
        self.server = server
        self.fixtures = fixtures
        self.browser = browser
        self.passed = 0
        self.failed = []
        self.page_errors = []
        page.on("pageerror", lambda e: self.page_errors.append(str(e)))

    def check(self, what, got, want):
        if got == want:
            self.passed += 1
            print(f"    ok   {what}")
        else:
            self.failed.append(what)
            print(f"    FAIL {what}\n           got  {got!r}\n           want {want!r}")
        return got == want

    def truthy(self, what, got):
        return self.check(what, bool(got), True)

    def note(self, what, value=""):
        print(f"         {what}{': ' if value != '' else ''}{value}")

    # ---- the things every suite does ------------------------------------

    def open_app(self, wait_for_worker=False):
        self.page.goto(self.server.app_url)
        self.page.wait_for_selector("#screen-home:not([hidden])", timeout=90000)
        if wait_for_worker:
            self.page.wait_for_function(
                "() => navigator.serviceWorker.controller !== null", timeout=30000)
        self.page.wait_for_timeout(900)

    def screen(self):
        return self.page.evaluate(
            "() => { const s = [...document.querySelectorAll('.screen')].find(x => !x.hidden);"
            " return s ? s.id : 'none'; }")

    def open_settings(self):
        """Idempotent: the panel is a <details> that stays open across screens."""
        if not self.page.evaluate("() => document.getElementById('settings').open"):
            self.page.locator("#settings-btn").click()
        self.page.wait_for_timeout(500)

    def cards(self):
        """Every live card, straight out of IndexedDB. Tombstones excluded."""
        return self.page.evaluate("""async () => {
          const db = await new Promise(r => { const q = indexedDB.open('lexis'); q.onsuccess = () => r(q.result); });
          const all = await new Promise(r => {
            const g = db.transaction('cards').objectStore('cards').getAll(); g.onsuccess = () => r(g.result); });
          return all.filter(c => !c.deleted);
        }""")

    def rows(self):
        """Every row, tombstones included."""
        return self.page.evaluate("""async () => {
          const db = await new Promise(r => { const q = indexedDB.open('lexis'); q.onsuccess = () => r(q.result); });
          return await new Promise(r => {
            const g = db.transaction('cards').objectStore('cards').getAll(); g.onsuccess = () => r(g.result); });
        }""")

    def log(self):
        return self.page.evaluate("""async () => {
          const db = await new Promise(r => { const q = indexedDB.open('lexis'); q.onsuccess = () => r(q.result); });
          return await new Promise(r => {
            const g = db.transaction('log').objectStore('log').getAll(); g.onsuccess = () => r(g.result); });
        }""")

    def put_card(self, word, changes):
        """Change one card in place and reload, for arranging a situation."""
        self.page.evaluate("""async ({ word, changes }) => {
          const db = await new Promise(r => { const q = indexedDB.open('lexis'); q.onsuccess = () => r(q.result); });
          const all = await new Promise(r => {
            const g = db.transaction('cards').objectStore('cards').getAll(); g.onsuccess = () => r(g.result); });
          const card = word ? all.find(c => c.word === word) : all[0];
          const next = { ...card, ...changes, fsrs: { ...card.fsrs, ...(changes.fsrs || {}) }, mod: Date.now() };
          await new Promise(r => {
            const p = db.transaction('cards', 'readwrite').objectStore('cards').put(next); p.onsuccess = () => r(); });
          return next.word;
        }""", {"word": word, "changes": changes})
        self.page.reload()
        self.page.wait_for_selector("#screen-home:not([hidden])", timeout=90000)
        self.page.wait_for_timeout(800)

    def set_pref(self, key, value):
        self.page.evaluate("""({ key, value }) => {
          const p = JSON.parse(localStorage.getItem('lexis:prefs') || '{}');
          p[key] = value; localStorage.setItem('lexis:prefs', JSON.stringify(p));
        }""", {"key": key, "value": value})

    def answer(self, rating="good", reveal=True):
        """One card, answered. Returns the word that was on screen."""
        if reveal and self.page.locator("#reveal-btn").is_visible():
            self.page.locator("#reveal-btn").click()
            self.page.wait_for_timeout(250)
        word = self.page.locator("#card-word").inner_text()
        self.page.locator(f".grade.{rating}").click()
        self.page.wait_for_timeout(500)
        # Again holds the card up; moving on is a deliberate tap.
        if self.page.locator("#next-btn").is_visible():
            self.page.locator("#next-btn").click()
            self.page.wait_for_timeout(350)
        return word


def node_available():
    try:
        subprocess.run(["node", "--version"], capture_output=True, check=True, timeout=20)
        return True
    except Exception:
        return False
