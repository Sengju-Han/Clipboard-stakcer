# Anki deck export (runs on GitHub, nothing to install)

Pulls your collection from **AnkiWeb** and exports one deck as spreadsheet-ready
files: the target word, every field on the note, the scheduling state, and your
actual review ratings. It runs on GitHub's servers, so you do not need Python,
Anki desktop, or anything else on your computer or phone — just a browser.

## Before you start

This repository is **public**, which means anyone can download the files a
workflow run produces. Your AnkiWeb password stays encrypted either way, but the
exported vocabulary and study history would be public. Two options:

- **Recommended:** make the repository private first —
  *Settings → General → Danger Zone → Change visibility → Make private*.
- Or leave it public and tick the `confirm_public` box when you run the workflow.
  The workflow refuses to run without that tick, so nothing gets published by accident.

## One-time setup: store your AnkiWeb login

1. Open this repository on github.com → **Settings** → **Secrets and variables** → **Actions**.
2. **New repository secret**, name `ANKIWEB_USERNAME`, value = the email address you
   use to log in to ankiweb.net. Save.
3. **New repository secret** again, name `ANKIWEB_PASSWORD`, value = your AnkiWeb
   password. Save.

GitHub encrypts both and masks them in logs. They are never printed, never written
into the export, and are not readable back out of the settings page — not even by you.

## Run it

1. Go to the **Actions** tab → **Anki deck export** → **Run workflow**.
2. **First time:** leave *deck* empty and press the green button. When the run
   finishes, open it and read the summary — it lists every deck name you have.
3. **Run it again**, this time pasting the deck name exactly as listed
   (e.g. `English::TOEFL`). Subdecks are included automatically.
   To export **several decks at once**, separate them with commas —
   `Podcasts, Suits, duo` — or put `*` to export every deck you have.
4. Set *timezone* to your own (e.g. `Asia/Seoul`) so dates match your review days.
5. When the run finishes, scroll to **Artifacts** and download **anki-export**.
   It downloads as a **zip — unzip it first**, then open `anki-export.xlsx`.
   (Opening the zip itself in a spreadsheet app is what produces "the file is
   damaged".) It contains the files below. The summary on that page also shows a
   preview table, so you can check the result without downloading anything.

Artifacts are deleted after 7 days; just run the workflow again for a fresh copy.

## What you get

| File | Contents |
| --- | --- |
| `anki-export.xlsx` | **Start here.** Cards and reviews as two sheets, with filters ready — opens directly in Excel, Numbers, and Google Sheets |
| `cards.csv` | One row per card, same data, for scripts or importing elsewhere |
| `cards.json` | The same data plus full review history, for scripts |
| `reviews.csv` | One row per individual review you have ever done |
| `by-deck/<deck>.csv` | The same rows split per deck, when you export more than one |
| `decks.csv` / `decks.json` | Every deck name with its card count |

Columns in `cards.csv`:

| Column | Meaning |
| --- | --- |
| `word` | The target word — see *Which field becomes the word* below. Override with the *word_field* input. |
| `state` | new / learning / review / relearning / suspended / buried |
| `due_date`, `interval_days` | When it comes back, and the current interval |
| `ease_pct` | SM-2 ease factor (250% is the default starting value) |
| `reviews`, `lapses` | Times reviewed, and times a mature card was forgotten |
| `last_rating`, `avg_rating` | The button you pressed: 1 again, 2 hard, 3 good, 4 easy |
| `again` / `hard` / `good` / `easy` | How many times you pressed each button |
| `success_pct` | Share of reviews that were not "again" |
| `fsrs_difficulty_pct` | FSRS difficulty, 0% easiest to 100% hardest (blank if you don't use FSRS) |
| `fsrs_stability_days` | Days until recall probability falls to ~90% |
| `fsrs_retrievability_pct` | Estimated chance you'd recall it right now |
| `first_review`, `last_review`, `created` | Dates, in the timezone you chose |
| `total_minutes`, `avg_seconds` | Time spent on the card |
| `tags`, `notetype`, `card_template`, `flag` | Card metadata |
| `field: …` | One column per field on your note type, HTML stripped |

## Exporting several decks

Put a comma-separated list in the *deck* box (`Podcasts, Suits, duo`), or `*` for
everything. You get one combined `cards.csv` — the `deck` column says where each
card came from — plus a per-deck copy under `by-deck/`, and the run summary breaks
the totals down by deck.

Naming a parent deck and one of its subdecks together is safe: each card is
exported once, never twice. Unknown names are reported as a warning and skipped,
so one typo doesn't waste the whole run.

## Which field becomes the `word` column

Anki has no concept of "the word", so the script works it out per note type:

1. If you set the **word_field** input, that field is used.
2. Otherwise a field *named* like a vocabulary field wins — `Word`, `Term`,
   `Vocab`, `Expression`, `단어`, `어휘`, and similar.
3. Otherwise the field that is consistently **much shorter** than the others wins.
   This is what handles cue-sentence decks, where the prompt is a sentence with a
   blank in it and the answer field holds the single word.
4. If nothing stands out — two sentence fields, say — it falls back to the note's
   sort field and says so.

Every run prints which field it chose and why, with an example note, under
*Which field became the `word` column* in the run summary. If it guessed wrong,
re-run with **word_field** set. Either way every field is always exported in full
as its own `field: …` column, so nothing is lost.

## Is this safe for my Anki account?

Yes — it only ever reads:

- It downloads your collection with Anki's own official sync library, the same
  code Anki desktop uses.
- It **never uploads**. If AnkiWeb ever asks for an upload, the job stops with an
  error rather than sending anything, so the temporary empty collection on the
  runner can never overwrite your real one.
- It syncs cards only, not media, so no audio or images are transferred.
- The runner and everything on it are destroyed when the job ends.

Reviewing on your phone or desktop is unaffected. Anki may show "sync required"
afterwards, which is normal for any extra device and resolves on your next sync.

## If something goes wrong

| Message | Fix |
| --- | --- |
| `AnkiWeb login failed` | Use your login **email**, not your display name. Log in at ankiweb.net once in a browser, then re-run. |
| `AnkiWeb asked for a FULL UPLOAD` | Your account has no collection on the server yet. Sync once from Anki desktop or AnkiDroid, then re-run. Nothing was changed. |
| `No cards matched` | Deck names are case-sensitive and use `::` between parent and subdeck. Run with an empty deck name to list them. |
| `This repository is public` | Make it private, or tick `confirm_public`. |
| Sync fails repeatedly | AnkiWeb rate-limits frequent syncs. Wait a few minutes between runs. |
| "The file is damaged" when opening it | You are most likely opening the downloaded `.zip` rather than a file inside it. Unzip first (on iPhone: tap the zip in Files, then open the folder it creates), then open `anki-export.xlsx`. |
| The CSV looks like one long column | Your spreadsheet app is splitting on semicolons instead of commas. Open `anki-export.xlsx` instead — it has no import step to get wrong. |

## Running it yourself (optional)

Not needed for the GitHub workflow, but the script is a normal CLI:

```bash
pip install -r anki/requirements.txt
export ANKIWEB_USERNAME=you@example.com ANKIWEB_PASSWORD=...
python anki/export_deck.py --deck "English::TOEFL" --timezone Asia/Seoul

# or, against a collection file you already have, with no login at all:
python anki/export_deck.py --local-collection ~/.local/share/Anki2/User\ 1/collection.anki2 --deck "English::TOEFL"
```
