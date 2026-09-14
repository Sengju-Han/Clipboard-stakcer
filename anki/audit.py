#!/usr/bin/env python3
"""Check every sentence already in the collection, and report what is wrong.

Cards made months ago carry the mistakes of the day they were typed, and a
flashcard reviewed a hundred times teaches its mistake a hundred times. This
reads the whole collection, checks each sentence, and writes a report.

It changes nothing unless asked. --apply writes the corrections back and syncs;
without it this is entirely read-only and safe to run whenever.

    ANKIWEB_USERNAME, ANKIWEB_PASSWORD, ANTHROPIC_API_KEY (for --checker claude)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from anki.collection import Collection  # noqa: E402

from add_card import existing_notes, sync_up  # noqa: E402
from export_deck import (  # noqa: E402
    SOUND_TAG,
    build_deck_query,
    deck_inventory,
    fail,
    log,
    parse_deck_list,
    plural,
    sync_down,
    write_summary,
)
from proofread import (  # noqa: E402
    apply_correction,
    client,
    check_with_languagetool,
    has_markup,
    proofread,
    split_annotation,
)

# Sentences per request. One system prompt covers the batch, so bigger batches
# cost less; too big and a single failure loses more work than it saves.
BATCH = 20

# The free LanguageTool endpoint is rate limited and this sends a thousand
# sentences at it, so it goes slowly on purpose.
LANGUAGETOOL_PAUSE = 3.0


def collect(col: Collection, note_ids: list[int], field: str) -> tuple[list[dict], dict]:
    """The sentences worth checking, and a count of why the rest were not."""
    items, skipped = [], {"no field": 0, "empty": 0, "has formatting": 0}
    for note_id in note_ids:
        note = col.get_note(note_id)
        if field not in note:
            skipped["no field"] += 1
            continue
        raw = note[field]
        head, _ = split_annotation(raw)
        if not head.strip():
            skipped["empty"] += 1
            continue
        if has_markup(head):
            # A correction comes back as plain text; writing it over formatting
            # would silently throw the formatting away.
            skipped["has formatting"] += 1
            continue
        items.append({
            "note_id": note_id,
            "guid": note.guid,
            "deck": col.decks.name(col.get_card(note.card_ids()[0]).did),
            "target": note["Back"] if "Back" in note else "",
            "text": head.strip(),
        })
    return items, skipped


def already_done(path: Path) -> dict[int, dict]:
    """What a previous run got through, so a second one picks up where it left off."""
    done = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
                done[row["note_id"]] = row
            except Exception:
                continue
    return done


def check_batch(batch: list[dict], checker: str, client) -> list:
    if checker == "claude":
        return proofread([i["text"] for i in batch], client, targets=[i["target"] for i in batch])
    results = check_with_languagetool([i["text"] for i in batch])
    time.sleep(LANGUAGETOOL_PAUSE)
    return results


def write_report(rows: list[dict], skipped: dict, checked: int, out_dir: Path, applied: bool) -> None:
    changed = [r for r in rows if r["issues"]]
    by_deck: dict[str, list[dict]] = {}
    for row in changed:
        by_deck.setdefault(row["deck"], []).append(row)

    lines = [
        f"## {plural(checked, 'sentence')} checked — {plural(len(changed), 'needs work')}"
        if not applied else
        f"## {plural(len(changed), 'sentence')} corrected, out of {checked} checked",
        "",
        f"- Clean: **{checked - len(changed)}**",
    ] + [f"- Skipped, {reason}: **{count}**" for reason, count in skipped.items() if count]

    shown = 0
    for deck, entries in sorted(by_deck.items(), key=lambda kv: -len(kv[1])):
        lines += ["", f"### {deck} — {plural(len(entries), 'sentence')}", ""]
        for row in entries:
            if shown >= 120:
                break
            lines += [
                f"- ~~{row['was']}~~",
                f"  **{row['now']}**",
                "  " + "; ".join(row["issues"]),
            ]
            shown += 1
    if len(changed) > shown:
        lines += ["", f"_...and {len(changed) - shown} more. The full list is in "
                      "`audit.jsonl` under Artifacts._"]
    if not applied and changed:
        lines += ["", "Nothing has been changed. Re-run with **apply** ticked to write these "
                      "back and sync them."]
    write_summary(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Check the sentences already in the collection.")
    parser.add_argument("--deck", default="*")
    parser.add_argument("--field", default="Example")
    parser.add_argument("--checker", default="claude", choices=("claude", "languagetool"))
    parser.add_argument("--out-dir", default="audit")
    parser.add_argument("--limit", type=int, default=0, help="Only the first N sentences.")
    parser.add_argument("--batch", type=int, default=BATCH)
    parser.add_argument("--apply", action="store_true", help="Write the corrections back and sync.")
    parser.add_argument("--local-collection", default="")
    args = parser.parse_args()

    username = os.environ.get("ANKIWEB_USERNAME", "").strip()
    password = os.environ.get("ANKIWEB_PASSWORD", "")
    if not args.local_collection and (not username or not password):
        fail("ANKIWEB_USERNAME / ANKIWEB_PASSWORD are not set.")
    if args.checker == "claude" and not os.environ.get("ANTHROPIC_API_KEY", "").strip():
        fail("ANTHROPIC_API_KEY is not set.",
             "Add the secret, or run with checker set to languagetool.")

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    ledger = out_dir / "audit.jsonl"

    if args.local_collection:
        work = Path(args.local_collection)
        col, auth = Collection(str(work)), None
    else:
        work = Path("anki-work") / "collection.anki2"
        work.parent.mkdir(exist_ok=True)
        col = Collection(str(work))
        auth = col.sync_login(username, password, os.environ.get("ANKIWEB_ENDPOINT") or None)
        sync_down(col, username, password, os.environ.get("ANKIWEB_ENDPOINT") or None)

    # Writing a .colpkg closes the collection, so it happens before any work and
    # the collection is reopened. Taken whenever a write is possible, not after.
    if args.apply:
        backup = out_dir / "before-audit.colpkg"
        col.export_collection_package(str(backup), False, True)
        col = Collection(str(work))
        log(f"Backup written to {backup} ({backup.stat().st_size // 1024}KB, no media).")

    decks = deck_inventory(col)
    query = build_deck_query(parse_deck_list(args.deck), decks)
    note_ids = list(col.find_notes(query))
    items, skipped = collect(col, note_ids, args.field)
    if args.limit:
        items = items[: args.limit]

    done = already_done(ledger)
    pending = [i for i in items if i["note_id"] not in done]
    log(f"{len(note_ids)} notes, {len(items)} sentences to check, "
        f"{len(done)} already done, {len(pending)} to go.")
    for reason, count in skipped.items():
        if count:
            log(f"  skipped, {reason}: {count}")

    checker_client = None
    if args.checker == "claude" and pending:
        checker_client = client()

    for start in range(0, len(pending), args.batch):
        batch = pending[start : start + args.batch]
        try:
            results = check_batch(batch, args.checker, checker_client)
        except Exception as exc:
            log(f"::warning::Batch starting at {start} failed ({type(exc).__name__}: {exc}). "
                "Stopping here; re-run to carry on from this point.")
            break
        with ledger.open("a", encoding="utf-8") as handle:
            for item, result in zip(batch, results):
                corrected = (result.corrected or "").strip()
                row = {
                    "note_id": item["note_id"], "guid": item["guid"], "deck": item["deck"],
                    "was": item["text"], "now": corrected,
                    "issues": list(result.issues) if corrected and corrected != item["text"] else [],
                }
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        log(f"  {min(start + len(batch), len(pending))}/{len(pending)}")

    rows = list(already_done(ledger).values())
    changed = [r for r in rows if r["issues"]]
    log(f"{plural(len(changed), 'sentence')} would change.")

    if args.apply and changed:
        applied = apply_all(col, changed, args.field)
        log(f"Applied {plural(applied, 'correction')}.")
        if auth:
            sync_up(col, auth)
            log("Synced to AnkiWeb.")

    col.close()
    write_report(rows, skipped, len(rows), out_dir, bool(args.apply and changed))
    return 0


def apply_all(col: Collection, changed: list[dict], field: str) -> int:
    """Write the corrections back, and refuse to sync if anything else moved."""
    before_notes = existing_notes(col)
    before_cards = {row[0]: row for row in col.db.all(
        "select id, nid, did, ord, type, queue, due, ivl, factor, reps, lapses from cards")}

    touched, stale_audio = [], 0
    for row in changed:
        note_id = col.db.scalar("select id from notes where guid = ?", row["guid"])
        if not note_id:
            continue
        note = col.get_note(note_id)
        if field not in note:
            continue
        updated, refused = apply_correction(note[field], row["now"])
        if refused or updated == note[field]:
            continue
        # The recording was made from the old wording, so it now says something
        # the card no longer does. Drop it and let the audio workflow remake it.
        if SOUND_TAG.search(updated):
            updated = SOUND_TAG.sub("", updated).rstrip()
            stale_audio += 1
        note[field] = updated
        col.update_note(note)
        touched.append(note_id)

    after_notes = existing_notes(col)
    if set(after_notes) != set(before_notes):
        fail("The note list changed during the audit, so nothing was synced.")
    moved = [n for n, mod in before_notes.items()
             if after_notes[n] != mod and n not in touched]
    if moved:
        fail(f"{len(moved)} notes changed that should not have. Nothing was synced.")
    after_cards = {row[0]: row for row in col.db.all(
        "select id, nid, did, ord, type, queue, due, ivl, factor, reps, lapses from cards")}
    if after_cards != before_cards:
        fail("Card scheduling moved during the audit, so nothing was synced.")

    if stale_audio:
        log(f"::warning::{plural(stale_audio, 'card')} had audio of the old wording. The tag was "
            "removed; re-run the TTS package workflow to record the corrected sentence.")
    return len(touched)


if __name__ == "__main__":
    sys.exit(main())
