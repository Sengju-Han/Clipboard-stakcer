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
  meant to change** changed, and that **no card's scheduling moved** — and
  refuses to sync if any of that is wrong

If a corrected sentence already had a recording, that recording says the old
wording. The `[sound:]` tag is removed and you are told how many, so the
**Anki TTS package** workflow can record the corrected sentence.

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
