# The tests

They drive the real app in a real browser against the real deck in `docs/`.
There is no mock of the scheduler, the database or the service worker, because
every bug worth catching in this project has been in how those three behave
together rather than in any one of them.

```
pip install -r test/requirements.txt
playwright install chromium      # not needed where one is already at /opt/pw-browsers
python3 test/run.py              # everything
python3 test/run.py review undo  # just those
```

Each suite gets a fresh browser profile, so no suite can depend on another
having run, and the runner owns the HTTP server — which is what makes the
offline suite possible. A test that wants nothing to be reachable stops the
server and means it. Playwright's own offline flag does not reach
service-worker fetches: an earlier version of that test set it, watched every
screen open, and reported everything working.

Fixtures are generated rather than committed: a 600-line subtitle file, a wav
with a tone in the first six seconds, and a small `.apkg` written by hand at
schema 11 — by hand on purpose, so reading it checks the reader against the
format rather than against this project's own writer.

One file is committed, and deliberately: `folded-meaning.json`. The block of
markup that folds a word's meaning onto the back of a card is written twice,
once in JavaScript by the Add to Anki page and once in Python by the fold
workflow, and two implementations of one piece of markup drift silently. That
file is the block, and three separate tests assert their side produces exactly
it — so the day one of them changes, the other has to be looked at.

## What each one is for

| | |
|---|---|
| `review` | a clue, an answer, a grade, and whether any of it survives a reload |
| `undo` | taking back an answer, including the copy an Again requeued |
| `browse` | find, edit without disturbing scheduling, delete, and the tombstone that stops the other phone handing it back |
| `add_export` | adding a card, refusing a duplicate, and all three ways out |
| `progress` | the charts, the streak, and what retention deliberately leaves out |
| `leeches` | the cards that keep winning, and the two ways out of one |
| `schedule_honesty` | a deck that arrived without its review history has to say so |
| `watch` | subtitles read, words marked against the deck, one mined, one dismissed |
| `explain` | the explanation after an Again, and that an explained word costs nothing |
| `speak` | a conversation from this week's words, with a scripted partner |
| `apkg_roundtrip` | somebody else's file read, and one written back, keeping what this app does not use |
| `real_server` | the page against the real Worker, with nothing standing between them |
| `study_ahead` | an evening with nothing due is not a dead end |
| `timezones` | a card due today is due today, wherever you are |
| `markup` | a word containing a `<` is a word, not markup |
| `no_database` | a browser that will not store anything says so, rather than spinning |
| `account` | a second device, and what the server is handed |
| `offline` | all of it with the server stopped |

## What is stood in for, and what it cost

Two things, and the difference between them matters.

**Claude**, in every suite that reaches it. The tests must not spend money and
must not depend on what a model says today.

**The server**, in `account` — and that one is how a real bug got through.
Playwright's `page.route` answers a request *before* the browser's own CORS
check runs, so a reply every browser would refuse looks fine to the suite.
`Access-Control-Allow-Methods` said `GET, POST, OPTIONS` while the vault was
written with `PUT`; every browser refused the request before it left and
reported `Failed to fetch`, which is indistinguishable from the server being
down. Nothing could be saved for a week and every test passed throughout.

So `real_server` exists: the real Worker on a real port through Miniflare, the
page on a different port so the origins differ the way they do in life, and no
stub. It skips where node or Miniflare is not installed, because these tests
have to stay runnable on a machine that has never touched the server — but
`REQUIRE_REAL_SERVER=1` turns that skip into a failure, and CI sets it. A
protection that can quietly stop running is not a protection.

The server's own tests are separate and do not need a browser:
`cd server && npm install && npm test`.
