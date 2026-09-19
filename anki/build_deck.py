#!/usr/bin/env python3
"""Turn an Anki export into a deck the review app can open.

The app schedules with FSRS, which keeps a different kind of state than the
SM-2 numbers most of these cards were built under: a stability in days and a
difficulty from 1 to 10, rather than an interval and an ease percentage. A card
that has been reviewed thirty times carries thirty times' worth of evidence
about how well it is known, and throwing that away to start everything at new
would be the single most destructive thing an import could do.

So nothing starts from scratch. Where Anki already holds FSRS memory state -
which it does once FSRS is enabled - it is carried across untouched. Where it
holds only the older numbers, they are converted: the interval is the best
single estimate of stability, and the ease percentage maps onto difficulty.
The conversion is approximate and says so; being approximately right about a
card's history beats being exactly wrong about all of them.

    python build_deck.py --export anki-export/cards.json --out docs/deck
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

SOUND_TAG = re.compile(r"\[sound:([^]]*)\]")
TTS_DIRECTIVE = re.compile(r"\{\{[^}]*\btts\b[^}]*\}\}")
MARKUP = re.compile(r"<[^>]+>")

# FSRS card states, matching ts-fsrs's State enum exactly. The app reads these
# numbers straight into the scheduler, so they are not ours to renumber.
NEW, LEARNING, REVIEW, RELEARNING = 0, 1, 2, 3

STATE_FROM_ANKI = {
    "new": NEW,
    "learning": LEARNING,
    "review": REVIEW,
    "relearning": RELEARNING,
    # A suspended or buried card keeps whatever progress it had; the app hides
    # it rather than rescheduling it, so the state it returns to matters.
    "suspended": REVIEW,
    "buried": REVIEW,
    "manually buried": REVIEW,
    "sibling buried": REVIEW,
}

# FSRS difficulty runs 1 (easiest) to 10 (hardest). Anki's ease runs from 130%
# upward, with 250% the default a card starts at and drops from when it lapses.
# 250% therefore means "no evidence either way" and belongs in the middle.
EASE_ANCHOR = 250.0
DIFFICULTY_MID = 5.0


def as_float(value, fallback=None):
    try:
        if value in ("", None):
            return fallback
        return float(value)
    except (TypeError, ValueError):
        return fallback


def as_int(value, fallback=0):
    """Tolerates the export writing a count as either a number or a list."""
    if isinstance(value, list):
        return len(value)
    try:
        if value in ("", None):
            return fallback
        return int(value)
    except (TypeError, ValueError):
        return fallback


def plain(text: str) -> str:
    """Field text without markup or sound tags, for display and for search."""
    without = SOUND_TAG.sub("", TTS_DIRECTIVE.sub("", text or ""))
    # A div or br is a line break to a reader, not a space.
    without = re.sub(r"<\s*(?:div|br|p|li|tr)\b[^>]*>", "\n", without, flags=re.I)
    without = MARKUP.sub("", without)
    without = without.replace("&nbsp;", " ").replace("&amp;", "&")
    without = without.replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"')
    return re.sub(r"[ \t]+", " ", without).strip()


def difficulty_from(card: dict) -> float:
    """FSRS difficulty, preferring what Anki already knows."""
    pct = as_float(card.get("fsrs_difficulty_pct"))
    if pct is not None:
        # export_deck.py writes difficulty back out as a percentage of the 1-10
        # range, the way Anki displays it. This is the exact inverse.
        return round(min(10.0, max(1.0, 1.0 + (pct / 100.0) * 9.0)), 4)

    # No FSRS state: read the ease instead. A card that has dropped below the
    # 250% starting point has been failed, and is harder than average.
    ease = as_float(card.get("ease_pct"))
    if ease is None or ease <= 0:
        return DIFFICULTY_MID
    # 130% (Anki's floor) lands near 8, 250% at 5, higher eases below that.
    guess = DIFFICULTY_MID + (EASE_ANCHOR - ease) / 40.0
    return round(min(10.0, max(1.0, guess)), 4)


# FSRS refuses a card that has been reviewed but has no memory behind it:
# stability below one day is not a memory, it is a contradiction. A card can
# reach that state in Anki - reviewed today, interval still zero - so the floor
# is applied here rather than left to blow up mid-session.
S_MIN = 1.0


def stability_from(card: dict) -> float:
    """FSRS stability in days, preferring what Anki already knows."""
    known = as_float(card.get("fsrs_stability_days"))
    if known and known > 0:
        return round(max(S_MIN, known), 4)

    # The interval is what Anki decided this card could survive, which is the
    # same question stability answers. It is the honest stand-in.
    interval = as_float(card.get("interval_days"), 0) or 0
    return round(max(S_MIN, interval), 4)


def at_noon(day: date) -> str:
    """A due date as an instant that still means that day wherever it is read.

    Midnight UTC is the obvious choice and it is wrong twice over. Read in
    Seoul it is nine in the morning, so a card due today only turned up
    mid-morning — somebody reviewing before breakfast was told nothing was
    owed. And the app compares review cards by Anki's day, which begins at
    four, so midnight UTC read anywhere west of here fell into the day before
    and the card arrived early.

    Noon has neither problem: 12:00 UTC is the same calendar day everywhere
    from UTC-8 to UTC+15 and stays on that day after the four-hour shift.
    Hawaii and Samoa are the exceptions, and would see a card a day late.
    """
    return datetime(day.year, day.month, day.day, 12, tzinfo=timezone.utc).isoformat()


def due_from(card: dict, today: date, tally: dict | None = None) -> str:
    """When the card is next owed, as an ISO timestamp.

    Falling back to today is right but it is not nothing: a card that says it
    is due now when it is not is a card in tonight's session that should not
    be. `tally` counts how often that happens so the build can say so — 1,004
    cards out of 1,177 landing on today once went unremarked, and the deck
    that came out of it told somebody a thousand cards were owed.
    """
    raw = (card.get("due_date") or "").strip()
    if raw:
        try:
            when = datetime.strptime(raw[:10], "%Y-%m-%d").date()
            # Anki writes a far-future date for cards it will never show again;
            # a century out is not a schedule, it is a tombstone.
            if when.year < today.year + 50:
                return at_noon(when)
            if tally is not None:
                tally["far_future"] = tally.get("far_future", 0) + 1
        except ValueError:
            if tally is not None:
                tally["unreadable"] = tally.get("unreadable", 0) + 1
    elif tally is not None:
        tally["missing"] = tally.get("missing", 0) + 1
    if tally is not None:
        tally["fell_back"] = tally.get("fell_back", 0) + 1
    return at_noon(today)


def last_review_from(card: dict):
    raw = (card.get("last_review") or "").strip()
    if not raw:
        return None
    try:
        when = datetime.strptime(raw[:10], "%Y-%m-%d").date()
    except ValueError:
        return None
    return datetime(when.year, when.month, when.day, tzinfo=timezone.utc).isoformat()


def was_bigger(target: Path, kept: int) -> str:
    """Whether the deck this is about to replace held a great deal more.

    The proportion above is blind to an export that came back short: twelve
    rows in, twelve cards out, nothing skipped, and a deck of twelve published
    over a deck of twelve hundred. What the app is reading today is the only
    record of how big the collection was yesterday.
    """
    if not target.exists():
        return ""
    try:
        before = json.loads(target.read_text(encoding="utf-8")).get("card_count", 0)
    except (ValueError, OSError):
        return ""                      # unreadable is not evidence of anything
    return losing_too_much(kept, int(before or 0), "that were in the deck are not in this one")


def build_card(card: dict, today: date, tally: dict | None = None) -> dict | None:
    fields = card.get("fields_raw") or card.get("fields") or {}
    if not isinstance(fields, dict) or not fields:
        return None

    # Which field holds what varies by note type, so go by what the exporter
    # worked out rather than assuming a shape.
    word_field = card.get("word_field") or ""
    word = plain(fields.get(word_field, "")) or plain(card.get("word", ""))
    if not word:
        return None

    # Half this collection writes the answer field as the word on one line and a
    # memory hook underneath - "a convict / conquered victim in a prison". Run
    # together they read as a nonsense phrase and make the answer hard to check
    # at a glance, so they are separated here rather than papered over in the UI.
    # The hook belongs with the answer, never with the clue: it is the thing that
    # makes the word stick once you have seen it, not a hint before you try.
    word, _, hook = (line.strip() for line in (word.partition("\n")))
    hook = hook.strip()

    # Everything that is not the word and not the example is context: the clue
    # the learner reads before trying to recall.
    example_field = next((n for n in fields if n.lower() == "example"), "")
    clue_fields = [n for n in fields if n != word_field and n != example_field]

    example_raw = fields.get(example_field, "") if example_field else ""
    sounds = SOUND_TAG.findall(example_raw) + [
        ref for name, value in fields.items() if name != example_field
        for ref in SOUND_TAG.findall(value)
    ]

    state = STATE_FROM_ANKI.get((card.get("state") or "new").lower(), NEW)
    if state == NEW:
        stability, difficulty, due = 0.0, 0.0, due_from({}, today)
        last_review = None
    else:
        stability = stability_from(card)
        difficulty = difficulty_from(card)
        due = due_from(card, today, tally)
        last_review = last_review_from(card)

    return {
        # The guid is Anki's note identity and the only stable key across an
        # export, a re-export, and a round trip back. Ours is built on it.
        "id": card.get("guid") or f"card-{card.get('card_id')}",
        "guid": card.get("guid") or "",
        "deck": card.get("deck") or "Default",
        "word": word,
        "hook": hook,
        "clue": "\n".join(filter(None, (plain(fields.get(n, "")) for n in clue_fields))),
        "example": plain(example_raw),
        "audio": sounds[0] if sounds else "",
        "tags": [t for t in (card.get("tags") or "").split() if t],
        "notetype": card.get("notetype") or "",
        "created": card.get("created") or "",
        # FSRS state, in exactly the shape ts-fsrs wants back.
        "fsrs": {
            "due": due,
            "stability": stability,
            "difficulty": difficulty,
            "elapsed_days": 0,
            "scheduled_days": as_int(card.get("interval_days")),
            "learning_steps": 0,
            "reps": as_int(card.get("reviews")),
            "lapses": as_int(card.get("lapses")),
            "state": state,
            "last_review": last_review,
        },
        # Kept so the app can show where a card's schedule came from, and so a
        # bad conversion can be told apart from a bad review.
        "imported": {
            "interval_days": as_int(card.get("interval_days")),
            "ease_pct": as_float(card.get("ease_pct")) or 0,
            "had_fsrs_state": bool(as_float(card.get("fsrs_stability_days"))),
        },
    }


# How much of a collection may disappear between one build and the next before
# this refuses to publish the result. The deck it writes is committed and
# pushed to Pages by the workflow with nothing in between, so a build that
# quietly lost most of the cards is a deck the next phone downloads.
MAX_LOSS = 0.10


def losing_too_much(kept: int, total: int, what: str) -> str:
    """The sentence to fail with, or empty when the loss is within reason."""
    if total <= 0 or kept >= total * (1 - MAX_LOSS):
        return ""
    gone = total - kept
    return (f"{gone} of {total} cards {what} — {kept} left, "
            f"which is {kept * 100 // total}% of what there was.")


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the review app's deck from an Anki export.")
    parser.add_argument("--export", required=True, help="cards.json written by export_deck.py")
    parser.add_argument("--out", default="docs/deck", help="Directory to write deck.json into.")
    parser.add_argument("--allow-loss", action="store_true",
                        help="Write the deck even if most of the collection has gone. "
                             "For a collection that really did shrink.")
    args = parser.parse_args()

    source = Path(args.export)
    if not source.exists():
        print(f"::error::{source} does not exist. Run the deck export first.", flush=True)
        return 1

    payload = json.loads(source.read_text(encoding="utf-8"))
    rows = payload.get("cards") or []
    if not rows:
        print("::error::That export has no cards in it.", flush=True)
        return 1

    today = date.today()
    tally: dict[str, int] = {}
    cards, skipped = [], 0
    for row in rows:
        built = build_card(row, today, tally)
        if built is None:
            skipped += 1
            continue
        cards.append(built)

    if not cards:
        print("::error::Nothing could be built from that export.", flush=True)
        return 1

    # A duplicate guid would make two cards fight over one row in the app's
    # store, and the second would silently win.
    seen, unique = set(), []
    for card in cards:
        if card["id"] in seen:
            skipped += 1
            continue
        seen.add(card["id"])
        unique.append(card)

    decks = sorted({c["deck"] for c in unique})
    carried = sum(1 for c in unique if c["imported"]["had_fsrs_state"])
    converted = sum(1 for c in unique if c["fsrs"]["state"] != NEW and not c["imported"]["had_fsrs_state"])
    fresh = sum(1 for c in unique if c["fsrs"]["state"] == NEW)

    deck = {
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": payload.get("collection_source", ""),
        "card_count": len(unique),
        "decks": decks,
        "scheduling": {
            "carried_fsrs_state": carried,
            "converted_from_ease": converted,
            "new": fresh,
            # How many due dates could not be used, and why. The app reads this
            # and so does anybody looking at the file.
            "due_date_fell_back": tally.get("fell_back", 0),
            "due_date_far_future": tally.get("far_future", 0),
            "due_date_missing": tally.get("missing", 0),
            "due_date_unreadable": tally.get("unreadable", 0),
        },
        "cards": unique,
    }

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / "deck.json"

    # Two ways this can be wrong and still look like a build.
    #
    # Most of the rows failing to become cards is the field names having moved:
    # build_card returns None when it cannot find a word, so a wrong word_field
    # empties the deck one card at a time and reports it as a skip count.
    #
    # The export itself coming back short is the other, and the proportion
    # above cannot see it - twelve rows in, twelve cards out, nothing lost.
    # Only the deck already on disk knows the collection used to be bigger.
    complaints = [c for c in (
        losing_too_much(len(unique), len(rows), "in the export did not become cards"),
        was_bigger(target, len(unique)),
    ) if c]
    if complaints and not args.allow_loss:
        for line in complaints:
            print(f"::error::{line}", flush=True)
        print("Nothing was written. The deck on disk is the one the app still reads.\n"
              "If the collection really did shrink, run again with --allow-loss.", flush=True)
        return 1
    for line in complaints:
        print(f"::warning::{line} Written anyway, because --allow-loss was given.", flush=True)

    target.write_text(json.dumps(deck, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")

    size_kb = target.stat().st_size / 1024
    print(f"Wrote {target} — {len(unique)} cards, {size_kb:.0f} KB", flush=True)
    print(f"  decks: {', '.join(decks)}", flush=True)
    print(f"  scheduling carried from FSRS: {carried}", flush=True)
    print(f"  scheduling converted from interval and ease: {converted}", flush=True)
    print(f"  new, never reviewed: {fresh}", flush=True)
    if skipped:
        print(f"  skipped (no fields, no word, or duplicate): {skipped}", flush=True)

    # The loudest thing this build can tell you. A deck where most cards had no
    # usable due date is a deck that will say a thousand cards are owed on the
    # morning it is opened, and the only honest moment to say so is here.
    fell_back = tally.get("fell_back", 0)
    if fell_back:
        why = ", ".join(
            f"{tally[k]} {name}" for k, name in
            [("far_future", "dated a lifetime away"), ("missing", "with no date at all"),
             ("unreadable", "with a date that could not be read")] if tally.get(k)
        )
        share = fell_back / len(unique)
        level = "warning" if share >= 0.2 else "notice"
        print(f"::{level}::{fell_back} of {len(unique)} cards had no usable due date "
              f"({why}) and are scheduled for today. "
              + ("Most of this deck, so the app will say almost everything is owed. "
                 "That normally means the export did not come from AnkiWeb. "
                 if share >= 0.5 else ""), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
