#!/usr/bin/env python3
"""Add one note to the collection, generate audio for its sentence, and sync it up.

The other two scripts in here only ever read from AnkiWeb. This one writes: it
adds a note and pushes it, so a card typed on a phone appears on every device
without anything to import.

It only ever adds. Before syncing it checks that every note that existed before
still exists, unchanged, and that exactly one note was created; if anything else
moved it stops without syncing. A full upload is refused the same way it is
everywhere else, so a broken run can never replace the real collection.

    ANKIWEB_USERNAME, ANKIWEB_PASSWORD, ANKIWEB_ENDPOINT (optional)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

from anki.collection import Collection  # noqa: E402
from anki.notes import NoteFieldsCheckResult  # noqa: E402
from anki.sync_pb2 import SyncCollectionResponse  # noqa: E402

from build_tts_apkg import MIN_BYTES, PROVIDERS, audio_name, speakable  # noqa: E402
from proofread import (  # noqa: E402
    apply_correction,
    check_with_languagetool,
    proofread,
    split_annotation,
)
from export_deck import (  # noqa: E402
    SOUND_TAG,
    TTS_DIRECTIVE,
    fail,
    log,
    sync_down,
    write_summary,
)

CHANGES = SyncCollectionResponse.ChangesRequired
MEDIA_SYNC_TIMEOUT = 300


def parse_field(raw: str) -> tuple[str, str]:
    """Split Name=value on the first = only, so values may contain their own."""
    name, sep, value = raw.partition("=")
    if not sep or not name.strip():
        fail(f"--field expects Name=value, got {raw!r}")
    return name.strip(), value


async def synthesize(text: str, voice: str, attempts: int, provider: str) -> bytes:
    last = ""
    for attempt in range(attempts):
        try:
            audio = await PROVIDERS[provider](text, voice)
            if len(audio) < MIN_BYTES:
                raise ValueError(f"only {len(audio)} bytes")
            return audio
        except Exception as exc:
            last = f"{type(exc).__name__}: {exc}"
            if attempt < attempts - 1:
                time.sleep(2 ** attempt)
    fail(f"Could not generate audio ({last}).", "Nothing was added and nothing was synced.")


def run_checker(checker: str, head: str, target: str):
    if checker == "claude":
        import anthropic

        return proofread([head], anthropic.Anthropic(), targets=[target])[0]
    return check_with_languagetool([head])[0]


def check_sentence(values: dict[str, str], field: str, checker: str) -> tuple[list[str], str]:
    """Correct the sentence in place, and say what changed and who changed it.

    A card is worth more than a perfect sentence, so nothing here is fatal: if
    no checker can run, the card is added exactly as typed.
    """
    raw = values.get(field, "")
    head, _ = split_annotation(raw)
    if not head.strip():
        return [], checker
    log(f"Checking the sentence with {checker}...")
    try:
        checked = run_checker(checker, head, values.get("Back", ""))
    except Exception as exc:
        log(f"::warning::{checker} could not check the sentence ({type(exc).__name__}: {exc}).")
        # A key that exists but cannot be used - no credit, expired, revoked -
        # is worse than no key at all, because its presence is what turned the
        # free checker off. So hand the sentence to the one that needs nothing.
        if checker != "languagetool":
            log("Falling back to languagetool.")
            checker = "languagetool"
            try:
                checked = run_checker(checker, head, "")
            except Exception as second:
                log(f"::warning::languagetool could not check it either "
                    f"({type(second).__name__}: {second}). Adding the sentence as typed.")
                return [], checker
        else:
            log("Adding the sentence as typed.")
            return [], checker

    updated, refused = apply_correction(raw, checked.corrected)
    if refused:
        for issue in checked.issues:
            log(f"  would change: {issue}")
        if checked.issues:
            log(f"::warning::Suggestions not applied because {refused}.")
        return [], checker
    if updated == raw:
        log("The sentence looks fine.")
        return [], checker

    values[field] = updated
    log("Corrected the sentence:")
    log(f"  was: {head.strip()}")
    log(f"  now: {checked.corrected.strip()}")
    for issue in checked.issues:
        log(f"  - {issue}")
    return (checked.issues or ["rewritten"]), checker


def existing_notes(col: Collection) -> dict[int, int]:
    return dict(col.db.all("select id, mod from notes"))


def check_add_only(col: Collection, before: dict[int, int], added: int) -> None:
    """Refuse to sync unless this run did nothing but add the one note."""
    after = existing_notes(col)
    missing = [note_id for note_id in before if note_id not in after]
    changed = [note_id for note_id, mod in before.items()
               if note_id in after and after[note_id] != mod]
    created = [note_id for note_id in after if note_id not in before]
    if missing or changed or created != [added]:
        fail(
            "This run changed more than the one new note, so nothing was synced.",
            f"{len(missing)} notes disappeared, {len(changed)} were modified, "
            f"{len(created)} were created. Your AnkiWeb collection is untouched.",
        )


def sync_up(col: Collection, auth) -> None:
    """Push the new note. A request for a full upload is refused, not obeyed."""
    out = col.sync_collection(auth, False)
    if out.new_endpoint:
        auth.endpoint = out.new_endpoint
        out = col.sync_collection(auth, False)
    if out.server_message:
        log(f"AnkiWeb says: {out.server_message}")
    if out.required == CHANGES.FULL_UPLOAD:
        fail(
            "AnkiWeb asked for a FULL UPLOAD - refusing.",
            "Uploading from here would replace your collection with this runner's copy.\n"
            "Nothing was changed on AnkiWeb. Sync your phone with AnkiWeb, then try again.",
        )
    if out.required in (CHANGES.FULL_DOWNLOAD, CHANGES.FULL_SYNC):
        fail(
            "AnkiWeb needs a full download before it will accept changes.",
            "The new note was NOT saved. This usually means another device made a schema\n"
            "change. Sync your phone with AnkiWeb, then add the card again.",
        )


def sync_up_media(col: Collection, auth, timeout: int) -> str:
    """Upload the audio, and wait for it rather than ending the job mid-transfer."""
    col.sync_media(auth)
    deadline = time.time() + timeout
    while time.time() < deadline:
        status = col.media_sync_status()
        if not status.active:
            return "done"
        time.sleep(2)
    col.abort_media_sync()
    return "timed out"


def main() -> int:
    parser = argparse.ArgumentParser(description="Add one note, with audio, and sync it.")
    parser.add_argument("--deck", required=True)
    parser.add_argument("--notetype", required=True)
    parser.add_argument("--field", action="append", default=[], metavar="NAME=VALUE",
                        help="Repeatable. Empty values are ignored.")
    parser.add_argument("--fields-json", default="",
                        help="Field values as a JSON object, for callers that do not know "
                             "the note type's field names in advance. Merged with --field.")
    parser.add_argument("--tags", default="")
    parser.add_argument("--speak-field", default="Example",
                        help="Field to generate audio for (empty = no audio).")
    parser.add_argument("--voice", default="en-US-AvaNeural")
    parser.add_argument("--provider", default="edge", choices=sorted(PROVIDERS),
                        help="edge = Microsoft neural voices; silent = offline test files.")
    parser.add_argument("--attempts", type=int, default=4)
    parser.add_argument("--no-proofread", action="store_true",
                        help="Skip the typo and phrasing check entirely.")
    parser.add_argument("--checker", default="auto",
                        choices=("auto", "claude", "languagetool"),
                        help="auto uses Claude when ANTHROPIC_API_KEY is set and "
                             "LanguageTool otherwise.")
    parser.add_argument("--allow-duplicate", action="store_true",
                        help="Add even if a note with the same first field exists.")
    parser.add_argument("--create-deck", action="store_true",
                        help="Create the deck if it does not exist, instead of failing.")
    parser.add_argument("--backup-dir", default="",
                        help="Write a .colpkg of the collection before it is changed.")
    parser.add_argument("--catalog", default="",
                        help="Write the deck and note type names here, for the web form.")
    parser.add_argument("--skip-media-sync", action="store_true")
    parser.add_argument("--local-collection", default="", help="Use a file instead of syncing.")
    args = parser.parse_args()

    username = os.environ.get("ANKIWEB_USERNAME", "").strip()
    password = os.environ.get("ANKIWEB_PASSWORD", "")
    if not args.local_collection and (not username or not password):
        fail("ANKIWEB_USERNAME / ANKIWEB_PASSWORD are not set.")

    values = dict(parse_field(raw) for raw in args.field)
    if args.fields_json.strip():
        try:
            supplied = json.loads(args.fields_json)
        except ValueError as exc:
            fail(f"--fields-json is not valid JSON: {exc}")
        if not isinstance(supplied, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in supplied.items()
        ):
            fail("--fields-json must be a JSON object of field name to text.")
        values.update(supplied)
    values = {name: value for name, value in values.items() if value.strip()}
    if not values:
        fail("No field values were given.", "Pass at least one --field Name=value.")

    if args.local_collection:
        work = Path(args.local_collection)
        col = Collection(str(work))
        auth = None
    else:
        work = Path("anki-work") / "collection.anki2"
        work.parent.mkdir(exist_ok=True)
        col = Collection(str(work))
        auth = col.sync_login(username, password, os.environ.get("ANKIWEB_ENDPOINT") or None)
        sync_down(col, username, password, os.environ.get("ANKIWEB_ENDPOINT") or None)

    # Writing a .colpkg closes the collection, so the backup is taken here and
    # the collection reopened: at this point nothing has been built on top of it
    # that reopening would invalidate.
    if args.backup_dir:
        backup = Path(args.backup_dir)
        backup.mkdir(parents=True, exist_ok=True)
        target = backup / "before-add.colpkg"
        col.export_collection_package(str(target), False, True)
        col = Collection(str(work))
        log(f"Backup written to {target} ({target.stat().st_size // 1024}KB, no media).")

    notetype = col.models.by_name(args.notetype)
    if notetype is None:
        names = ", ".join(n.name for n in col.models.all_names_and_ids())
        col.close()
        fail(f"No note type named {args.notetype!r}.", f"Available: {names}")

    field_names = [field["name"] for field in notetype["flds"]]
    unknown = [name for name in values if name not in field_names]
    if unknown:
        col.close()
        fail(f"{args.notetype!r} has no field called " + ", ".join(repr(u) for u in unknown),
             f"Its fields are: {', '.join(field_names)}")

    deck = col.decks.by_name(args.deck)
    if deck is None and not args.create_deck:
        names = ", ".join(d.name for d in col.decks.all_names_and_ids())
        col.close()
        fail(f"No deck named {args.deck!r}.", f"Available: {names}")
    deck_id = deck["id"] if deck else col.decks.id(args.deck)

    if args.catalog:
        in_use = dict(col.db.all("select mid, count() from notes group by mid"))
        Path(args.catalog).parent.mkdir(parents=True, exist_ok=True)
        Path(args.catalog).write_text(json.dumps({
            "decks": sorted(d.name for d in col.decks.all_names_and_ids()
                            if d.name.lower() != "default"),
            # Most-used first. A collection carries Anki's stock note types
            # whether or not anyone uses them, and sorted by name "Basic" wins -
            # which would open the form on a note type with no Example field,
            # and so no audio.
            "notetypes": [
                {"name": name, "fields": fields, "notes": count}
                for name, fields, count in sorted(
                    (
                        (
                            entry.name,
                            [f["name"] for f in col.models.get(entry.id)["flds"]],
                            in_use.get(entry.id, 0),
                        )
                        for entry in col.models.all_names_and_ids()
                    ),
                    key=lambda row: (-row[2], row[0]),
                )
            ],
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    corrections: list[str] = []
    checker = ""
    if not args.no_proofread:
        checker = args.checker
        if checker == "auto":
            checker = "claude" if os.environ.get("ANTHROPIC_API_KEY", "").strip() else "languagetool"
        corrections, checker = check_sentence(values, args.speak_field, checker)

    note = col.new_note(notetype)
    for name, value in values.items():
        note[name] = value
    note.tags = args.tags.split()

    state = note.duplicate_or_empty()
    if state == NoteFieldsCheckResult.EMPTY:
        col.close()
        fail("The first field is empty, so Anki would not create a card.")
    if state == NoteFieldsCheckResult.DUPLICATE and not args.allow_duplicate:
        col.close()
        fail(f"A {args.notetype!r} note with this first field already exists.",
             "Nothing was added. Tick 'allow duplicate' if you meant to add it anyway.")

    spoken = ""
    stored = ""
    if args.speak_field and args.speak_field in values:
        raw = values[args.speak_field]
        if SOUND_TAG.search(raw):
            log("The field already references audio; not generating any.")
        else:
            spoken = speakable(raw)
            if spoken:
                log(f"Generating audio for {len(spoken)} characters with {args.voice}...")
                audio = asyncio.run(synthesize(spoken, args.voice, args.attempts, args.provider))
                name = audio_name(spoken)
                scratch = Path("anki-work") / name
                scratch.parent.mkdir(exist_ok=True)
                scratch.write_bytes(audio)
                stored = col.media.add_file(str(scratch))
                note[args.speak_field] = raw + f" [sound:{stored}]"
                log(f"  {stored}, {len(audio)} bytes")

    speaks = [t for t in notetype["tmpls"]
              if any(TTS_DIRECTIVE.search(t[side]) for side in ("qfmt", "afmt"))]
    if stored and speaks:
        log(f"::warning::{args.notetype!r} still has a {{{{tts}}}} directive, so this card will "
            "play the recording and the synthetic voice. Remove it under Cards -> Back template.")

    before = existing_notes(col)
    cards = col.add_note(note, deck_id)
    check_add_only(col, before, note.id)
    log(f"Added note {note.id} to {args.deck!r} ({cards.count if hasattr(cards, 'count') else ''}).")

    media_state = "not synced"
    if auth:
        sync_up(col, auth)
        log("Collection synced to AnkiWeb.")
        if stored and not args.skip_media_sync:
            media_state = sync_up_media(col, auth, MEDIA_SYNC_TIMEOUT)
            log(f"Media sync {media_state}.")
    col.close()

    first = next(iter(values.values()))
    write_summary(
        [
            f"## Added to {args.deck}",
            "",
            f"- **{first[:80]}**",
            f"- Note type: `{args.notetype}` · tags: `{args.tags or 'none'}`",
        ]
        + [f"- `{name}`: {value[:120]}" for name, value in values.items()]
        + ([f"- Audio: `{stored}` for {len(spoken)} characters, media sync **{media_state}**"]
           if stored else ["- No audio was generated."])
        + ([f"", f"### The sentence was corrected — by `{checker}`", ""]
           + [f"- {issue}" for issue in corrections]
           if corrections else [])
        + ([f"- ::warning:: `{args.notetype}` still contains `{{{{tts}}}}`; the card will speak twice."]
           if stored and speaks else [])
        + [
            "",
            "Sync AnkiDroid to see it. If the audio does not play straight away, sync again - "
            "media transfers separately from the cards.",
        ]
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
