#!/usr/bin/env python3
"""Delete the {{tts}} directives from your note types, once.

AnkiDroid speaks a field at review time when its template contains a {{tts}}
directive. Once a card carries real recorded audio, that synthetic voice is not
a fallback, it is a second voice talking over the first. This removes the
directives and syncs the change, so it takes effect everywhere.

Only template text changes. Fields, field order, and the note type ids are left
alone, and the job refuses to sync if it finds otherwise.

    ANKIWEB_USERNAME, ANKIWEB_PASSWORD, ANKIWEB_ENDPOINT (optional)
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from anki.collection import Collection  # noqa: E402

from add_card import sync_up  # noqa: E402
from build_tts_apkg import strip_tts  # noqa: E402
from export_deck import TTS_DIRECTIVE, fail, log, plural, sync_down, write_summary  # noqa: E402


def notetype_shape(col: Collection) -> dict[int, tuple]:
    """What must not change: the id, the field names, and the template names."""
    shape = {}
    for entry in col.models.all_names_and_ids():
        notetype = col.models.get(entry.id)
        shape[entry.id] = (
            notetype["name"],
            tuple(field["name"] for field in notetype["flds"]),
            tuple(template["name"] for template in notetype["tmpls"]),
        )
    return shape


def speaking_notetypes(col: Collection) -> list[dict]:
    found = []
    for entry in col.models.all_names_and_ids():
        notetype = col.models.get(entry.id)
        if any(TTS_DIRECTIVE.search(template[side])
               for template in notetype["tmpls"] for side in ("qfmt", "afmt")):
            found.append(notetype)
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description="Remove {{tts}} from every note type.")
    parser.add_argument("--notetype", default="", help="Only this note type (default: all).")
    parser.add_argument("--backup-dir", default="", help="Write a .colpkg before changing anything.")
    parser.add_argument("--dry-run", action="store_true", help="Report what would change.")
    parser.add_argument("--local-collection", default="", help="Use a file instead of syncing.")
    args = parser.parse_args()

    username = os.environ.get("ANKIWEB_USERNAME", "").strip()
    password = os.environ.get("ANKIWEB_PASSWORD", "")
    if not args.local_collection and (not username or not password):
        fail("ANKIWEB_USERNAME / ANKIWEB_PASSWORD are not set.")

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

    speaking = speaking_notetypes(col)
    if args.notetype:
        speaking = [n for n in speaking if n["name"] == args.notetype]
    if not speaking:
        where = f" named {args.notetype!r}" if args.notetype else ""
        log(f"No note type{where} has a {{{{tts}}}} directive. Nothing to do.")
        write_summary([f"## Nothing to remove", "",
                       f"No note type{where} speaks a field at review time."])
        col.close()
        return 0

    log("Found {{tts}} in: " + ", ".join(f"{n['name']!r}" for n in speaking))
    if args.dry_run:
        lines = [f"## {plural(len(speaking), 'note type')} would change", ""]
        for notetype in speaking:
            for template in notetype["tmpls"]:
                for side in ("qfmt", "afmt"):
                    for line in template[side].splitlines():
                        if TTS_DIRECTIVE.search(line):
                            lines.append(f"- **{notetype['name']}** / {template['name']} / "
                                         f"{side}: `{line.strip()}`")
        write_summary(lines)
        col.close()
        return 0

    # Changing a note type touches every card that uses it, so the state before
    # is worth keeping even though only template text is edited.
    if args.backup_dir:
        backup = Path(args.backup_dir)
        backup.mkdir(parents=True, exist_ok=True)
        target = backup / "before-remove-tts.colpkg"
        col.export_collection_package(str(target), False, True)
        col = Collection(str(work))
        log(f"Backup written to {target} ({target.stat().st_size // 1024}KB, no media).")

    before = notetype_shape(col)
    removed = strip_tts(col, [n["id"] for n in speaking_notetypes(col)
                              if not args.notetype or n["name"] == args.notetype])
    after = notetype_shape(col)
    if before != after:
        fail("Something other than the template text changed, so nothing was synced.",
             "Your collection on AnkiWeb is untouched.")

    still = speaking_notetypes(col)
    if args.notetype:
        still = [n for n in still if n["name"] == args.notetype]
    if still:
        fail("A {{tts}} directive survived the edit, so nothing was synced.")

    log("Removed: " + ", ".join(removed))
    if auth:
        sync_up(col, auth)
        log("Synced to AnkiWeb.")
    col.close()

    write_summary(
        [f"## Removed {plural(len(removed), 'directive')}", ""]
        + [f"- `{item}`" for item in removed]
        + ["",
           "Sync AnkiDroid and the cards will stop speaking with the built-in voice. "
           "Cards that already carry a `[sound:]` recording keep playing it.",
           "",
           "This is a one-off: new cards made from these note types will not speak either."]
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
