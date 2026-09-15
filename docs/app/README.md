# Lexis

Your deck, off Anki. A review app that runs in the browser, schedules with
FSRS, and keeps everything on your phone.

**Open it at** `https://sengju-han.github.io/Clipboard-stakcer/app/`

Add it to your home screen and it behaves like an installed app — full screen,
its own icon, and it opens with no signal.

## What it does

- **Reviews with FSRS**, the scheduler Anki uses under the hood. Benchmarks on
  500M+ Anki reviews put it 20–30% below SM-2 for the same retention, which is
  a fifth fewer cards for the same memory.
- **Works offline.** The deck lives in IndexedDB, not on a server. Answering a
  card is a local write, so there is no spinner on a train.
- **Keeps your history.** Importing does not reset anything. A card already in
  the app keeps its own scheduling and only has its wording refreshed.
- **Speaks.** Real audio when the media is published, the phone's own voice
  when it is not.
- **Shows the memory hook.** Half this collection writes the answer as the word
  on one line and a hook underneath. They are separated, and the hook appears
  with the answer, never with the clue.

## Anki compatibility, precisely

**In: yes, directly.** *Settings → Import an .apkg from Anki* reads the file
AnkiDroid exports — unzipped, zstd-decompressed and queried in the browser.
Nothing is uploaded. It handles the modern `collection.anki21b` format and the
older `collection.anki21` / `collection.anki2`, and deliberately ignores the
decoy `collection.anki2` that modern exports include so pre-2.1.50 Anki says
"upgrade" instead of crashing — reading that would import one junk card and
report success.

**Out: content yes, scheduling no.** *Export for Anki (CSV)* writes a file Anki
imports with nothing set by hand: decks and tags travel by declared column,
the answer keeps its word, hook and example stacked. Scheduling cannot travel
in a CSV. *Back everything up (JSON)* is the lossless one — every card, its
FSRS state, and the full review log.

**Not compatible:** this does not write `.apkg`, does not sync with AnkiWeb
from the app, and does not use Anki's database format internally. It is not a
drop-in replacement for Anki; it is a different app that reads and writes
Anki's files.

Verified by round trip: 1,177 cards exported as CSV and imported into a fresh
Anki collection — 1177 new, 0 duplicates, 0 empty, decks preserved.

## Your cards on both devices

*Settings → Sync.* A token, an owner and a repository, on each device. Cards
and reviews are kept as one JSON file in your own private repository — no
account to make, no server to pay for, and the data stays somewhere you already
control.

Merging is per card, by when each was actually changed. Last-write-wins would
keep whichever device pushed most recently, which can easily be the one that
answered *first*; every card carries its own modification time instead, so a
review answered on the tablet at nine beats one answered on the phone at eight
whichever order they sync in. Reviews are events rather than state, so the two
logs are unioned and the history ends up complete on both devices.

Nothing is deleted by a sync, and the write is guarded by the file's sha — if
the other device wrote while this one was merging, GitHub refuses and the app
says to try again rather than overwriting their work.

The backend sits behind a two-method interface, so when this outgrows a
repository only the adapter changes. The merge is the hard part and it is not
GitHub-specific.

## Progress

*Progress* on the home screen answers the two questions a learner actually has.

**How much work is coming** — one bar per day for thirty days, counted from each
card's own due date, so it is the same number the review screen will hand you on
the day. Anything already owed is red on today rather than hidden in the total.

**Whether it is sticking** — retention is the share of cards that were *already
learned*, came back, and still were. Cards in learning are deliberately left
out: counting them drags the number down on exactly the days you study hardest,
which is backwards.

Also a streak, thirty days of what you have actually answered, and the deck
split into mature (three weeks or more between reviews), young, learning and
new — the same 21-day line Anki draws, so the word means the same thing to
anyone arriving from there.

## Browsing and fixing

*Browse deck* searches word, hook, clue and example at once — including Korean,
so the clue you half-remember finds the card. Each result shows its deck and
what the scheduler thinks: new, due now, or how far off.

Tap one to edit or delete it. **Editing the wording leaves scheduling alone** —
scheduling is evidence about how well a word is known, and fixing a typo is not
evidence about anything. Deleting takes the card's history with it and says so
before it does.

This matters more than it sounds: the audit found real typos already in this
collection (`pradoxcial`, `succesfully`, `threatend`). Before this, an app you
could add to but never correct would have reviewed each of them a hundred times.

## Adding cards

*+ Add a card* on the home screen. Word, memory hook, clue, example, deck. It
saves straight to this browser and is due immediately, so it works with no
signal. A word already in the deck is refused rather than quietly duplicated.

## Getting your cards in

Run **Build the review deck** under Actions. It pulls the collection from
AnkiWeb, converts it, and commits `docs/deck/deck.json`. The app picks it up
the next time it is opened, or immediately from *Settings → Check for a newer
deck*.

Scheduling is carried across rather than restarted. Where Anki holds FSRS
memory state it is used directly; where it holds only the older interval and
ease numbers, `anki/build_deck.py` converts them — the interval is the best
single estimate of stability, and the ease maps onto difficulty. That
conversion is approximate and the deck file records which cards it applied to.

## Settings worth knowing

| | |
| --- | --- |
| **Target retention** | How much you want to remember. 0.9 is the standard trade; higher means more reviews for less forgetting. |
| **New cards a day** | Rationed, and the count survives a reload — otherwise closing the app would be a way to get unlimited new cards. |
| **Audio location** | Where the mp3 files are served from. Leave it blank and your phone's voice reads the sentence instead. |

## How it is built

| File | Does |
| --- | --- |
| `app.js` | The review loop and every screen. |
| `review.js` | FSRS wiring, the queue, and what each grade button will do. |
| `store.js` | IndexedDB: cards, an append-only review log, and settings. |
| `apkg.js` | Reads an Anki `.apkg` in the browser. Loaded only when you import one. |
| `stats.js` | Forecast, retention, streak and maturity, plus the inline-SVG bars. |
| `sync.js` | The merge, and the GitHub adapter behind it. |
| `vendor/ts-fsrs.mjs` | The scheduler. MIT, from Open Spaced Repetition, vendored rather than fetched from a CDN so it works offline. |
| `vendor/fflate.mjs` · `vendor/fzstd.mjs` · `vendor/sql-wasm.*` | Zip, zstd and SQLite, all MIT. Fetched on demand, never pre-cached — the SQLite engine alone is most of a megabyte and most sessions never import a file. |
| `sw.js` | Caches the app's own files. Never the deck — a stale deck cached behind the app's back is how you end up reviewing yesterday's cards forever. |

The review log is append-only and never rewritten. It is the record that lets a
schedule be rebuilt from scratch if card state is ever lost, or if a better
scheduler arrives later.

## What it does not do yet

No accounts, no sync between devices, no capture from video, no speaking
practice, and no `.apkg` writing. Those are the later phases.
