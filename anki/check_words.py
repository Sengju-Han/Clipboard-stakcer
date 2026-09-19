#!/usr/bin/env python3
"""Check the word each card exists for, not the sentence it comes with.

The sentence audit reads every example and leaves the word alone on purpose -
a correction that replaces the word makes a better sentence and a worthless
card. Which means nothing has ever looked at the word itself, and a card whose
word is misspelled teaches the misspelling, every review, for as long as it is
in the deck. `entiments` for *sentiments*. `hidious` for *hideous*.
`achipelago`. `sraggly`. `bone marrorw`.

Finding them needs no cleverness: the sentence on the card almost always
contains the word spelled correctly, because it was written separately. So the
question is only whether the card's word turns up in its own example - and when
it does not, whether that is a misspelling or an ordinary thing.

An ordinary thing, usually. `avow` against "he avowed it" is the word in
another form, and so is `come full circle` against "came full circle". Telling
those apart from `hidious` against "I'm hideous" is not something a rule does
well - both are one letter away - so the judging is asked of a model, and only
for the handful of cards where the word is missing at all.

    ANTHROPIC_API_KEY, ANKIWEB_USERNAME, ANKIWEB_PASSWORD
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import sys
from pathlib import Path

from anki.collection import Collection  # noqa: E402

from add_card import (  # noqa: E402
    check_recordings_kept,
    existing_notes,
    recordings,
    sync_up,
)
from export_deck import (  # noqa: E402
    build_deck_query,
    deck_inventory,
    fail,
    log,
    parse_deck_list,
    plural,
    sync_down,
    write_summary,
)
from proofread import BLOCK_BOUNDARY, MARKUP, client  # noqa: E402

from pydantic import BaseModel  # noqa: E402

BATCH = 25

# How much of a respelling a correction may be before it stops being one. A
# card is its word: `entiments` -> `sentiments` is a repair, `beatnik` ->
# `hipster` is a different card wearing the same scheduling. Nothing below this
# is ever written back, whatever the model says.
MIN_LIKENESS = 0.6

# Short words are in every sentence by accident - "of", "up", "on" - so their
# absence proves nothing and their presence proves less.
MIN_TOKEN = 3


class Judged(BaseModel):
    index: int
    verdict: str          # "typo" | "form" | "fine"
    corrected: str = ""
    why: str = ""


class Verdicts(BaseModel):
    results: list[Judged]


SYSTEM = """You are checking the headword on a language learner's flashcards.

Each card has a word it exists to practise and an example sentence. You are \
given only the cards where the word does not appear in its own sentence, and \
asked why not. There are three answers:

- "typo": the word is misspelled on the card. The sentence almost always has \
it spelled correctly - `hidious` on a card whose sentence reads "I'm hideous". \
Put the correct spelling in `corrected`, keeping the card's own form: if the \
card says `achipelago` and the sentence says "archipelago", the correction is \
"archipelago", not "archipelagos".
- "form": the word is there, in another form. `avow` against "he avowed it", \
`come full circle` against "came full circle", `ouster` against "he was \
ousted". This is ordinary and is not a problem. Leave `corrected` empty.
- "fine": the sentence illustrates the word without containing it, on purpose. \
A card for `alliteration` whose sentence is "Peter Piper picked a peck of \
pickled peppers", or for `euphemism` whose sentence is "he passed away". Leave \
`corrected` empty.

A correction must be a respelling of the word on the card, never a different \
word. If the card's word is simply wrong for its sentence - a card that says \
`deficit` beside a sentence about a detriment - answer "fine" and say so in \
`why`: that is for a person to decide, not for you to fix.

`why` is one short clause, lower case, no full stop."""


def tidy(text: str) -> str:
    """Plain lower-case words, with markup, media and punctuation gone."""
    text = MARKUP.sub(" ", str(text or ""))
    text = re.sub(r"\[sound:[^\]]*\]", " ", text)
    return re.sub(r"\s+", " ", re.sub(r"[^a-z' ]+", " ", text.lower())).strip()


def headword(raw: str) -> str:
    """The word itself, without the memory hook written underneath it."""
    head = BLOCK_BOUNDARY.split(str(raw or ""))[0]
    return MARKUP.sub("", head).strip()


def likeness(one: str, other: str) -> float:
    return difflib.SequenceMatcher(None, one.lower(), other.lower()).ratio()


def missing_from(word: str, example: str) -> list[str]:
    """Which of the word's own tokens the sentence does not contain."""
    have = {token for token in tidy(example).split() if token}
    return [
        token for token in tidy(word).split()
        if len(token) >= MIN_TOKEN and token not in have
    ]


def nearest(token: str, example: str) -> tuple[str, float]:
    """The closest word the sentence does have, and how close it is."""
    best, score = "", 0.0
    for other in {t for t in tidy(example).split() if len(t) >= MIN_TOKEN}:
        ratio = likeness(token, other)
        if ratio > score:
            best, score = other, ratio
    return best, score


def collect(col: Collection, note_ids: list[int], word_field: str, example_field: str):
    """Every card whose word does not appear in its own example."""
    found, skipped = [], {"no word": 0, "no example": 0, "the word is there": 0}
    for note_id in note_ids:
        note = col.get_note(note_id)
        if word_field not in note or example_field not in note:
            continue
        word = headword(note[word_field])
        example = note[example_field]
        if not word.strip():
            skipped["no word"] += 1
            continue
        if not tidy(example):
            skipped["no example"] += 1
            continue
        gone = missing_from(word, example)
        if not gone:
            skipped["the word is there"] += 1
            continue
        near, score = nearest(gone[0], example)
        found.append({
            "note_id": note_id, "guid": note.guid, "word": word,
            "example": example, "missing": gone, "nearest": near, "likeness": round(score, 3),
        })
    return found, skipped


def other_faults(col: Collection, note_ids: list[int], word_field: str,
                 example_field: str) -> dict:
    """Card problems a rule can see on its own, with no model and no key.

    None of these is corrected. Every one is a judgement a person has to make:
    whether a word in two decks is a duplicate or deliberate, whether a clue is
    too generous, whether a card with no sentence is unfinished or fine. The
    report names them and stops there.
    """
    seen: dict[str, list] = {}
    no_example, gives_it_away = [], []

    for note_id in note_ids:
        note = col.get_note(note_id)
        if word_field not in note:
            continue
        word = headword(note[word_field])
        plain = tidy(word)
        if not plain:
            continue
        seen.setdefault(plain, []).append({"note_id": note_id, "word": word})

        if example_field in note and not tidy(note[example_field]):
            no_example.append({"note_id": note_id, "word": word})

        # The front of the card. A clue that contains the word is a card
        # answered by reading it, which is a card that teaches nothing and
        # still takes up a review.
        for name, value in note.items():
            if name in (word_field, example_field):
                continue
            clue = tidy(value)
            if len(plain) > 3 and clue and re.search(rf"\b{re.escape(plain)}", clue):
                gives_it_away.append({"note_id": note_id, "word": word,
                                      "field": name, "clue": value})
                break

    twice = [{"word": rows[0]["word"], "count": len(rows)}
             for rows in seen.values() if len(rows) > 1]
    return {
        "twice": sorted(twice, key=lambda r: r["word"].lower()),
        "no_example": no_example,
        "gives_it_away": gives_it_away,
    }


def judge(batch: list[dict], api) -> list[Judged]:
    lines = []
    for index, item in enumerate(batch):
        lines.append(
            f"<card index=\"{index}\">\n<word>{item['word']}</word>\n"
            f"<sentence>{tidy(item['example'])}</sentence>\n</card>"
        )
    response = api.messages.parse(
        model=os.environ.get("ANTHROPIC_MODEL", "").strip() or "claude-haiku-4-5",
        max_tokens=8000, system=SYSTEM,
        messages=[{"role": "user", "content": "\n".join(lines)}],
        output_format=Verdicts,
    )
    if getattr(response, "stop_reason", None) == "refusal":
        return [Judged(index=i, verdict="fine") for i in range(len(batch))]
    by_index = {r.index: r for r in response.parsed_output.results}
    return [by_index.get(i, Judged(index=i, verdict="fine")) for i in range(len(batch))]


def a_respelling(was: str, now: str) -> bool:
    """Whether a correction is the same word spelled properly, or a different word."""
    if not now.strip() or now.strip().lower() == was.strip().lower():
        return False
    return likeness(was, now) >= MIN_LIKENESS


def apply_all(col: Collection, fixes: list[dict], word_field: str) -> dict:
    """Write the corrected words back, and refuse to sync if anything else moved."""
    before_notes = existing_notes(col)
    before_audio = recordings(col)
    before_cards = {row[0]: row for row in col.db.all(
        "select id, nid, did, ord, type, queue, due, ivl, factor, reps, lapses from cards")}

    touched = []
    for row in fixes:
        note_id = col.db.scalar("select id from notes where guid = ?", row["guid"])
        if not note_id:
            continue
        note = col.get_note(note_id)
        if word_field not in note:
            continue
        # Only the first line. Everything after the first block break is the
        # learner's own memory hook and is not this job's business.
        raw = note[word_field]
        head = headword(raw)
        if head != row["word"]:
            continue                      # it changed since it was read
        # The word itself, before any memory hook written underneath it.
        first = BLOCK_BOUNDARY.split(raw)[0]
        if MARKUP.search(first):
            # Bold, colour, a ruby annotation: rewriting the line as plain text
            # would throw it away, and the correction is one letter. Reported
            # instead, the same way a formatted sentence is.
            row["why"] = "the word carries formatting, so it was left as typed"
            row["corrected"] = ""
            continue
        note[word_field] = row["corrected"] + raw[len(first):]
        col.update_note(note)
        touched.append(note_id)

    after_notes = existing_notes(col)
    if set(after_notes) != set(before_notes):
        fail("The note list changed while correcting words, so nothing was synced.")
    moved = [n for n, mod in before_notes.items()
             if after_notes[n] != mod and n not in touched]
    if moved:
        fail(f"{len(moved)} notes changed that should not have. Nothing was synced.")
    after_cards = {row[0]: row for row in col.db.all(
        "select id, nid, did, ord, type, queue, due, ivl, factor, reps, lapses from cards")}
    if after_cards != before_cards:
        fail("Card scheduling moved while correcting words, so nothing was synced.")
    # This one touches no sentence, so it can take no recording off anything.
    check_recordings_kept(before_audio, recordings(col))
    return {"applied": len(touched)}


def report_other(faults: dict) -> list[str]:
    """The rule-based findings, which are reported and never acted on."""
    lines = []
    twice, no_example, given = (faults.get(k) or []
                                for k in ("twice", "no_example", "gives_it_away"))
    if not (twice or no_example or given):
        return lines

    lines += ["", "## And while it was looking", "",
              "None of this is changed by this job. Each one is a decision only you "
              "can make, so it is named and left alone."]
    if twice:
        # plural() over the words, not the cards: three duplicated words is not
        # "the same word on three cards", which is what this said and is a
        # different and wronger fact.
        lines += ["", f"### {plural(len(twice), 'word')} on more than one card", "",
                  "Two cards for one word means answering it twice for the rest of "
                  "your life, on two separate schedules.", ""]
        lines += [f"- `{row['word']}` — {row['count']} cards" for row in twice[:40]]
    if no_example:
        lines += ["", f"### {plural(len(no_example), 'card')} with no example sentence", "",
                  "A word with nothing to hang it on is the hardest kind to keep. The "
                  "audio workflow also has nothing to record for these.", ""]
        lines += [f"- `{row['word']}`" for row in no_example[:40]]
    if given:
        lines += ["", f"### {plural(len(given), 'card')} whose clue contains the answer", "",
                  "The front of the card says the word it is asking for, so it is "
                  "answered by reading it — and still takes a review every time.", ""]
        for row in given[:40]:
            clue = tidy(row["clue"])[:70]
            lines.append(f"- `{row['word']}` — {row['field']}: _{clue}_")
    return lines


def write_report(rows: list[dict], skipped: dict, checked: int, applied: bool,
                 faults: dict | None = None) -> None:
    typos = [r for r in rows if r.get("verdict") == "typo" and r.get("corrected")]
    refused = [r for r in rows if r.get("verdict") == "typo" and not r.get("corrected")]
    forms = [r for r in rows if r.get("verdict") == "form"]
    fine = [r for r in rows if r.get("verdict") == "fine"]
    # Whether anything was judged at all. Without a key nothing is, and every
    # count below would read as zero - which says "checked, and none of them
    # were misspelled" when the truth is "not checked". That is the worse of
    # the two wrong answers, because it is the reassuring one.
    judged = any("verdict" in row for row in rows)

    if not judged:
        lines = [
            f"## {plural(len(rows), 'word')} not in their own sentence, unjudged",
            "",
            f"- Cards whose word is in their own sentence: **{skipped['the word is there']}**",
            f"- Cards where it is not: **{len(rows)}**",
            "",
            "Which of these are misspellings and which are the word in another form "
            "was not worked out: that needs `ANTHROPIC_API_KEY`, because `hidious` "
            "against \"I'm hideous\" and `avow` against \"he avowed it\" are both one "
            "letter from the word on the card.",
        ]
        if rows:
            lines += ["", "| on the card | the sentence has |", "|---|---|"]
            for row in sorted(rows, key=lambda r: r["word"].lower())[:60]:
                lines.append(f"| `{row['word']}` | {row.get('nearest', '')} |")
        lines += report_other(faults or {})
        write_summary(lines)
        return

    lines = [
        f"## {plural(len(typos), 'misspelled word')} out of {checked} cards"
        if not applied else
        f"## {plural(len(typos), 'word')} corrected, out of {checked} cards",
        "",
        f"- Cards whose word is in their own sentence: **{skipped['the word is there']}**",
        f"- Cards where it is not, and was looked at: **{len(rows)}**",
        f"- Of those: **{len(typos)}** misspelled, **{len(forms)}** the word in another "
        f"form, **{len(fine)}** deliberate",
    ]
    if typos:
        lines += ["", "### Misspelled", "",
                  "| on the card | should be | the sentence has |", "|---|---|---|"]
        for row in sorted(typos, key=lambda r: r["word"].lower()):
            lines.append(f"| `{row['word']}` | **{row['corrected']}** | {row['nearest']} |")
    if refused:
        lines += [
            "", f"### Left alone — {plural(len(refused), 'card')} where the fix is not a respelling",
            "",
            "The word and its sentence do not match, and the correction would be a "
            "different word rather than the same one spelled properly. That changes "
            "what the card is for, so it is a decision for you.",
            "",
        ]
        for row in refused[:30]:
            lines.append(f"- `{row['word']}` — {row.get('why', '')} — _{tidy(row['example'])[:80]}_")
    if not applied and typos:
        lines += ["", "Nothing has been changed. Re-run with **apply** ticked to write "
                      "these back and sync them."]
    lines += report_other(faults or {})
    write_summary(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Check the word on each card.")
    parser.add_argument("--deck", default="*")
    parser.add_argument("--word-field", default="Back")
    parser.add_argument("--example-field", default="Example")
    parser.add_argument("--out-dir", default="word-check")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--batch", type=int, default=BATCH)
    parser.add_argument("--apply", action="store_true", help="Write the corrections and sync.")
    parser.add_argument("--local-collection", default="")
    args = parser.parse_args()

    username = os.environ.get("ANKIWEB_USERNAME", "").strip()
    password = os.environ.get("ANKIWEB_PASSWORD", "")
    if not args.local_collection and (not username or not password):
        fail("ANKIWEB_USERNAME / ANKIWEB_PASSWORD are not set.")
    # A key is what tells a misspelling from an ordinary inflection, and both are
    # one letter from the word on the card. Without one the rest still runs -
    # the duplicates, the cards with no sentence, the clues that give the answer
    # away, and the plain list of words missing from their own example - and
    # says what it could not judge.
    judging = bool(os.environ.get("ANTHROPIC_API_KEY", "").strip())
    if args.apply and not judging:
        fail("ANTHROPIC_API_KEY is not set, so nothing can be corrected.",
             "Without it there is no telling `hidious` (a misspelling) from `avow` "
             "against \"he avowed it\" (the word in another form), and correcting the "
             "second would be a card ruined. Run without apply to see the rest.")

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.local_collection:
        # Defined in both branches on purpose: the backup below reopens it, and
        # a name that exists on only one path is a NameError waiting for the
        # day somebody allows a local collection to sync.
        work = Path(args.local_collection)
        col, auth = Collection(str(work)), None
    else:
        work = Path("anki-work") / "collection.anki2"
        work.parent.mkdir(exist_ok=True)
        col = Collection(str(work))
        auth = col.sync_login(username, password, os.environ.get("ANKIWEB_ENDPOINT") or None)
        sync_down(col, username, password, os.environ.get("ANKIWEB_ENDPOINT") or None)

    if args.apply and auth:
        backup = out_dir / "before-word-check.colpkg"
        col.export_collection_package(str(backup), False, True)
        col = Collection(str(work))
        log(f"Backup written to {backup} ({backup.stat().st_size // 1024}KB, no media).")

    query = build_deck_query(parse_deck_list(args.deck), deck_inventory(col))
    note_ids = list(col.find_notes(query))
    items, skipped = collect(col, note_ids, args.word_field, args.example_field)
    if args.limit:
        items = items[: args.limit]
    log(f"{len(note_ids)} notes; {len(items)} whose word is not in their own sentence.")

    faults = other_faults(col, note_ids, args.word_field, args.example_field)

    api = client() if judging else None
    for start in range(0, len(items) if judging else 0, args.batch):
        batch = items[start : start + args.batch]
        try:
            for item, verdict in zip(batch, judge(batch, api)):
                item["verdict"] = verdict.verdict
                item["why"] = verdict.why
                corrected = (verdict.corrected or "").strip()
                item["corrected"] = corrected if a_respelling(item["word"], corrected) else ""
                if verdict.verdict == "typo" and corrected and not item["corrected"]:
                    item["why"] = (item.get("why", "") +
                                   f" (refused: {corrected!r} is not a respelling of "
                                   f"{item['word']!r})").strip()
        except Exception as exc:
            log(f"::warning::Batch at {start} failed ({type(exc).__name__}: {exc}). Stopping here.")
            break
        log(f"  {min(start + len(batch), len(items))}/{len(items)}")

    (out_dir / "words.jsonl").write_text(
        "\n".join(json.dumps(i, ensure_ascii=False) for i in items) + "\n", encoding="utf-8")

    fixes = [i for i in items if i.get("verdict") == "typo" and i.get("corrected")]
    log(f"{plural(len(fixes), 'word')} would change.")

    if args.apply and fixes:
        outcome = apply_all(col, fixes, args.word_field)
        log(f"Corrected {plural(outcome['applied'], 'word')}.")
        if auth:
            sync_up(col, auth)
            log("Synced to AnkiWeb.")

    col.close()
    if not judging:
        log("::notice::No ANTHROPIC_API_KEY, so the words that are missing from their "
            "own sentence are listed but not judged.")
    write_report(items, skipped, len(note_ids), bool(args.apply and fixes), faults)
    return 0


if __name__ == "__main__":
    sys.exit(main())
