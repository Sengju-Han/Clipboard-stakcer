#!/usr/bin/env python3
"""Pre-generate audio for one field and package it as an .apkg that updates notes in place.

Replaces AnkiDroid's runtime {{tts}} with real neural audio stored as
[sound:...] tags. Anki matches notes on guid, so importing the result updates
the fields of notes that already exist and leaves their cards alone - review
history, due dates and FSRS state are not touched.

Like export_deck.py this only ever downloads from AnkiWeb. The collection on the
runner is a throwaway copy; it is modified, exported, and thrown away, and it is
never uploaded.

    ANKIWEB_USERNAME, ANKIWEB_PASSWORD, ANKIWEB_ENDPOINT (optional)
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import html
import json
import os
import re
import shutil
import sys
import unicodedata
import zipfile
from pathlib import Path

from anki.collection import Collection, NoteIdsLimit  # noqa: E402
from anki.import_export_pb2 import (  # noqa: E402
    ExportAnkiPackageOptions,
    ImportAnkiPackageOptions,
    ImportAnkiPackageRequest,
)

from export_deck import (  # noqa: E402
    SOUND_TAG,
    TTS_DIRECTIVE,
    build_deck_query,
    deck_inventory,
    fail,
    log,
    parse_deck_list,
    plural,
    sync_down,
    write_summary,
)

# A field often holds the sentence first and the user's own gloss after a block
# break. Only the first segment is worth speaking: the rest is notes to self,
# frequently in another language, and a voice reading it aloud is noise.
BLOCK_BOUNDARY = re.compile(r"<\s*(?:div|br|p|li|tr|h[1-6])\b[^>]*>", re.IGNORECASE)

# Anki treats a leading underscore as "template asset, leave alone" in Check
# Media. These are ordinary media files and should be swept like any other.
NAME_PREFIX = "ttsex-"

# A [sound:] tag pointing at a file this script made, with the space that was
# put in front of it. Used to tell our own recording from one somebody made
# themselves, which must never be thrown away to make room for a synthetic one.
OUR_SOUND = re.compile(r"\s*\[sound:(" + re.escape(NAME_PREFIX) + r"[^\]]*)\]")

# A truncated or empty mp3 is the failure that hides: it imports fine and is
# only discovered mid-review. Neural speech never lands this small.
MIN_BYTES = 2048

# One silent MPEG-1 Layer III frame, 128kbps 44.1kHz: 1152 samples, 26.1ms.
# Used by the offline provider so the pipeline can be exercised without network.
SILENT_FRAME = b"\xff\xfb\x90\x00" + b"\x00" * 413
FRAME_MS = 1152 / 44.1

# Rough speaking rate for English neural voices, used only to sanity-check that
# a file's length is plausible for its text.
MS_PER_CHAR = 65


# --------------------------------------------------------------------------
# text to speak, and the name of the file holding it
# --------------------------------------------------------------------------

def _cleaned(part: str) -> str:
    text = SOUND_TAG.sub(" ", part)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = unicodedata.normalize("NFC", text).replace("\xa0", " ")
    return re.sub(r"\s+", " ", text).strip()


def speakable(raw: str) -> str:
    """The part of a field worth sending to a speech engine.

    The first block with anything in it, with markup, entities and any existing
    media reference removed and whitespace collapsed. The result is both what
    gets spoken and what the filename hashes, so the same sentence always maps
    to the same file.

    The first block *with anything in it*, rather than simply the first: Anki's
    editor writes a multi-line field as <div>line</div><div>line</div>, so the
    text before the first break is the empty string and taking it gave nothing
    to speak. Two fields in a 1,177-card collection start that way, and they
    would have been skipped with no explanation.
    """
    for part in BLOCK_BOUNDARY.split(raw):
        text = _cleaned(part)
        if text:
            return text
    return ""


def audio_name(text: str, voice: str = "") -> str:
    """Content-addressed filename, so a re-run skips what is already generated.

    The voice is part of the content. Without it the same sentence read by two
    different voices wants the same filename, and the second reading can never
    be made: the file is already there, so it is skipped as done. That is what
    made changing a voice impossible.

    An empty voice hashes the text alone, which is what the files generated
    before voices were named do. They are still recognised by their prefix, so
    a re-voicing run can find and replace them; it simply cannot tell which
    voice they were, and regenerates them once.
    """
    seed = f"{voice}\n{text}" if voice else text
    return NAME_PREFIX + hashlib.sha1(seed.encode("utf-8")).hexdigest()[:16] + ".mp3"


def duration_seconds(path: Path) -> float | None:
    try:
        from mutagen.mp3 import MP3

        return round(MP3(str(path)).info.length, 2)
    except Exception:
        return None


# --------------------------------------------------------------------------
# providers
# --------------------------------------------------------------------------

async def edge_audio(text: str, voice: str) -> bytes:
    """Microsoft Edge's neural voices. No account, no key, no billing.

    Unofficial and reverse-engineered: fine for a personal batch, not something
    to depend on. If it ever stops working, switch providers rather than retry.
    """
    import edge_tts

    audio = bytearray()
    async for chunk in edge_tts.Communicate(text, voice).stream():
        if chunk["type"] == "audio":
            audio += chunk["data"]
    return bytes(audio)


async def silent_audio(text: str, voice: str) -> bytes:
    """Valid, silent mp3 of a plausible length. For testing the pipeline offline."""
    frames = max(1, round(len(text) * MS_PER_CHAR / FRAME_MS))
    return SILENT_FRAME * frames


PROVIDERS = {"edge": edge_audio, "silent": silent_audio}


# --------------------------------------------------------------------------
# planning
# --------------------------------------------------------------------------

def plan_notes(
    col: Collection, note_ids: list[int], field: str,
    voice: str = "", revoice: bool = False,
) -> tuple[list[dict], dict]:
    """Work out which notes need audio, and why the others do not.

    Skipping a note that already has a [sound:] tag is what makes re-running
    safe: a second pass over the same deck is a no-op rather than a note with
    two play buttons.

    With revoice, a note that already has a tag is re-recorded instead of
    skipped — but only when the tag points at a file this script made. A
    recording somebody made themselves is the one thing here that cannot be
    regenerated, so a note holding one is left exactly as it is.
    """
    items: list[dict] = []
    skipped = {
        "no field": 0, "field empty": 0, "already has audio": 0,
        "already in this voice": 0, "has a recording we did not make": 0,
    }

    for note_id in note_ids:
        note = col.get_note(note_id)
        if field not in note:
            skipped["no field"] += 1
            continue
        raw = note[field]
        replaces = ""
        if "[sound:" in raw:
            if not revoice:
                skipped["already has audio"] += 1
                continue
            ours = OUR_SOUND.findall(raw)
            # Not `if not ours`: a field can hold both, and dropping ours would
            # leave the hand-made one playing next to a new synthetic one.
            if len(ours) != 1 or "[sound:" in OUR_SOUND.sub("", raw):
                skipped["has a recording we did not make"] += 1
                continue
            replaces = ours[0]
        text = speakable(raw)
        if not text:
            skipped["field empty"] += 1
            continue
        wanted = audio_name(text, voice)
        if replaces == wanted:
            skipped["already in this voice"] += 1
            continue
        items.append(
            {
                "note_id": note_id,
                "guid": note.guid,
                "text": text,
                "chars": len(text),
                "file": wanted,
                "replaces": replaces,
            }
        )
    return items, skipped


def snapshot(col: Collection, note_ids: list[int]) -> dict:
    """Field values and card scheduling before anything is touched, to diff against."""
    fields = {}
    for note_id in note_ids:
        note = col.get_note(note_id)
        fields[note_id] = dict(note.items())
    cards = {
        row[0]: row
        for row in col.db.all(
            "select id, nid, did, ord, type, queue, due, ivl, factor, reps, lapses from cards"
        )
    }
    # Every note's recordings, including the ones this run never looks at. The
    # checks below are all about the notes in the plan, which is exactly the
    # blind spot to worry about: a package that quietly stripped the audio off
    # everything else would pass every one of them.
    elsewhere = {}
    for note_id, flds in col.db.all("select id, flds from notes"):
        if note_id in fields:
            continue
        names = SOUND_TAG.findall(flds or "")
        if names:
            elsewhere[note_id] = sorted(names)
    return {
        "fields": fields,
        "cards": cards,
        "notes": col.note_count(),
        "notetypes": col.db.scalar("select count() from notetypes"),
        "elsewhere": elsewhere,
    }


# --------------------------------------------------------------------------
# generation
# --------------------------------------------------------------------------

async def produce(item: dict, audio_dir: Path, provider, voice: str, attempts: int) -> dict:
    """Generate one file, retrying with a widening pause on transient failures."""
    path = audio_dir / item["file"]
    if path.exists() and path.stat().st_size >= MIN_BYTES:
        return {**item, "status": "cached", "bytes": path.stat().st_size}

    last = ""
    for attempt in range(attempts):
        try:
            audio = await provider(item["text"], voice)
            if len(audio) < MIN_BYTES:
                raise ValueError(f"only {len(audio)} bytes")
            path.write_bytes(audio)
            return {**item, "status": "generated", "bytes": len(audio)}
        except Exception as exc:
            last = f"{type(exc).__name__}: {exc}"
            if attempt < attempts - 1:
                await asyncio.sleep(2 ** attempt)
    return {**item, "status": "failed", "bytes": 0, "error": last}


async def generate_all(
    items: list[dict], audio_dir: Path, manifest: Path, provider_name: str,
    voice: str, concurrency: int, attempts: int,
) -> list[dict]:
    """Generate every missing file, recording each one as it lands.

    The manifest is what a resumed run reads, so it is appended to as results
    arrive rather than written at the end: a run cut short by the battery or the
    clock still leaves behind an accurate record of what exists.
    """
    provider = PROVIDERS[provider_name]
    gate = asyncio.Semaphore(concurrency)
    writing = asyncio.Lock()
    done: list[dict] = []

    async def one(item: dict) -> None:
        async with gate:
            result = await produce(item, audio_dir, provider, voice, attempts)
        result["duration"] = duration_seconds(audio_dir / result["file"]) if result["bytes"] else None
        async with writing:
            done.append(result)
            with manifest.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(result, ensure_ascii=False) + "\n")
            count = len(done)
            if count % 50 == 0 or count == len(items):
                log(f"  {count}/{len(items)} files")

    await asyncio.gather(*(one(item) for item in items))
    return done


# --------------------------------------------------------------------------
# writing it back
# --------------------------------------------------------------------------

def wants(before: str, stored: str, replacing: bool) -> str:
    """What the field should read once the tag is on it.

    One expression, used both to write the field and to check the field after a
    round trip through a real import, so the check cannot drift from the write.
    """
    head = OUR_SOUND.sub("", before).rstrip() if replacing else before
    return head + f" [sound:{stored}]"


def attach(col: Collection, results: list[dict], audio_dir: Path, field: str) -> list[dict]:
    """Copy the audio into the collection and put the tag on the field.

    The file is added first: the media store decides the final name, and the tag
    has to point at the name it actually used, not the one we asked for.

    Re-voicing drops the old tag rather than adding a second one. The file it
    pointed at stays in the media folder, unreferenced — Check Media in Anki
    sweeps those, and deleting them here would take somebody's only copy if two
    notes shared a sentence and only one was re-recorded.
    """
    attached = []
    for result in results:
        if result["status"] == "failed":
            continue
        stored = col.media.add_file(str(audio_dir / result["file"]))
        note = col.get_note(result["note_id"])
        replacing = bool(result.get("replaces"))
        if "[sound:" in note[field] and not replacing:
            continue  # generated in this run, but written by an earlier one
        note[field] = wants(note[field], stored, replacing)
        col.update_note(note)
        attached.append({**result, "stored": stored})
    return attached


def strip_tts(col: Collection, notetype_ids: list[int]) -> list[str]:
    """Remove the {{tts}} directives from the templates, and say what was removed.

    Without this the card plays twice on review: the pre-generated audio and the
    synthetic voice this was meant to replace. Anki matches note types by id and
    the field list is untouched, so this updates the installed note type in place
    rather than forking a copy of it - verified by importing the result.
    """
    changed = []
    for notetype_id in notetype_ids:
        notetype = col.models.get(notetype_id)
        if notetype is None:
            continue
        touched = False
        for template in notetype["tmpls"]:
            for side in ("qfmt", "afmt"):
                kept = [line for line in template[side].splitlines() if not TTS_DIRECTIVE.search(line)]
                if len(kept) != len(template[side].splitlines()):
                    template[side] = "\n".join(kept)
                    touched = True
                    changed.append(f"{notetype['name']} / {template['name']} / {side}")
        if touched:
            col.models.update_dict(notetype)
    return changed


def export_package(col: Collection, note_ids: list[int], out_path: Path, legacy: bool) -> int:
    """Write the .apkg.

    Scheduling is deliberately left out. The cards on the device are the ones
    that matter, and an import carrying scheduling could only ever move them
    backwards to whatever the collection looked like when this job synced.
    """
    return col.export_anki_package(
        out_path=str(out_path),
        options=ExportAnkiPackageOptions(
            with_scheduling=False, with_deck_configs=False, with_media=True, legacy=legacy
        ),
        limit=NoteIdsLimit(note_ids=note_ids),
    )


# --------------------------------------------------------------------------
# verification
# --------------------------------------------------------------------------

def media_in_package(apkg: Path) -> tuple[set[str] | None, int]:
    """Filenames the archive says it carries, and how many payload entries it has."""
    with zipfile.ZipFile(apkg) as archive:
        names = archive.namelist()
        raw = archive.read("media") if "media" in names else b""
    payloads = sum(1 for name in names if name.isdigit())
    try:
        return set(json.loads(raw.decode("utf-8")).values()), payloads
    except Exception:
        return None, payloads  # newer packages store the map as protobuf


def verify(
    apkg: Path, before_path: Path, snap: dict, attached: list[dict],
    field: str, notetypes_before: dict, stripped: bool,
) -> list[dict]:
    """Import the package into the collection as it was, and check what moved.

    Reading the archive only proves it is well formed. Importing it into a copy
    of the real collection proves the thing that actually matters: that notes
    are updated rather than duplicated and that no card's scheduling shifts.
    """
    checks: list[dict] = []

    def check(label: str, ok: bool, detail: str = "") -> None:
        checks.append({"label": label, "ok": bool(ok), "detail": detail})

    check("every note that needed audio got it", len(attached) == len(snap["fields"]),
          f"{len(attached)} tagged, {len(snap['fields'])} planned")

    stored = {item["stored"] for item in attached}
    listed, payloads = media_in_package(apkg)
    if listed is None:
        check("package media map is readable", False, "stored in the newer binary format")
    else:
        check("every generated file is in the package", stored <= listed,
              f"{len(listed)} in package, {len(stored)} expected")
    check("one media payload per distinct file", payloads == len(stored),
          f"{payloads} payloads, {len(stored)} distinct files")

    col = Collection(str(before_path))
    try:
        result = col.import_anki_package(
            ImportAnkiPackageRequest(
                package_path=str(apkg),
                options=ImportAnkiPackageOptions(
                    merge_notetypes=False, with_scheduling=False, with_deck_configs=False
                ),
            )
        )
        summary = str(getattr(result, "log", ""))[:200].replace("\n", " ")
        check("notes updated, none added", col.note_count() == snap["notes"],
              f"{col.note_count()} notes after import, {snap['notes']} before")

        missing = [item for item in attached
                   if not col.db.scalar("select id from notes where guid = ?", item["guid"])]
        check("every packaged note matched an existing note by guid", not missing,
              f"{len(missing)} unmatched")

        bad_field, bad_other, doubled = [], [], []
        for item in attached:
            note_id = col.db.scalar("select id from notes where guid = ?", item["guid"])
            if not note_id:
                continue
            after = dict(col.get_note(note_id).items())
            before = snap["fields"][item["note_id"]]
            if after.get(field) != wants(before[field], item["stored"], bool(item.get("replaces"))):
                bad_field.append(item["guid"])
            tags = len(SOUND_TAG.findall(after.get(field, "")))
            if tags != 1:
                doubled.append(f"{item['guid']} ({tags})")
            for name, value in before.items():
                if name != field and after.get(name) != value:
                    bad_other.append(f"{item['guid']}/{name}")
        check(f"`{field}` is the original text with one tag on it", not bad_field, f"{len(bad_field)} wrong")
        # Not "doubled": zero lands here too, and that is the more likely of the
        # two - it means the import declined the note rather than updating it.
        check(f"every `{field}` ends with exactly one [sound:] tag", not doubled,
              f"{len(doubled)} with a different count: " + ", ".join(doubled[:5]))
        check("every other field is byte-identical", not bad_other, f"{len(bad_other)} changed")

        # And the notes this run never planned to touch. Their audio is not
        # this package's business at all, so anything missing here is damage.
        quieted = []
        for note_id, names in snap.get("elsewhere", {}).items():
            flds = col.db.scalar("select flds from notes where id = ?", note_id)
            kept = set(SOUND_TAG.findall(flds or "")) if flds is not None else set()
            gone = [name for name in names if name not in kept]
            if gone:
                quieted.append(f"{note_id} ({len(gone)})")
        check("no note outside this run lost a recording", not quieted,
              f"{len(quieted)} went quiet: " + ", ".join(quieted[:5]))

        moved = []
        for row in col.db.all(
            "select id, nid, did, ord, type, queue, due, ivl, factor, reps, lapses from cards"
        ):
            if snap["cards"].get(row[0]) != row:
                moved.append(row[0])
        check("no card's scheduling changed", not moved, f"{len(moved)} cards moved")

        after_nt = {nt_id: col.models.get(nt_id) for nt_id in notetypes_before}
        check("note type ids still exist", all(after_nt.values()))
        forked = col.db.scalar("select count() from notetypes") - snap["notetypes"]
        check("no extra note type was created", forked <= 0,
              f"{snap['notetypes']} before, {snap['notetypes'] + forked} after")
        same = [
            nt_id for nt_id, before in notetypes_before.items()
            if after_nt.get(nt_id)
            and [f["name"] for f in after_nt[nt_id]["flds"]] == [f["name"] for f in before["flds"]]
            and after_nt[nt_id]["css"] == before["css"]
        ]
        check("fields and CSS unchanged on every note type", len(same) == len(notetypes_before),
              f"{len(same)}/{len(notetypes_before)} unchanged")
        if stripped:
            # Whether a template edit is applied depends on the note type being
            # newer than the installed one, so it is checked, not assumed: an
            # edit that is silently dropped leaves the card speaking twice.
            loud = [
                nt_id for nt_id, notetype in after_nt.items()
                if notetype and any(TTS_DIRECTIVE.search(template[side])
                                    for template in notetype["tmpls"] for side in ("qfmt", "afmt"))
            ]
            check("{{tts}} is gone from the imported templates", not loud,
                  f"{len(loud)} note types would still speak the field")
        if summary:
            log(f"  import log: {summary}")
    finally:
        col.close()

    sizes = [item["bytes"] for item in attached]
    check("no file is suspiciously small", all(size >= MIN_BYTES for size in sizes),
          f"smallest {min(sizes) if sizes else 0} bytes")
    timed = [item for item in attached if item.get("duration")]
    odd = [
        item for item in timed
        if not (0.25 <= item["duration"] / max(item["chars"] * MS_PER_CHAR / 1000, 0.1) <= 4.0)
    ]
    check("length is plausible for the text", not odd, f"{len(odd)} of {len(timed)} outside 0.25-4x")
    return checks


# --------------------------------------------------------------------------

def human_size(num: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if num < 1024 or unit == "GB":
            return f"{num:.0f}{unit}" if unit == "B" else f"{num:.1f}{unit}"
        num /= 1024
    return f"{num:.1f}GB"


def main() -> int:
    parser = argparse.ArgumentParser(description="Build an .apkg of pre-generated TTS audio.")
    parser.add_argument("--deck", default="", help="Deck name, or several separated by commas.")
    parser.add_argument("--query", default="", help="Raw Anki search; overrides --deck.")
    parser.add_argument("--field", default="Example", help="Field to speak and to append the tag to.")
    parser.add_argument("--provider", default="edge", choices=sorted(PROVIDERS),
                        help="edge = Microsoft neural voices; silent = offline test files.")
    parser.add_argument("--voice", default="en-US-AvaNeural")
    parser.add_argument("--revoice", action="store_true",
                        help="Re-record notes that already have audio this script made, "
                             "in the voice given by --voice. Recordings made elsewhere are "
                             "left alone.")
    parser.add_argument("--out-dir", default="tts-build")
    parser.add_argument("--audio-dir", default="", help="Audio cache (default: <out-dir>/audio).")
    parser.add_argument("--concurrency", type=int, default=6)
    parser.add_argument("--attempts", type=int, default=4, help="Tries per file before giving up.")
    parser.add_argument("--limit", type=int, default=0, help="Only the first N notes (0 = all).")
    parser.add_argument("--keep-tts", action="store_true",
                        help="Leave {{tts}} in the templates. The card will then play both.")
    parser.add_argument("--modern-format", action="store_true",
                        help="Newer .apkg format. The default is the one every AnkiDroid reads.")
    parser.add_argument("--dry-run", action="store_true", help="Report the plan, generate nothing.")
    parser.add_argument("--local-collection", default="", help="Use a collection file instead of syncing.")
    args = parser.parse_args()

    username = os.environ.get("ANKIWEB_USERNAME", "").strip()
    password = os.environ.get("ANKIWEB_PASSWORD", "")
    if not args.local_collection and (not username or not password):
        fail("ANKIWEB_USERNAME / ANKIWEB_PASSWORD are not set.",
             "Add them under Settings -> Secrets and variables -> Actions.")

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    audio_dir = Path(args.audio_dir) if args.audio_dir else out_dir / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    manifest = out_dir / "manifest.jsonl"

    if args.local_collection:
        work = Path(args.local_collection)
        col = Collection(str(work))
    else:
        work = Path("anki-work") / "collection.anki2"
        work.parent.mkdir(exist_ok=True)
        col = Collection(str(work))
        sync_down(col, username, password, os.environ.get("ANKIWEB_ENDPOINT") or None)

    decks = deck_inventory(col)
    query = args.query or build_deck_query(parse_deck_list(args.deck), decks)
    log(f"Searching: {query}")
    note_ids = list(col.find_notes(query))
    if not note_ids:
        fail(f"No notes matched {query!r}.",
             "Available decks: " + (", ".join(d["deck"] for d in decks) or "(none)"))
    if args.limit:
        note_ids = note_ids[: args.limit]

    items, skipped = plan_notes(col, note_ids, args.field, args.voice, args.revoice)
    distinct = {item["file"] for item in items}
    chars = sum(item["chars"] for item in items)
    log(f"{len(note_ids)} notes matched; {len(items)} need audio "
        f"({len(distinct)} distinct files, {chars} characters).")
    for reason, count in skipped.items():
        if count:
            log(f"  skipped, {reason}: {count}")

    notetypes_before = {}
    for note_id in note_ids:
        notetype = col.get_note(note_id).note_type()
        notetypes_before[notetype["id"]] = notetype

    if args.dry_run or not items:
        head = [
            f"## TTS build — dry run" if args.dry_run else "## TTS build — nothing to do",
            "",
            f"- Query: `{query}`",
            f"- Notes matched: **{len(note_ids)}**, needing audio: **{len(items)}**",
            f"- Distinct files: **{len(distinct)}** · characters: **{chars}**",
        ] + [f"- Skipped, {r}: **{c}**" for r, c in skipped.items() if c]
        write_summary(head)
        col.close()
        return 0

    snap = snapshot(col, [item["note_id"] for item in items])
    col.close()
    before_path = out_dir / "before.anki2"
    shutil.copy2(work, before_path)
    col = Collection(str(work))

    log(f"Generating with {args.provider} ({args.voice}), concurrency {args.concurrency}...")
    by_file: dict[str, dict] = {}
    for item in items:
        by_file.setdefault(item["file"], item)
    results = asyncio.run(generate_all(
        list(by_file.values()), audio_dir, manifest, args.provider,
        args.voice, args.concurrency, args.attempts,
    ))
    produced = {r["file"]: r for r in results}
    failures = [r for r in results if r["status"] == "failed"]
    if failures:
        for bad in failures[:5]:
            log(f"::error::{bad['file']}: {bad.get('error', 'unknown')}")
        fail(f"{plural(len(failures), 'file')} could not be generated.",
             "Nothing was packaged. Re-run to resume: files already on disk are kept.")

    # Notes sharing a sentence share a file, so only the generation outcome is
    # copied across. Merging the whole result would carry the note identity of
    # whichever note happened to be generated for, and the others would be
    # tagged onto it instead of themselves.
    everything = [
        {**item, **{key: produced[item["file"]].get(key) for key in ("status", "bytes", "duration")}}
        for item in items
    ]
    attached = attach(col, everything, audio_dir, args.field)
    log(f"Tagged {plural(len(attached), 'note')} using "
        f"{plural(len(distinct), 'file')}.")
    if len(attached) != len(items):
        fail(f"{len(items)} notes needed audio but {len(attached)} were tagged.",
             "Nothing was packaged. This is a bug in the builder, not in your collection.")

    stripped = [] if args.keep_tts else strip_tts(col, list(notetypes_before))
    if stripped:
        log("Removed {{tts}} from: " + ", ".join(stripped))

    apkg = out_dir / "tts-update.apkg"
    count = export_package(col, [item["note_id"] for item in attached], apkg, not args.modern_format)
    col.close()
    log(f"Exported {plural(count, 'note')} to {apkg} ({human_size(apkg.stat().st_size)}).")

    checks = verify(apkg, before_path, snap, attached, args.field, notetypes_before, bool(stripped))
    before_path.unlink(missing_ok=True)
    shutil.rmtree(out_dir / "before.media", ignore_errors=True)

    passed = sum(1 for c in checks if c["ok"])
    audio_bytes = sum(item["bytes"] for item in attached)
    size = apkg.stat().st_size
    write_summary(
        [
            f"## TTS build — {plural(len(attached), 'note')}, {human_size(size)}",
            "",
            f"- Query: `{query}` · field: `{args.field}`",
            f"- Voice: `{args.voice}` via `{args.provider}`"
            + (" · **re-voicing**: the old tag is replaced, not added to" if args.revoice else ""),
            f"- Characters sent: **{chars}** · distinct files: **{len(distinct)}** "
            f"(deduplicated {len(items) - len(distinct)})",
            f"- Audio: **{human_size(audio_bytes)}** · package: **{human_size(size)}**",
        ]
        + [f"- Skipped, {r}: **{c}**" for r, c in skipped.items() if c]
        + (["- Removed `{{tts}}` from: " + ", ".join(f"`{s}`" for s in stripped)] if stripped else [])
        + ["", f"### Verification — {passed}/{len(checks)} passed", ""]
        + [f"- {'PASS' if c['ok'] else '**FAIL**'} — {c['label']}"
           + (f" _({c['detail']})_" if c["detail"] else "") for c in checks]
        + ([
            "",
            f"::warning::The package is {human_size(size)}. Importing something this large on a "
            "phone is slow and can run out of memory. Consider building one deck at a time.",
        ] if size > 200 * 1024 * 1024 else [])
        + [
            "",
            "### Before you import",
            "",
            "1. AnkiDroid → **export a full backup (.colpkg)** and confirm the file exists.",
            "2. Download `tts-update.apkg` from the artifacts below.",
            "3. Import it in AnkiDroid, choosing the mode that **updates** existing notes.",
            f"4. The summary should say about **{len(attached)} notes updated, 0 added**. "
            "If it says added, stop and restore the backup without syncing.",
            "5. Open a card and check the play button works — and that you hear the "
            "recording only, not the synthetic voice as well. If you hear both, the note "
            "type kept its `{{tts}}` line: remove it in **Cards → Back template**.",
            "6. Sync, so the audio reaches your other device.",
        ]
        + ([
            "",
            f"Once you are happy with how it sounds, **Check Media → Delete Unused** in "
            f"AnkiDroid clears the {plural(len(attached), 'recording')} the old voice left "
            "behind. They are kept until then so that nothing is lost if you change your "
            "mind and re-voice back.",
        ] if args.revoice else [])
    )

    if passed != len(checks):
        fail(f"{len(checks) - passed} verification check(s) failed; do not import this package.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
