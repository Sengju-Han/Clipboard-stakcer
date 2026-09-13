# Add cards from a web form, with the audio made for you

A small page you open on your phone. Type a card, press **Add card**, and about
a minute later it is in your collection on every device — with the sentence
already recorded, because the job that saves it also generates the audio.

No computer, no app to install, nothing to import.

## How it fits together

```
phone browser  ->  GitHub Actions  ->  AnkiWeb  ->  AnkiDroid
  the form         adds the note        sync         next sync
                   makes the audio
```

The page itself does nothing except ask GitHub to run the workflow. All the work
happens on GitHub's servers, which is why your AnkiWeb password never has to
leave the repository secrets.

## What it does to your collection

This is the **only** part of this repository that writes to your AnkiWeb
account. The other two workflows are read-only. So it is deliberately narrow:

- It **only adds**. Before syncing anything it checks that every note that
  existed before still exists, unchanged, and that exactly one note was created.
  If anything else moved, it stops and syncs nothing.
- A **full upload is refused**, the same as everywhere else. That is the
  operation that would replace your real collection with the runner's copy, and
  nothing here will ever perform it.
- It **will not add a duplicate** unless you tick the box, so a double tap on
  *Add card* costs you nothing.
- Every run keeps a **backup of the collection as it was before the note was
  added**, under *Artifacts*, for 14 days. Cards only, no media, so it is small.

## Setting it up, once

**1. Put the workflow on your default branch.** GitHub will only run a workflow
it can see on `main`, so merge this branch first. Nothing works until you do.

**2. Turn on GitHub Pages.** *Settings → Pages → Source: Deploy from a branch →
`main` → `/docs`.* After a minute the form is at
`https://<your-username>.github.io/<repository>/`. Bookmark it, or add it to
your home screen so it opens like an app.

**3. Add an `ANTHROPIC_API_KEY` secret** if you want your English checked — see
*Checking your English* below. Skip it and everything else still works.

**4. Make a token.** *Settings → Developer settings → Personal access tokens →
Fine-grained tokens → Generate new token.*

- Repository access: **Only select repositories** → this one.
- Permissions → Repository permissions → **Actions: Read and write**.
- Give it an expiry you are happy with. You will need to replace it when it runs out.

**5. Paste it into the form** under *Settings*. It is kept in that browser only
and is sent to `api.github.com` and nowhere else. It is never committed, never
in the page's source, and not readable by anyone who opens the page.

### Public or private?

The page is served by GitHub Pages, and **Pages on a private repository needs a
paid GitHub plan**. On the free plan the repository has to be public for the
form to work.

Nothing secret is published either way: the page holds no credentials, and
`docs/collection.json` holds only your deck and note type *names* — no cards, no
study history. Your AnkiWeb login stays in repository secrets, which are never
readable, public repository or not. But if the deck names themselves are
private, that is the thing to weigh.

## Using it

Pick a deck, pick a type, fill in the fields, press **Add card**.

**Nothing waits for the network.** The fields clear the instant you press it and
the cursor goes back to the first one, so you can type the next card immediately.
Each card you send becomes a row above the form that follows its own run on its
own — several can be in flight at once, and a row turning green is the only
signal you need to look at.

- The **Example** field is the one that gets audio. Leave it empty and the card
  is added without any.
- The deck and type you used last are remembered. The note type list is ordered
  by how much you actually use it, so the one you want is already selected.
- If a send fails, the row says why and offers **Put it back** — it drops
  everything you typed back into the form. Nothing you write is ever lost.
- Then **sync AnkiDroid**. If the card appears but the audio does not play yet,
  sync once more — Anki transfers media separately from cards, so it can arrive
  a moment later.

A card takes about half a minute to land, but you should never notice, because
you are not waiting on it. Cards sent in a burst queue up on GitHub's side and
drain one after another; that is deliberate, since two syncs at once would
collide.

## Checking your English

Sentences typed on a phone pick up typos, and a sentence can be spelled
perfectly and still not be one a native speaker would write. Both are checked
before the card is saved, and the sentence is corrected in place. What changed
is listed in the run summary, so you can see it and disagree.

The audio is generated **after** the correction, so the recording says the
corrected sentence rather than the typo.

It works under deliberately tight rules. It will not:

- change or remove the word you are practising, even for one that reads better
- change the meaning, or add or remove information
- make a correct sentence longer, more formal, or more literary
- touch text in another language — Korean is left exactly as typed
- fill in a blank. `' '` marks a gap you fill in from memory; it survives.

A sentence that is already fine comes back untouched. That is the normal case,
and a run reporting no changes has not failed.

**One exception:** when the sentence contains formatting (bold, colour, a link),
the suggestion is reported but **not** applied — the correction comes back as
plain text, and applying it would throw the formatting away. Fix those by hand.

### Turning it on

Everything is already wired. The one thing left is the key:

1. Get one at **platform.claude.com → Account Settings → API keys**.
2. Add it to this repository as a secret named exactly **`ANTHROPIC_API_KEY`**
   (*Settings → Secrets and variables → Actions → New repository secret*).

That is the whole setup. The next card you add gets checked — no code change, no
workflow edit, nothing to redeploy.

**It stays optional.** Without the secret the card is added exactly as typed and
the run says so. Untick *Check my English* on the form to skip it for one card.

### What it costs

It runs on **Claude Sonnet 5** — $2 per million input tokens, $10 per million
output. Measured against the real prompt and your average sentence:

| | per card | 100 cards | 1000 cards |
| --- | --- | --- | --- |
| light | $0.002 | $0.21 | $2.12 |
| typical | $0.005 | $0.46 | $4.62 |
| heavy | $0.010 | $0.96 | $9.62 |

The spread is thinking tokens, billed as output, which vary with the sentence.
New accounts get a small amount of free credit, and at this rate that covers a
great many cards.

API billing is separate from a Claude.ai subscription — a Pro or Max plan does
not cover it. Worth setting a spend limit on the key in the Console.

### Using a different model

Set a repository **variable** (not a secret) named `ANTHROPIC_MODEL`
(*Settings → Secrets and variables → Actions → Variables*) to any model id —
`claude-haiku-4-5` to halve the cost again, `claude-opus-5` for the most
capable. Leave it unset for the default.

## Turning off Anki's built-in voice

Out of the box your note types contain a `{{tts}}` directive, which makes
AnkiDroid read the sentence aloud with the phone's synthetic voice at review
time. Once a card carries a real recording, that is not a fallback — it is a
second voice talking over the first. Every add-card run warns you while it is
still there.

Run **Actions → Anki remove built-in TTS → Run workflow** once. It deletes the
directives and syncs, so it takes effect on every device. Tick *dry_run* first
if you want to see what it would remove.

Only template text changes: fields, field order and note type ids are left
alone, and the job refuses to sync if it finds otherwise. It keeps a backup
first, and running it twice is harmless — the second time it finds nothing to do.

Cards that already have a `[sound:]` recording keep playing it. Cards without
one simply go quiet, which is what the **Anki TTS package** workflow is for.

## If something goes wrong

| What you see | What it means |
| --- | --- |
| *Not found* | The workflow is not on `main` yet, or the owner/repository is wrong. |
| *GitHub rejected the token* | Expired or mistyped. Make a new one. |
| *not allowed to run workflows* | The token is missing **Actions: Read and write**, or is not scoped to this repository. |
| *A note with this first field already exists* | Anki's own duplicate check. Tick the box if you meant it. |
| *has no field called …* | The type you picked does not have that field. `EN LEARNING TOOL` has Front, Back and Example only — no Hint. |
| *AnkiWeb asked for a FULL UPLOAD* | Refused on purpose. Sync your phone with AnkiWeb first, then add the card again. Nothing was changed. |
| *AnkiWeb needs a full download* | Another device changed something structural. The card was **not** saved. Sync your phone, then re-add. |
| The run failed | Open it from the link. If it failed before the sync step, nothing reached your collection. |
| *ANTHROPIC_API_KEY is not set* | The sentence check is off. Add the secret, or untick *Check my English*. The card was still added. |
| *Could not check the sentence* | The check itself failed, so the card went in exactly as typed. Nothing was lost. |
| *Suggestions not applied* | The sentence carries formatting. The suggestion is in the log; apply it by hand. |

## Running it without the page

The form is only a convenience. The same workflow has a *Run workflow* button in
the **Actions** tab, and the script is a normal CLI:

```bash
python anki/add_card.py \
  --deck "Steve Jobs" --notetype "EN LEARNING TOOL" \
  --field "Front=상기시키다" --field "Back=evoke" \
  --field "Example=The smell of rain evokes memories of my childhood."
```

Add `--provider silent` to exercise everything without calling the voice
service, and `--local-collection path/to/collection.anki2` to work on a file
instead of syncing.
