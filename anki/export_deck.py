#!/usr/bin/env python3
"""Download an Anki collection from AnkiWeb and export one deck as CSV/JSON.

Runs headless (GitHub Actions, no Anki desktop). It is strictly read-only with
respect to your AnkiWeb account: it only ever downloads. If AnkiWeb ever asks
for an upload, the script aborts instead, so a local empty collection can never
overwrite your real one.

Credentials come from the environment, never from the command line, so they
cannot leak into process lists or CI logs:
    ANKIWEB_USERNAME, ANKIWEB_PASSWORD, ANKIWEB_ENDPOINT (optional)
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import NoReturn

import anki.lang

# strip_html() needs the i18n backend initialised before a Collection exists.
anki.lang.set_lang("en_US")

from anki.collection import Collection  # noqa: E402
from anki.sync_pb2 import SyncCollectionResponse  # noqa: E402
from anki.utils import strip_html  # noqa: E402

CHANGES = SyncCollectionResponse.ChangesRequired

# card.queue / card.type -> human readable state
QUEUE_STATES = {
    -3: "buried",
    -2: "buried",
    -1: "suspended",
    0: "new",
    1: "learning",
    2: "review",
    3: "learning",
    4: "preview",
}
TYPE_STATES = {0: "new", 1: "learning", 2: "review", 3: "relearning"}
RATING_NAMES = {1: "again", 2: "hard", 3: "good", 4: "easy"}
REVIEW_KINDS = {0: "learning", 1: "review", 2: "relearning", 3: "filtered", 4: "manual", 5: "rescheduled"}

# Field names that usually hold the target word itself rather than a prompt.
VOCAB_FIELD_NAME = re.compile(
    r"word|term|vocab|target|expression|lemma|spelling|headword|단어|어휘|표현", re.IGNORECASE
)


def log(msg: str) -> None:
    print(msg, flush=True)


def fail(msg: str, hint: str = "") -> NoReturn:
    print(f"::error::{msg}", flush=True)
    if hint:
        print(hint, flush=True)
    sys.exit(1)


# --------------------------------------------------------------------------
# formatting helpers
# --------------------------------------------------------------------------

def clean(text: str) -> str:
    """Plain-text version of an Anki field (drops HTML, media refs, cloze marks)."""
    if not text:
        return ""
    text = re.sub(r"\[sound:[^]]*\]", " ", text)
    text = re.sub(r"\{\{c\d+::(.*?)(?:::.*?)?\}\}", r"\1", text, flags=re.S)
    try:
        text = strip_html(text)
    except Exception:  # pragma: no cover - fallback if i18n is unavailable
        text = re.sub(r"<[^>]+>", " ", text)
    text = unicodedata.normalize("NFC", text).replace(" ", " ")
    return re.sub(r"\s+", " ", text).strip()


class Clock:
    """Timestamp formatting in the user's chosen timezone."""

    def __init__(self, tz_name: str) -> None:
        self.tz = timezone.utc
        self.name = "UTC"
        if tz_name and tz_name.upper() != "UTC":
            try:
                from zoneinfo import ZoneInfo

                self.tz = ZoneInfo(tz_name)
                self.name = tz_name
            except Exception:
                log(f"::warning::Unknown timezone {tz_name!r}; falling back to UTC.")

    def date(self, ts: int | None) -> str:
        return datetime.fromtimestamp(ts, self.tz).strftime("%Y-%m-%d") if ts else ""

    def stamp(self, ts: int | None) -> str:
        return datetime.fromtimestamp(ts, self.tz).isoformat(timespec="seconds") if ts else ""


def round_or_blank(value, digits: int = 2):
    return round(value, digits) if value else ""


# --------------------------------------------------------------------------
# AnkiWeb sync (download only)
# --------------------------------------------------------------------------

def sync_down(col: Collection, username: str, password: str, endpoint: str | None) -> None:
    log("Logging in to AnkiWeb...")
    try:
        auth = col.sync_login(username, password, endpoint)
    except Exception as exc:
        fail(
            f"AnkiWeb login failed ({type(exc).__name__}).",
            "Check the ANKIWEB_USERNAME / ANKIWEB_PASSWORD secrets. Use the email address\n"
            "you log in to ankiweb.net with. If you recently changed your password, or you\n"
            "have never synced this account, log in at ankiweb.net once and try again.",
        )

    log("Syncing collection from AnkiWeb...")
    try:
        out = col.sync_collection(auth, False)  # sync_media=False: cards only
        if out.new_endpoint:
            log("Server moved the account to another endpoint; retrying there.")
            auth.endpoint = out.new_endpoint
            out = col.sync_collection(auth, False)
    except Exception as exc:
        fail(f"Sync failed ({type(exc).__name__}): {exc}")

    if out.server_message:
        log(f"AnkiWeb says: {out.server_message}")

    required = out.required
    if required == CHANGES.FULL_UPLOAD:
        fail(
            "AnkiWeb asked for a FULL UPLOAD - refusing.",
            "This job never uploads. Uploading from here would replace your real collection\n"
            "with an empty one. Nothing was changed on AnkiWeb. This usually means the\n"
            "account has no collection on the server yet: sync from Anki desktop or AnkiDroid\n"
            "once, then re-run this workflow.",
        )
    if required in (CHANGES.FULL_DOWNLOAD, CHANGES.FULL_SYNC):
        log("Server requires a full download (expected on a fresh checkout). Downloading...")
        col.full_upload_or_download(auth=auth, server_usn=out.server_media_usn, upload=False)
        log("Full download complete.")
    else:
        log("Collection is up to date.")


# --------------------------------------------------------------------------
# extraction
# --------------------------------------------------------------------------

def deck_inventory(col: Collection) -> list[dict]:
    decks = []
    for entry in col.decks.all_names_and_ids():
        escaped = entry.name.replace('"', '\\"')
        decks.append(
            {
                "deck": entry.name,
                "deck_id": entry.id,
                "cards_incl_subdecks": len(col.find_cards(f'deck:"{escaped}"')),
                "notes_incl_subdecks": len(col.find_notes(f'deck:"{escaped}"')),
            }
        )
    return sorted(decks, key=lambda d: d["deck"])


def parse_deck_list(raw: str) -> list[str]:
    """Split the deck input on commas and newlines, keeping :: inside names."""
    names = []
    for chunk in re.split(r"[,\n]", raw or ""):
        name = chunk.strip().strip('"').strip()
        if name and name not in names:
            names.append(name)
    return names


def build_deck_query(requested: list[str], decks: list[dict]) -> str:
    """Turn the requested deck names into one Anki search, skipping unknown ones.

    Listing a parent and its subdeck is harmless: a card matching both clauses
    is still returned once.
    """
    if any(name in ("*", "all") for name in requested):
        return "deck:*"

    known = {deck["deck"].lower() for deck in decks}
    clauses = []
    for name in requested:
        lowered = name.lower()
        if lowered not in known and not any(k.startswith(f"{lowered}::") for k in known):
            log(f"::warning::No deck named {name!r} - skipping it.")
            continue
        clauses.append('deck:"{}"'.format(name.replace('"', '\\"')))

    if not clauses:
        fail(
            "None of the deck names matched a deck in your collection.",
            "Deck names use :: between a parent and a subdeck. Run with an empty deck "
            "box to list them. Available: " + (", ".join(d["deck"] for d in decks) or "(none)"),
        )
    return clauses[0] if len(clauses) == 1 else "(" + " or ".join(clauses) + ")"


def safe_filename(name: str) -> str:
    return re.sub(r"[^\w.-]", "_", name.replace("::", "__"), flags=re.UNICODE) or "deck"


def card_record(col: Collection, card_id: int, clock: Clock) -> dict:
    card = col.get_card(card_id)
    note = card.note()
    stats = col.card_stats_data(card_id)

    fields = {name: value for name, value in note.items()}
    notetype = note.note_type() or {}

    # Which field holds the target word is decided later, per note type, once
    # every card has been read - see assign_word_column().
    names = list(fields)
    sort_index = int(notetype.get("sortf", 0) or 0)
    sort_field = names[sort_index] if sort_index < len(names) else (names[0] if names else "")

    # Review history, newest first as returned by the backend.
    reviews = []
    for entry in stats.revlog:
        reviews.append(
            {
                "reviewed_at": clock.stamp(entry.time),
                "rating": entry.button_chosen or None,
                "rating_name": RATING_NAMES.get(entry.button_chosen, ""),
                "kind": REVIEW_KINDS.get(entry.review_kind, str(entry.review_kind)),
                "interval_days": round(entry.interval / 86400, 3) if entry.interval else 0,
                "previous_interval_days": round(entry.last_interval / 86400, 3) if entry.last_interval else 0,
                "ease_pct": entry.ease / 10 if entry.ease else "",
                "seconds_taken": round(entry.taken_secs, 1) if entry.taken_secs else "",
                "fsrs_stability_days": round_or_blank(entry.memory_state.stability) if entry.HasField("memory_state") else "",
                "fsrs_difficulty": round_or_blank(entry.memory_state.difficulty) if entry.HasField("memory_state") else "",
            }
        )

    graded = [r["rating"] for r in reviews if r["rating"] in RATING_NAMES]
    counts = {name: 0 for name in RATING_NAMES.values()}
    for rating in graded:
        counts[RATING_NAMES[rating]] += 1
    passed = sum(1 for r in graded if r >= 2)

    # Negative queues (suspended/buried) override the card type; otherwise the
    # type tells new/learning/review/relearning apart.
    if card.queue < 0:
        state = QUEUE_STATES.get(card.queue, "unknown")
    else:
        state = TYPE_STATES.get(card.type, QUEUE_STATES.get(card.queue, "unknown"))

    # FSRS memory state lives on the card once FSRS is enabled; fall back to the
    # newest review entry, which carries it even before the card is rescheduled.
    difficulty = stability = None
    if stats.HasField("memory_state"):
        difficulty, stability = stats.memory_state.difficulty, stats.memory_state.stability
    elif reviews and reviews[0]["fsrs_difficulty"] != "":
        difficulty, stability = reviews[0]["fsrs_difficulty"], reviews[0]["fsrs_stability_days"]

    return {
        "word": "",  # filled in by assign_word_column()
        "deck": stats.deck or col.decks.name(card.did),
        "state": state,
        "due_date": clock.date(stats.due_date) if stats.due_date else "",
        "new_card_position": stats.due_position if state == "new" else "",
        "interval_days": stats.interval or 0,
        "ease_pct": stats.ease / 10 if stats.ease else "",
        "reviews": stats.reviews,
        "lapses": stats.lapses,
        "last_rating": reviews[0]["rating"] if reviews and reviews[0]["rating"] else "",
        "last_rating_name": reviews[0]["rating_name"] if reviews else "",
        "avg_rating": round(sum(graded) / len(graded), 2) if graded else "",
        "again": counts["again"],
        "hard": counts["hard"],
        "good": counts["good"],
        "easy": counts["easy"],
        "success_pct": round(100 * passed / len(graded), 1) if graded else "",
        # FSRS difficulty is 1-10 internally; Anki shows it as a percentage.
        "fsrs_difficulty_pct": round(100 * (float(difficulty) - 1) / 9, 1) if difficulty else "",
        "fsrs_stability_days": round_or_blank(float(stability)) if stability else "",
        "fsrs_retrievability_pct": round(100 * stats.fsrs_retrievability, 1) if stats.fsrs_retrievability else "",
        "desired_retention": round_or_blank(stats.desired_retention, 3),
        "first_review": clock.date(stats.first_review),
        "last_review": clock.date(stats.latest_review),
        "created": clock.date(stats.added),
        "total_minutes": round(stats.total_secs / 60, 1) if stats.total_secs else "",
        "avg_seconds": round(stats.average_secs, 1) if stats.average_secs else "",
        "flag": card.user_flag() or "",
        "tags": " ".join(note.tags),
        "notetype": stats.notetype,
        "card_template": stats.card_type,
        "deck_preset": stats.preset,
        "note_id": note.id,
        "card_id": card_id,
        "_sort_field": sort_field,
        "_fields": {name: clean(value) for name, value in fields.items()},
        "_reviews": reviews,
    }


def plural(count: float, noun: str) -> str:
    return f"{count:g} {noun}" + ("" if count == 1 else "s")


def _median_length(values: list[str]) -> float | None:
    """Median word count of the non-empty values, or None if they are all empty."""
    lengths = [len(value.split()) for value in values if value]
    return median(lengths) if lengths else None


def choose_word_field(field_names: list[str], samples: dict[str, list[str]], sort_field: str) -> tuple[str, str]:
    """Pick the field holding the target word, and explain the choice.

    Sort order is no guide: a note may well be prompted by a cue sentence and
    answered with the word. So prefer a field named like a vocabulary field,
    then the field that is consistently much shorter than the others.
    """
    for name in field_names:
        if VOCAB_FIELD_NAME.search(name):
            return name, "its name looks like a vocabulary field"

    lengths = {}
    for name in field_names:
        value = _median_length(samples[name])
        if value is not None:
            lengths[name] = value
    if len(lengths) >= 2:
        shortest = min(lengths, key=lambda name: (lengths[name], field_names.index(name)))
        runner_up = min(value for name, value in lengths.items() if name != shortest)
        if lengths[shortest] <= 3 and runner_up > lengths[shortest]:
            return shortest, (
                f"it is the short one - {plural(lengths[shortest], 'word')} per note "
                f"against {plural(runner_up, 'word')} in the next shortest field"
            )

    return sort_field, "no field looked more like the word, so the note's sort field was used"


def assign_word_column(records: list[dict], override: str | None) -> list[str]:
    """Fill the "word" column for every card and report how it was decided."""
    by_notetype: dict[str, list[dict]] = {}
    for record in records:
        by_notetype.setdefault(record["notetype"], []).append(record)

    report: list[str] = []
    for notetype, group in sorted(by_notetype.items()):
        field_names: list[str] = []
        for record in group:
            for name in record["_fields"]:
                if name not in field_names:
                    field_names.append(name)
        samples = {name: [r["_fields"].get(name, "") for r in group] for name in field_names}

        if override and override in field_names:
            chosen, why = override, "you set the word_field input"
        else:
            if override:
                log(f"::warning::Note type {notetype!r} has no field named {override!r}; detecting instead.")
            chosen, why = choose_word_field(field_names, samples, group[0]["_sort_field"])

        for record in group:
            record["word"] = record["_fields"].get(chosen, "") or record["_fields"].get(record["_sort_field"], "")

        example = next((r for r in group if r["_fields"].get(chosen)), group[0])
        shown = " · ".join(
            f"{name}: {(example['_fields'].get(name) or '')[:40]}" for name in field_names[:4]
        )
        report.append(
            f"- **{notetype}** ({len(group)} cards): `word` column = **{chosen}**, because {why}.\n"
            f"  - fields: {', '.join(f'`{n}`' for n in field_names)}\n"
            f"  - example — {shown}"
        )
    return report


# --------------------------------------------------------------------------
# output
# --------------------------------------------------------------------------

def write_cards_csv(path: Path, records: list[dict]) -> list[str]:
    field_names: list[str] = []
    for record in records:
        for name in record["_fields"]:
            if name not in field_names:
                field_names.append(name)

    base = [k for k in records[0] if not k.startswith("_")] if records else []
    header = base + [f"field: {name}" for name in field_names]

    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=header, extrasaction="ignore")
        writer.writeheader()
        for record in records:
            row = {k: v for k, v in record.items() if not k.startswith("_")}
            for name in field_names:
                row[f"field: {name}"] = record["_fields"].get(name, "")
            writer.writerow(row)
    return header


def write_per_deck_csvs(out_dir: Path, records: list[dict]) -> dict[str, list[dict]]:
    """Group by deck; when more than one is exported, also write a file per deck."""
    groups: dict[str, list[dict]] = {}
    for record in records:
        groups.setdefault(record["deck"], []).append(record)
    if len(groups) > 1:
        folder = out_dir / "by-deck"
        folder.mkdir(parents=True, exist_ok=True)
        for deck, group in groups.items():
            write_cards_csv(folder / f"{safe_filename(deck)}.csv", group)
    return groups


def write_reviews_csv(path: Path, records: list[dict]) -> int:
    header = [
        "word", "card_id", "reviewed_at", "rating", "rating_name", "kind",
        "interval_days", "previous_interval_days", "ease_pct", "seconds_taken",
        "fsrs_stability_days", "fsrs_difficulty",
    ]
    rows = 0
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=header, extrasaction="ignore")
        writer.writeheader()
        for record in records:
            for review in reversed(record["_reviews"]):  # oldest first, reads naturally
                writer.writerow({"word": record["word"], "card_id": record["card_id"], **review})
                rows += 1
    return rows


def write_summary(lines: list[str]) -> None:
    text = "\n".join(lines) + "\n"
    target = os.environ.get("GITHUB_STEP_SUMMARY")
    if target:
        with open(target, "a", encoding="utf-8") as handle:
            handle.write(text)
    log(text)


def deck_breakdown(by_deck: dict[str, list[dict]]) -> list[str]:
    lines = ["| deck | cards | studied | mature | reviews |", "|---|---|---|---|---|"]
    for deck, group in sorted(by_deck.items()):
        studied = sum(1 for r in group if r["reviews"])
        mature = sum(1 for r in group if isinstance(r["interval_days"], int) and r["interval_days"] >= 21)
        lines.append(
            f"| {deck} | {len(group)} | {studied} | {mature} | {sum(r['reviews'] for r in group)} |"
        )
    return lines


def markdown_table(records: list[dict], limit: int) -> list[str]:
    columns = ["word", "state", "due_date", "interval_days", "ease_pct", "reviews", "lapses", "last_rating_name"]
    lines = ["", "| " + " | ".join(columns) + " |", "|" + "---|" * len(columns)]
    for record in records[:limit]:
        cells = [str(record.get(c, "")).replace("|", "\\|")[:60] for c in columns]
        lines.append("| " + " | ".join(cells) + " |")
    if len(records) > limit:
        lines.append(f"| _...{len(records) - limit} more in the downloaded files_ |" + " |" * (len(columns) - 1))
    return lines


# --------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="Export an Anki deck from AnkiWeb.")
    parser.add_argument(
        "--deck",
        default="",
        help="Deck name, or several separated by commas. '*' exports every deck. "
        "Subdecks are always included. Empty = only list the decks.",
    )
    parser.add_argument("--query", default="", help="Raw Anki search string; overrides --deck.")
    parser.add_argument("--out-dir", default="anki-export", help="Directory for the exported files.")
    parser.add_argument("--word-field", default="", help="Field holding the target word (default: the note's sort field).")
    parser.add_argument("--timezone", default="UTC", help="IANA timezone for dates, e.g. Asia/Seoul.")
    parser.add_argument("--limit", type=int, default=0, help="Export at most N cards (0 = all).")
    parser.add_argument(
        "--local-collection",
        default="",
        help="Read an existing collection.anki2 file instead of syncing (offline use and tests).",
    )
    args = parser.parse_args()

    username = os.environ.get("ANKIWEB_USERNAME", "").strip()
    password = os.environ.get("ANKIWEB_PASSWORD", "")
    if not args.local_collection and (not username or not password):
        fail(
            "ANKIWEB_USERNAME / ANKIWEB_PASSWORD are not set.",
            "Add them under Settings -> Secrets and variables -> Actions in this repository.",
        )

    clock = Clock(args.timezone)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.local_collection:
        col = Collection(args.local_collection)
    else:
        work_dir = Path("anki-work")
        work_dir.mkdir(exist_ok=True)
        col = Collection(str(work_dir / "collection.anki2"))

    try:
        if not args.local_collection:
            sync_down(col, username, password, os.environ.get("ANKIWEB_ENDPOINT") or None)

        decks = deck_inventory(col)
        (out_dir / "decks.json").write_text(json.dumps(decks, ensure_ascii=False, indent=2), encoding="utf-8")
        with (out_dir / "decks.csv").open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(
                handle, fieldnames=["deck", "deck_id", "cards_incl_subdecks", "notes_incl_subdecks"]
            )
            writer.writeheader()
            writer.writerows(decks)
        log(f"Found {len(decks)} decks, {col.card_count()} cards in the collection.")

        requested = parse_deck_list(args.deck)
        if not requested and not args.query:
            write_summary(
                ["## Your Anki decks", "", "Re-run this workflow with one of these deck names:", ""]
                + [f"- `{d['deck']}` — {d['cards_incl_subdecks']} cards (including subdecks)" for d in decks]
                + [
                    "",
                    "You can name several at once, separated by commas "
                    "(`Podcasts, Suits, duo`), or `*` for every deck.",
                ]
            )
            return 0

        query = args.query if args.query else build_deck_query(requested, decks)
        log(f"Searching: {query}")

        try:
            card_ids = list(col.find_cards(query))
        except Exception as exc:
            fail(f"Anki rejected the search {query!r}: {exc}")

        if not card_ids:
            names = ", ".join(d["deck"] for d in decks) or "(none)"
            fail(
                f"No cards matched {query!r}.",
                f"Those decks exist but hold no cards. Available decks: {names}",
            )

        if args.limit:
            card_ids = card_ids[: args.limit]
        log(f"Exporting {len(card_ids)} cards...")

        records = [card_record(col, cid, clock) for cid in card_ids]
        field_report = assign_word_column(records, args.word_field.strip() or None)
        records.sort(key=lambda r: (r["deck"], r["word"].lower()))

        write_cards_csv(out_dir / "cards.csv", records)
        review_rows = write_reviews_csv(out_dir / "reviews.csv", records)
        by_deck = write_per_deck_csvs(out_dir, records)
        payload = {
            "exported_at": clock.stamp(int(datetime.now(tz=timezone.utc).timestamp())),
            "timezone": clock.name,
            "query": query,
            "card_count": len(records),
            "cards": [
                {**{k: v for k, v in r.items() if not k.startswith("_")}, "fields": r["_fields"], "reviews": r["_reviews"]}
                for r in records
            ],
        }
        (out_dir / "cards.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    finally:
        col.close()

    studied = [r for r in records if r["reviews"]]
    matured = [r for r in records if isinstance(r["interval_days"], int) and r["interval_days"] >= 21]
    leeches = sorted(records, key=lambda r: -r["lapses"])[:5]
    title = ", ".join(by_deck) if len(by_deck) > 1 else (args.deck or query)
    write_summary(
        [
            f"## {title} — {len(records)} cards",
            "",
            f"- Studied at least once: **{len(studied)}** · never seen: **{len(records) - len(studied)}**",
            f"- Mature (interval 21+ days): **{len(matured)}**",
            f"- Total reviews recorded: **{review_rows}**",
            "- Hardest cards (most lapses): " + (", ".join(f"`{r['word']}` ({r['lapses']})" for r in leeches if r["lapses"]) or "none yet"),
            "",
            "Download **anki-export** under *Artifacts* below for `cards.csv`, `reviews.csv` and `cards.json`"
            + (", plus one CSV per deck under `by-deck/`." if len(by_deck) > 1 else "."),
            "",
        ]
        + (deck_breakdown(by_deck) if len(by_deck) > 1 else [])
        + [
            "",
            "### Which field became the `word` column",
            "",
        ]
        + field_report
        + [
            "",
            "If that picked the wrong field, re-run with *word_field* set to the field you want.",
            "",
            "<details><summary>Preview</summary>",
        ]
        + markdown_table(records, 25)
        + ["", "</details>"]
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
