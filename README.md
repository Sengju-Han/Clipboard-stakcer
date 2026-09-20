# Lexis

A spaced-repetition app for learning English, built for somebody who has a
phone and no computer. It reads and writes Anki's files, so nothing here is a
place your deck goes and cannot come back from.

**The app: [`docs/app/`](docs/app/) — open it on your phone and add it to the
home screen.** It works offline from then on.

---

## What it does

**Reviews, with FSRS.** The scheduler is ts-fsrs, the same algorithm Anki
switched to, which asks for 20–30% fewer reviews than SM-2 for the same
retention. The whole loop runs out of IndexedDB — answering a card is a local
write, so it stays instant on a train with no signal. Undo puts a mis-tap back
exactly as it was, card and log.

**Anki, both directions.** It opens an `.apkg` straight off your phone — every
card, deck, tag and the scheduling — and writes one back the same way, with the
note ids intact, so importing it into the collection it came from *updates*
those notes rather than doubling them. There is a CSV export for people who
want a spreadsheet and a JSON backup that restores this browser exactly.

**Watch, and take the words out of it.** Open a subtitle file — `.srt`, `.vtt`,
`.ass` — with a video or a podcast alongside it, or paste the transcript
straight out of YouTube's own Show transcript panel. Every word is marked
against your deck, so the screen does not say "here are the subtitles", it says
*here are the four words in this episode you do not have yet*. Tap one and the
card arrives with the sentence already attached. There is a filter for lines
with exactly one unknown word in them, which is the thing the apps in this
space charge for.

**And in the voice that said it.** Mining a line can capture the two seconds of
audio where somebody actually said it — not a synthetic voice reading the
sentence, the scene. It rides along into the `.apkg` too, so it plays in Anki
on the other side. This is the one feature the desktop mining tools are really
for, and it has never worked on a phone because it needed an add-on.

**Speak it.** A conversation built out of the six words you are in the middle
of learning — reachable, not yet safe. Your phone listens, Claude answers, your
phone reads the reply out. A word only counts as used when you actually said
it, not when Claude thinks you did.

**Why a word will not stick.** After an Again, the card stays up and explains
itself: not the definition, which is already on the card, but when a native
speaker reaches for it, what it travels with, and the near-identical word you
were probably confusing it with. Words already explained cost nothing — the
answers live in this repository.

**Both phones.** Either through your own GitHub repository, or with an email
and a password through [a server you can deploy in four steps from a phone](server/README.md).
Both are optional and neither is in the way: the deck lives in the browser. A
card deleted on one phone stays deleted on the other, which is less obvious
than it sounds and is the reason a deletion leaves something behind.

**And the small things**, which are most of what you notice. Undo puts a
mis-tap back exactly as it was — the card, the log and the count — and hands you
back the same card with its answer showing. A card you have forgotten eight
times says so, because that is usually the card rather than the word, and
offers to let you fix it there and then or rest it for a fortnight. An evening
with nothing due offers the cards that come back soonest, and says what
answering early costs. A deck that arrived without its review history says so
rather than telling you a thousand cards are owed.

## What it costs

Nothing to run. The deck, the scheduler, the review loop, the subtitle mining
and the audio capture are all in the browser. An Anthropic key is optional and
only pays for words nobody has ever asked about; the server, if you deploy one,
sits inside Cloudflare's free tier at this size.

## The Anki workflows

From before the app, still working, still useful if you keep a collection in
AnkiWeb:

- **[Export a deck](anki/README.md)** — words, scheduling and review ratings out of AnkiWeb, no install.
  Straight into a folder in your Google Drive once it is
  [connected](https://sengju-han.github.io/Clipboard-stakcer/connected.html), which is a few
  minutes on a phone and never again.
- **[Add sentence audio](anki/TTS.md)** — neural audio for a field, packaged so it updates notes in place without touching their scheduling.
- **[Add cards from your phone](anki/ADD-CARD.md)** — a web form that writes the card and its audio.
- **[Audit your sentences](anki/AUDIT.md)** — finds typos and unnatural phrasing across the collection, and can fix them in place.

## Building it

Nothing to build. The app is plain modules served as files; the four libraries
it uses — ts-fsrs, fflate, fzstd, sql.js, all MIT — are vendored in
[`docs/app/vendor/`](docs/app/vendor/) with their licences.

It is tested three ways, and all three run on every pull request:

```
python3 test/run.py                    the app, in a real browser, against the real deck
python3 -m pytest test/python -q       the workflows that talk to AnkiWeb and to Claude
cd server && npm install && npm test   the server, against a real D1 through Miniflare
```

The scheduler, the database, the service worker and the file formats are the
real ones, because every bug worth catching here has been in how those behave
together rather than in any one of them — including the offline suite, which
stops the server and means it.

Two things are stood in for, and it is worth knowing which. **Claude** is, in
every suite: the tests must not spend money and must not depend on what a model
says today. **The server** is, in the account suite — and that stub is how a
real bug got through. Playwright answers a routed request *before* the
browser's own CORS check runs, so a reply every browser would refuse looked
fine to the suite: `Access-Control-Allow-Methods` omitted `PUT` while the vault
was written with `PUT`, nothing could be saved for a week, and every test
passed throughout. So one suite now talks to the real Worker on a real port,
from a page on a different one, with no stub at all — and in CI it is not
allowed to skip. See [`test/README.md`](test/README.md).
