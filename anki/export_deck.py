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

# [sound:file.mp3] references inside a field. clean() strips these out of the
# readable columns, so they are collected from the raw text first - otherwise
# an export cannot tell a field that already has audio from one that does not.
SOUND_TAG = re.compile(r"\[sound:([^]]*)\]")

# {{tts en_US:Example}} and friends. A note type that speaks a field at review
# time has one of these in a template; a pre-generated [sound:] tag replaces it.
TTS_DIRECTIVE = re.compile(r"\{\{[^}]*\btts\b[^}]*\}\}")


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
    text = SOUND_TAG.sub(" ", text)
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

def field_names(col: Collection, note_ids: list[int], sample: int = 200) -> set[str]:
    """Every field name in play, for saying what is there when a name is wrong."""
    names = set()
    for note_id in note_ids[:sample]:
        names.update(name for name, _ in col.get_note(note_id).items())
    return names


def insist_on_fields(col: Collection, note_ids: list[int], *wanted: str) -> None:
    """Stop if a field name matches nothing, rather than reporting an empty deck.

    Getting the name wrong is the likeliest way to run one of these jobs for
    nothing: every note is skipped, every count comes out zero, and the report
    reads as a collection with nothing wrong in it. An answer of zero that
    means "not looked at" and reads as "looked at, and fine" is the worse of
    the two wrong answers, because nobody investigates good news.
    """
    have = field_names(col, note_ids)
    astray = [name for name in wanted if name and name not in have]
    if not astray:
        return
    fail(
        f"No note has a field called {', '.join(repr(n) for n in astray)}.",
        "Nothing was checked. Field names are case-sensitive and this would "
        "otherwise have reported a collection with nothing wrong in it.\n"
        "The fields your notes actually have: " + ", ".join(sorted(have)),
    )


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

    # Anything already referencing audio. Collected before clean() runs, because
    # clean() removes [sound:] tags: without this the export looks identical
    # whether a note has audio or not.
    sounds = [ref for value in fields.values() for ref in SOUND_TAG.findall(value)]

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
        "word_field": "",  # which field it came from, so the column explains itself
        "deck": stats.deck or col.decks.name(card.did),
        # odid is set only while a card sits in a filtered deck, and then holds
        # the deck it will return to - which is the one worth reporting.
        "deck_id": card.odid or card.did,
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
        "sound_tags": " ".join(sounds),
        "notetype": stats.notetype,
        "notetype_id": notetype.get("id", ""),
        "card_template": stats.card_type,
        "deck_preset": stats.preset,
        # Anki matches notes on guid when importing an .apkg: a match updates the
        # existing note and leaves its cards' scheduling alone, a mismatch adds a
        # duplicate. Anything that rebuilds these notes has to carry it through.
        "guid": note.guid,
        "note_id": note.id,
        "card_id": card_id,
        "_sort_field": sort_field,
        "_fields": {name: clean(value) for name, value in fields.items()},
        # Exactly as stored, HTML and media refs intact. The readable columns are
        # for people; anything writing fields back has to start from these.
        "_fields_raw": dict(fields),
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


def reconcile_word_field(
    by_notetype: dict[str, list[dict]],
    fields_of: dict[str, list[str]],
    chosen_of: dict[str, str],
) -> str | None:
    """Find one field every note type can put in the word column, or None.

    Detection runs per note type, so a mixed export can end up with the prompt
    of one note type and the answer of another stacked in the same column, with
    nothing on the row to say which is which. Among the fields that were picked
    and that every note type has, prefer the one that behaves the *same* in each
    of them: a field holding a word here and a sentence there is not one column.
    """
    if len(set(chosen_of.values())) < 2:
        return None  # they already agree

    common = set.intersection(*(set(names) for names in fields_of.values()))
    candidates = [name for name in dict.fromkeys(chosen_of.values()) if name in common]
    if len(candidates) < 2:
        return candidates[0] if candidates else None

    # A field actually named like a vocabulary field beats any measurement of
    # how long its contents are, exactly as it does per note type.
    named = [name for name in candidates if VOCAB_FIELD_NAME.search(name)]
    if named:
        return named[0]

    scored: dict[str, tuple[float, float]] = {}
    for name in candidates:
        per_notetype = []
        for group in by_notetype.values():
            length = _median_length([r["_fields"].get(name, "") for r in group])
            if length is None:
                break  # empty for a whole note type, so it cannot be the word
            per_notetype.append(length)
        else:
            spread = max(per_notetype) - min(per_notetype)
            scored[name] = (spread, max(per_notetype))
    if not scored:
        return None
    return min(scored, key=lambda name: scored[name] + (candidates.index(name),))


def assign_word_column(records: list[dict], override: str | None) -> list[str]:
    """Fill the "word" column for every card and report how it was decided."""
    by_notetype: dict[str, list[dict]] = {}
    for record in records:
        by_notetype.setdefault(record["notetype"], []).append(record)

    fields_of: dict[str, list[str]] = {}
    samples_of: dict[str, dict[str, list[str]]] = {}
    for notetype, group in by_notetype.items():
        names: list[str] = []
        for record in group:
            for name in record["_fields"]:
                if name not in names:
                    names.append(name)
        fields_of[notetype] = names
        samples_of[notetype] = {name: [r["_fields"].get(name, "") for r in group] for name in names}

    chosen_of: dict[str, str] = {}
    why_of: dict[str, str] = {}
    override_used = False
    for notetype, group in by_notetype.items():
        names = fields_of[notetype]
        if override and override in names:
            chosen_of[notetype], why_of[notetype] = override, "you set the word_field input"
            override_used = True
            continue
        if override:
            log(f"::warning::Note type {notetype!r} has no field named {override!r}; detecting instead.")
        chosen_of[notetype], why_of[notetype] = choose_word_field(
            names, samples_of[notetype], group[0]["_sort_field"]
        )

    if not override_used:
        agreed = reconcile_word_field(by_notetype, fields_of, chosen_of)
        if agreed:
            for notetype, previous in list(chosen_of.items()):
                if previous != agreed:
                    log(
                        f"::warning::Note type {notetype!r} looked like it wanted {previous!r} in the "
                        f"word column; using {agreed!r} instead, so the column means the same thing "
                        f"on every row. Set word_field to override."
                    )
                chosen_of[notetype] = agreed
                why_of[notetype] = (
                    f"every note type here has a `{agreed}` field and it holds the same kind of "
                    "text in each of them"
                )

    report: list[str] = []
    for notetype, group in sorted(by_notetype.items()):
        chosen, why = chosen_of[notetype], why_of[notetype]
        field_names = fields_of[notetype]

        for record in group:
            value = record["_fields"].get(chosen, "")
            # An empty field on one note still needs something in the column.
            record["word_field"] = chosen if value else record["_sort_field"]
            record["word"] = value or record["_fields"].get(record["_sort_field"], "")

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
# note type metadata
# --------------------------------------------------------------------------

def tts_directives(templates: list[dict]) -> list[dict]:
    """Every {{tts ...}} in the templates, with the template and line it sits on."""
    found = []
    for template in templates:
        for side in ("qfmt", "afmt"):
            for number, line in enumerate(template[side].splitlines(), start=1):
                if TTS_DIRECTIVE.search(line):
                    found.append(
                        {
                            "template": template["name"],
                            "side": side,
                            "line_number": number,
                            "line": line.strip(),
                        }
                    )
    return found


def notetype_records(col: Collection, notetype_ids: list[int]) -> list[dict]:
    """Enough of each note type to rebuild it without forking it.

    Anki treats a rebuilt note type as the same one only when the id, fields,
    templates and CSS all match; anything else imports as a copy and drags the
    notes into it. So these are taken verbatim, and the stored dict is kept
    whole under `raw` rather than trusting this function to know what matters.
    """
    out = []
    for notetype_id in notetype_ids:
        notetype = col.models.get(notetype_id)
        if notetype is None:
            log(f"::warning::Note type id {notetype_id} is used by a card but is not in the collection.")
            continue
        templates = [
            {
                "ord": template.get("ord"),
                "name": template.get("name", ""),
                "qfmt": template.get("qfmt", ""),
                "afmt": template.get("afmt", ""),
            }
            for template in notetype.get("tmpls", [])
        ]
        out.append(
            {
                "id": notetype["id"],
                "name": notetype["name"],
                "kind": "cloze" if notetype.get("type") else "standard",
                "sort_field_index": notetype.get("sortf", 0),
                "field_names": [field["name"] for field in notetype.get("flds", [])],
                "templates": templates,
                "css": notetype.get("css", ""),
                "tts_directives": tts_directives(templates),
                "raw": notetype,
            }
        )
    return sorted(out, key=lambda notetype: notetype["name"])


def write_notetypes(out_dir: Path, notetypes: list[dict]) -> None:
    (out_dir / "notetypes.json").write_text(
        json.dumps(notetypes, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    folder = out_dir / "notetypes"
    folder.mkdir(parents=True, exist_ok=True)
    for notetype in notetypes:
        (folder / f"{safe_filename(notetype['name'])}.json").write_text(
            json.dumps(notetype, ensure_ascii=False, indent=2), encoding="utf-8"
        )


def notetype_report(notetypes: list[dict], records: list[dict]) -> list[str]:
    """Per note type: the fields it really has, its templates, and any {{tts}}.

    `cards.csv` holds the union of every field name seen across the export, so a
    `field: X` column there is no evidence that this note type has an X field.
    This says what each note type actually declares, and which of those fields
    are blank on every note exported - a different thing from not existing.
    """
    by_id: dict[int, list[dict]] = {}
    for record in records:
        by_id.setdefault(record["notetype_id"], []).append(record)

    lines = []
    for notetype in notetypes:
        group = by_id.get(notetype["id"], [])
        annotated = []
        for name in notetype["field_names"]:
            filled = sum(1 for record in group if record["_fields"].get(name))
            annotated.append(f"`{name}`" if filled else f"`{name}` _(blank on all {len(group)})_")
        directives = notetype["tts_directives"]
        spoken = (
            "; ".join(
                f"{d['template']} / {d['side']} line {d['line_number']} — `{d['line']}`"
                for d in directives
            )
            if directives
            else "none"
        )
        lines.append(
            f"- **{notetype['name']}** — id `{notetype['id']}`, {len(group)} cards\n"
            f"  - fields ({len(notetype['field_names'])}): {', '.join(annotated)}\n"
            f"  - templates ({len(notetype['templates'])}): "
            + ", ".join(f"`{t['name']}`" for t in notetype["templates"])
            + "\n  - "
            + "`{{tts}}`: "
            + spoken
        )
    return lines


def sound_report(records: list[dict]) -> list[str]:
    """Which notes already reference audio, per field.

    Worth knowing before anything appends a `[sound:]` tag. The readable columns
    have these stripped out, so their absence from `cards.csv` means nothing.
    """
    per_field: dict[str, set] = {}
    for record in records:
        for name, value in record["_fields_raw"].items():
            if SOUND_TAG.search(value):
                per_field.setdefault(name, set()).add(record["note_id"])
    if not per_field:
        return ["No note in this export references audio yet."]

    notes = set().union(*per_field.values())
    return [f"**{plural(len(notes), 'note')}** already reference audio:", ""] + [
        f"- `{name}` — {plural(len(ids), 'note')}" for name, ids in sorted(per_field.items())
    ]


# --------------------------------------------------------------------------
# output
# --------------------------------------------------------------------------

def build_table(records: list[dict]) -> tuple[list[str], list[dict]]:
    """Flatten the records into a header and plain rows, fields last."""
    field_names: list[str] = []
    for record in records:
        for name in record["_fields"]:
            if name not in field_names:
                field_names.append(name)

    base = [k for k in records[0] if not k.startswith("_")] if records else []
    header = base + [f"field: {name}" for name in field_names]

    rows = []
    for record in records:
        row = {k: v for k, v in record.items() if not k.startswith("_")}
        for name in field_names:
            row[f"field: {name}"] = record["_fields"].get(name, "")
        rows.append(row)
    return header, rows


def write_csv(path: Path, header: list[str], rows: list[dict]) -> None:
    # utf-8-sig so Excel detects UTF-8; the csv module quotes and uses CRLF.
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=header, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_cards_csv(path: Path, records: list[dict]) -> list[str]:
    header, rows = build_table(records)
    write_csv(path, header, rows)
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


REVIEW_COLUMNS = [
    "word", "card_id", "reviewed_at", "rating", "rating_name", "kind",
    "interval_days", "previous_interval_days", "ease_pct", "seconds_taken",
    "fsrs_stability_days", "fsrs_difficulty",
]


def build_review_table(records: list[dict]) -> list[dict]:
    rows = []
    for record in records:
        for review in reversed(record["_reviews"]):  # oldest first, reads naturally
            rows.append({"word": record["word"], "card_id": record["card_id"], **review})
    return rows


def write_reviews_csv(path: Path, rows: list[dict]) -> int:
    write_csv(path, REVIEW_COLUMNS, rows)
    return len(rows)


def write_workbook(path: Path, cards: tuple[list[str], list[dict]], reviews: list[dict]) -> bool:
    """Write a real .xlsx. Spreadsheet apps open it without guessing an encoding."""
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font
        from openpyxl.utils import get_column_letter
    except ImportError:
        log("::warning::openpyxl is not installed, so no Excel file was written.")
        return False

    book = Workbook()
    for index, (title, header, rows) in enumerate(
        [("Cards", cards[0], cards[1]), ("Reviews", REVIEW_COLUMNS, reviews)]
    ):
        sheet = book.active if index == 0 else book.create_sheet()
        sheet.title = title
        sheet.append(header)
        for cell in sheet[1]:
            cell.font = Font(bold=True)
        for row in rows:
            values = [row.get(name, "") for name in header]
            sheet.append(values)
            for cell, value in zip(sheet[sheet.max_row], values):
                # A leading "=" would otherwise be stored as a formula.
                if isinstance(value, str) and value[:1] in ("=", "+", "-", "@"):
                    cell.data_type = "s"

        sheet.freeze_panes = "A2"
        if rows:
            sheet.auto_filter.ref = f"A1:{get_column_letter(len(header))}{len(rows) + 1}"
        for column, name in enumerate(header, start=1):
            longest = max([len(str(row.get(name, ""))) for row in rows[:200]] + [len(name)])
            sheet.column_dimensions[get_column_letter(column)].width = min(max(longest + 2, 9), 55)

    book.save(path)
    return True


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
        source = f"local collection file `{args.local_collection}`"
        col = Collection(args.local_collection)
    else:
        source = "AnkiWeb sync, downloaded with the official `anki` library"
        work_dir = Path("anki-work")
        work_dir.mkdir(exist_ok=True)
        col = Collection(str(work_dir / "collection.anki2"))
    log(f"Collection source: {source}")

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

        notetype_ids = list(dict.fromkeys(r["notetype_id"] for r in records if r["notetype_id"]))
        notetypes = notetype_records(col, notetype_ids)
        write_notetypes(out_dir, notetypes)
        log(f"Exported {len(notetypes)} note types.")

        table = build_table(records)
        write_csv(out_dir / "cards.csv", *table)
        review_table = build_review_table(records)
        review_rows = write_reviews_csv(out_dir / "reviews.csv", review_table)
        wrote_xlsx = write_workbook(out_dir / "anki-export.xlsx", table, review_table)
        by_deck = write_per_deck_csvs(out_dir, records)
        payload = {
            "exported_at": clock.stamp(int(datetime.now(tz=timezone.utc).timestamp())),
            "timezone": clock.name,
            "collection_source": source,
            "query": query,
            "card_count": len(records),
            "notetypes": notetypes,
            "cards": [
                {
                    **{k: v for k, v in r.items() if not k.startswith("_")},
                    "fields": r["_fields"],
                    # As stored, HTML and [sound:] tags intact - what anything
                    # writing fields back has to start from.
                    "fields_raw": r["_fields_raw"],
                    "reviews": r["_reviews"],
                }
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
            "Download **anki-export** under *Artifacts* below. Unzip it, then open "
            + ("**anki-export.xlsx**" if wrote_xlsx else "`cards.csv`")
            + " — it also contains `cards.csv`, `reviews.csv` and `cards.json`"
            + (", plus one CSV per deck under `by-deck/`." if len(by_deck) > 1 else "."),
            "",
        ]
        + (deck_breakdown(by_deck) if len(by_deck) > 1 else [])
        + [
            "",
            "### Note types",
            "",
        ]
        + notetype_report(notetypes, records)
        + [
            "",
            "Full definitions - field order, every template, the CSS - are in "
            "`notetypes.json` and one file per note type under `notetypes/`.",
            "",
            "### Audio already on these notes",
            "",
        ]
        + sound_report(records)
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
