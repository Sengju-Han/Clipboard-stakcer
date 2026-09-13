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

Pick a deck, pick a type, fill in the fields, press **Add card**. The page
follows the run and tells you when it lands.

- The **Example** field is the one that gets audio. Leave it empty and the card
  is added without any.
- The deck and type you used last are remembered; the fields clear after each
  card so you can keep going.
- Then **sync AnkiDroid**. If the card appears but the audio does not play yet,
  sync once more — Anki transfers media separately from cards, so it can arrive
  a moment later.

Expect a minute or two per card. Most of that is downloading your collection;
the audio itself takes a second or so.

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

Add an `ANTHROPIC_API_KEY` secret alongside your AnkiWeb ones
(*Settings → Secrets and variables → Actions*). The key comes from
platform.claude.com → Account Settings → API keys.

**It is optional.** Without the secret the card is added exactly as typed and
the run says so. Untick *Check my English* on the form to skip it for one card.

Cost is roughly a cent per card at current Claude Opus 5 pricing. Worth setting
a spend limit on the key in the Console regardless.

## If the note type still speaks

If you have not yet run the **Anki TTS package** workflow, your note types still
contain `{{tts}}`, and a new card will play both the recording and the synthetic
voice. The run warns you when it notices. Fix it once, under
*Cards → Back template* in AnkiDroid, by deleting the `{{tts ...}}` line.

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
