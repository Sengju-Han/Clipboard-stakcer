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
| `vendor/ts-fsrs.mjs` | The scheduler. MIT, from Open Spaced Repetition, vendored rather than fetched from a CDN so it works offline. |
| `sw.js` | Caches the app's own files. Never the deck — a stale deck cached behind the app's back is how you end up reviewing yesterday's cards forever. |

The review log is append-only and never rewritten. It is the record that lets a
schedule be rebuilt from scratch if card state is ever lost, or if a better
scheduler arrives later.

## What it does not do yet

No accounts, no sync between devices, no capture from video, no speaking
practice. Those are the later phases. This one exists so you can stop opening
AnkiDroid.
