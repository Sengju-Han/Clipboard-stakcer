# Replacing AnkiDroid's robot voice with real audio

AnkiDroid's `{{tts}}` speaks a field with the phone's built-in engine at review
time. This workflow generates neural audio for that field once, packages it as
an `.apkg`, and you import it — after which the card plays a recording instead.

It runs on GitHub's servers. You need nothing installed, on a phone or anywhere.

## It does not touch your review history

This is the part worth understanding before you run it, because it is the thing
that would hurt if it were wrong.

Anki stores notes (your text) and cards (the scheduling) in separate tables, and
matches notes on a hidden identifier called a **guid** when importing. The
package is built from your own collection, so every note in it carries the guid
it already has. On import Anki finds the note that guid belongs to, updates its
fields, and never looks at the cards — so due dates, intervals, ease and FSRS
state stay exactly as they are.

The package is also built with scheduling deliberately left out, so there is
nothing in it that *could* move a card even if the matching went wrong.

Every build proves this before you see it: the job imports the package it just
made into a copy of your collection and checks that no note was added, that no
card's scheduling changed, and that no field other than the one being spoken
differs by a single byte. If any check fails the job fails and no package is
offered for download.

## Run it

1. **Actions** → **Anki TTS package** → **Run workflow**.
2. Leave *deck* as `Steve Jobs`, or set it to whichever deck you want to try
   first. **Do one small deck first** — see *Start small* below.
3. Press the green button and wait. The run summary shows what it generated and
   the result of every verification check.
4. Download **anki-tts-package** under *Artifacts* and unzip it. Inside is
   `tts-update.apkg`.

Tick *dry_run* to see what a deck would produce — how many files, how many
characters — without generating anything.

### Start small

Run one deck of a hundred-odd cards before the big one. It exercises every risky
part — guid matching, the import mode, whether the audio actually plays on your
phone — on a deck small enough to fix by hand if something is wrong. Only once
that import reports *updated, 0 added* is it worth doing the rest.

## Then, on the phone

1. AnkiDroid → **export a full backup (.colpkg)** and check the file exists.
   This is your undo button. Do not skip it.
2. Download `tts-update.apkg` to the device.
3. Open it in AnkiDroid and import, choosing the mode that **updates** existing
   notes rather than duplicating them.
4. Read the import summary. It should say roughly *N notes updated, 0 added*.
   **If it says added, stop.** Do not sync. Restore the backup from step 1, then
   force a full download-sync on your other device.
5. Open a card and check the play button appears and plays. You should hear the
   recording **only**. If you also hear the synthetic voice, the note type kept
   its `{{tts}}` line — remove it under *Cards → Back template*.
6. Sync, so the audio reaches your other device.

## What gets spoken

Only the first part of the field. Fields often hold a sentence and then a note
to self after a line break — a translation, a definition, a reminder — and a
voice reading that aloud is noise. Everything after the first block break is
ignored, along with any HTML.

A note is skipped if the field is empty, or if it already contains a `[sound:]`
tag. That second rule is what makes re-running safe: a second pass over the same
deck changes nothing rather than giving a card two play buttons.

Filenames are a hash of the text being spoken, which buys two things: identical
sentences share one file, and a run that is interrupted picks up where it left
off instead of starting again. Generated audio is cached between runs.

## Voices

`en-US-AvaNeural` by default. `en-US-AndrewNeural` and `en-US-EmmaNeural` are
the other two worth trying; any Microsoft neural voice name works.

To change the voice on cards that already have audio, tick **revoice**. Without
it a note that already has a `[sound:]` tag is skipped — that is what makes
re-running the workflow safe — so a new voice would never be heard.

Re-voicing replaces the tag rather than adding a second one, and it only ever
touches a recording this workflow made. **A recording you made yourself is left
exactly as it is**, including on a note that holds one of each, because that is
the one file here that cannot be generated again.

The old recordings stay in your media folder until you run **Check Media →
Delete Unused** in AnkiDroid. That is on purpose: if the new voice turns out to
be worse, re-voicing back to the old one costs nothing while the files are still
there.

Re-voicing to the voice a card already has is a no-op. Files generated before
the voice was part of their name are the exception: nothing records which voice
read them, so they are regenerated once and named properly from then on.

## A caveat about where the audio comes from

The voices are Microsoft Edge's, reached through an endpoint that was never
published for this use. There is no account, no key and no bill, which is why it
is the right choice for a one-off personal batch. It is also unofficial: it can
stop working without notice, and it is not something to build a habit on. If it
breaks, the fix is to switch to a paid provider, not to debug it.

Nothing about the rest of the pipeline depends on that choice — the audio is
just files on disk by the time the package is built.

## It never writes to your AnkiWeb account

Same guarantee as the export workflow: the collection is downloaded with Anki's
own sync library, modified on the runner, packaged, and thrown away. If AnkiWeb
ever asks for an upload the job stops instead. Your account is only ever read.

## If something goes wrong

| Message | What it means |
| --- | --- |
| `N notes needed audio but M were tagged` | A bug in the builder. Nothing was packaged; nothing was changed. |
| `N file(s) could not be generated` | The voice service failed repeatedly. Nothing was packaged. Re-run — files already generated are kept, so it resumes. |
| A verification check failed | The package would not have behaved correctly, so it is not offered. The summary names the check. |
| `AnkiWeb asked for a FULL UPLOAD` | Your account has no collection on the server. Sync from AnkiDroid once, then re-run. Nothing was changed. |
| Import says *added*, not *updated* | Guid matching failed. Restore the backup and do not sync. |
