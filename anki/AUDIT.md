# Auditing the sentences you already have

A card made months ago carries the mistakes of the day it was typed. Reviewed a
hundred times, it teaches them a hundred times. This reads the whole collection,
checks every sentence, and tells you what is wrong.

## Look before you change anything

**Actions → Anki audit sentences → Run workflow.** Leave *apply* off.

It changes nothing. It reads your collection, checks the sentences, and writes
the findings into the run summary — each one as it was, as it should be, and
why. Grouped by deck, worst deck first.

Start with *limit* at **50**. That tells you whether the corrections are the
kind you want before you spend on a thousand of them.

When you are happy, run it again with *limit* `0` and *apply* ticked.

## What it checks, and what it leaves alone

Only the sentence — the part before the first line break. Everything after that
is your own gloss, often in Korean, and is neither sent nor touched.

Skipped outright:

- empty fields
- sentences carrying formatting, because a correction comes back as plain text
  and writing it over bold or colour would throw that away

## Applying

`apply` writes the corrections and syncs. Before it does:

- a **`.colpkg` backup** is taken and kept as an artifact for 14 days
- afterwards it checks that **no note disappeared**, that **only the notes it
  meant to change** changed, that **no card's scheduling moved**, and that
  **no recording went missing anywhere in the collection** that it did not
  mean to remove — and refuses to sync if any of that is wrong

If a corrected sentence already had a recording, that recording says the old
wording. The `[sound:]` tag is removed and the summary says how many, so the
**Anki TTS package** workflow can record the corrected sentence — it looks for
notes with no audio, which is exactly what those are afterwards.

> It did not say how many until 19 September 2026, and the reason is worth
> knowing if you ran it before then. The correction is written back by a
> function that keeps the sentence and your own note after it and nothing else,
> so a `[sound:]` tag on the sentence line was already gone by the time
> anything counted it. It counted what was left, found none, and reported none.
> Every correction applied before that date took the card's recording with it
> in silence. Re-running **Anki TTS package** records them again.

## The word itself

The audit above never touches the word a card exists for. That is deliberate —
a correction that replaces it makes a better sentence and a worthless card —
but it means nothing has ever looked at the word, and a card whose *word* is
misspelled teaches the misspelling every time it comes up.

**Actions → Anki check words → Run workflow**, with *apply* off.

It asks one question per card: does the word turn up in its own example
sentence? Almost always yes, because the sentence was written separately and
has the word spelled properly in it. When the answer is no, that is either a
misspelling —

| on the card | the sentence has |
| --- | --- |
| `entiments` | sentiments |
| `hidious` | hideous |
| `achipelago` | archipelago |
| `bone marrorw` | bone marrow |
| `sraggly` | scraggly |

— or an ordinary thing: `avow` against *he avowed it*, `come full circle`
against *came full circle*. Both are a letter or two from the word on the card,
so no rule tells them apart; the ones that are missing get sent to Claude to be
judged, and nothing else does. On a thousand-card collection that is about
seven requests.

**A correction is only ever a respelling.** The word on the card is what the
card is: `entiments` → `sentiments` is a repair, `beatnik` → `hipster` is a
card with a year of scheduling on it that is now about something else. Anything
that is not close enough to the original spelling is refused and reported for
you to decide, whatever the model says. A word carrying formatting is left
alone for the same reason the audit leaves a formatted sentence alone.

Only the word changes. The sentence is untouched, no recording moves, and the
same checks run before anything is synced: no note gone, nothing else changed,
no card's scheduling moved.

The same run also names three things a rule can see on its own, and **changes
none of them** — each is a decision only you can make:

- **the same word on two cards**, which means answering it twice for the rest
  of your life, on two separate schedules
- **cards with no example sentence** — the hardest kind to keep, and the audio
  workflow has nothing to record for them either
- **cards whose clue contains the answer**, like a card for *ditch* whose front
  says `ditch`. It is answered by reading it and still takes a review every
  time.

Those need no key. Without `ANTHROPIC_API_KEY` the run still reports them, and
lists the words missing from their own sentence without judging them; only
*apply* requires one, because telling `hidious` from `avow` is the entire
difference between a repair and a ruined card.

## Resuming

Results are written as each batch lands and cached between runs. A run that
times out, or one you cancel, picks up where it stopped rather than paying for
the same sentences twice. The same applies if you start with 50 and then run
the rest: the first 50 are not re-checked.

## Cost

With `claude`, sentences go twenty to a request so one system prompt covers the
batch. For a thousand sentences that is roughly **a dollar**, once.

`languagetool` is free but goes one sentence at a time with a pause between, to
stay inside the public rate limit. For a whole collection expect it to take
around an hour — and remember it finds typos, not unnatural phrasing.
