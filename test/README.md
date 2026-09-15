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
| `apkg_roundtrip` | somebody else's file read, and one written back |
| `offline` | all of it with the server stopped |

The server's own tests are separate and do not need a browser:
`cd server && npm install && npm test`.
