#!/usr/bin/env python3
"""Fold the generated meaning onto cards that were made before there was one.

The Add to Anki page has explained a word while you typed it for a while now,
and since the change that came with this file it puts that explanation on the
card as well - appended to the answer field, under the word, inside a
`<details>` that shows as a small "in detail" until it is tapped.

That only ever helps cards made after it. The collection this was written
against has around 1,240 cards made before it, which is nearly all of them, and
those are the ones that are actually reviewed. This is for them.

It does not ask the model unless told to. `--ask 0`, the default, uses only the
answers already committed to `docs/lookups/`, which cost nothing and are
instant. Raising it is how you agree to spend money, one capped batch at a time,
and every answer it buys is written back to that cache - so the page, the other
device and every later run get it for free.

Nothing is written without `--apply`, and `--undo` takes it all out again.

    ANKIWEB_USERNAME, ANKIWEB_PASSWORD
    ANTHROPIC_API_KEY  (only with --ask)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

from anki.collection import Collection  # noqa: E402

from add_card import (  # noqa: E402
    existing_notes,
    recordings,
    check_recordings_kept,
    sync_up,
)
from export_deck import (  # noqa: E402
    build_deck_query,
    deck_inventory,
    fail,
    insist_on_fields,
    log,
    parse_deck_list,
    plural,
    sync_down,
    write_summary,
)
from explain import explain as ask_about, slug  # noqa: E402
from proofread import BLOCK_BOUNDARY, MARKUP, client, headword  # noqa: E402

# Where the markup comes from.
#
# The page builds this block in JavaScript at the moment a card is sent, and
# this builds the same block in Python for cards that already exist. Two
# implementations of one piece of markup is exactly the arrangement that drifts,
# and it drifts silently: nobody compares a card added in March against one
# folded in June until they are side by side and one of them looks wrong.
#
# So there is one source and it is the page. The constants below are read out of
# it rather than copied from it, which means a change to the styling there is a
# change here with nothing to remember. test/python/test_folded_meaning.py pins
# the result against a fixture, so a change that alters the output has to be
# looked at rather than merely allowed.
PAGE = Path(__file__).resolve().parents[1] / "docs" / "index.html"

STYLE_NAMES = ("D_WRAP", "D_SUM", "D_BODY", "D_HEAD", "D_ROW", "D_DIM")
LIMIT_NAMES = ("DETAIL_MAX", "DETAIL_LINE", "DETAIL_LIST", "DETAIL_WITH")


def page_constants(page: Path = PAGE) -> dict:
    """The block's class, its inline styles and its caps, read off the page."""
    if not page.exists():
        fail(f"{page} is not there, so there is nothing to copy the markup from.",
             "This reads the styles out of the page so the two cannot disagree.")
    text = page.read_text(encoding="utf-8")
    found: dict = {}
    for name in ("DETAIL_MARK",) + STYLE_NAMES:
        match = re.search(rf'const {name} = "([^"]*)"', text)
        if not match:
            fail(f"{page.name} no longer defines {name}.",
                 "The page and this script share one definition of the block. "
                 "If the page renamed it, rename it here too rather than "
                 "copying the markup back in.")
        found[name] = match.group(1)
    for name in LIMIT_NAMES:
        match = re.search(rf"const {name} = (\d+)", text)
        if not match:
            fail(f"{page.name} no longer defines {name}.", "")
        found[name] = int(match.group(1))
    return found


PAGE_CONSTANTS = page_constants()
DETAIL_MARK = PAGE_CONSTANTS["DETAIL_MARK"]
DETAIL_BLOCK = re.compile(rf"<details\b[^>]*{re.escape(DETAIL_MARK)}.*?</details>",
                          re.IGNORECASE | re.DOTALL)


def escape(text: str) -> str:
    """Exactly what the page's escape() does, including &#39; for an apostrophe.

    html.escape() writes &#x27; for the same character. Both are correct and
    they are not the same bytes, which is all it takes for a card folded here
    to differ from one folded on the page.
    """
    return (str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;").replace("'", "&#39;"))


def shorten(text: str) -> str:
    """One line, capped, the way the page caps it."""
    said = re.sub(r"\s+", " ", str(text or "")).strip()
    cap = PAGE_CONSTANTS["DETAIL_LINE"]
    return said if len(said) <= cap else said[: cap - 1].rstrip() + "…"


def _row(style: str, html: str) -> str:
    return f'<div style="{style}">{html}</div>' if html else ""


def block(info: dict) -> str:
    """The folded block for one explanation, or "" when there is nothing to fold."""
    if not info or info.get("recognised") is False:
        return ""
    c = PAGE_CONSTANTS

    def esc(value) -> str:
        return escape(shorten(value))

    body = _row(c["D_ROW"], esc(info.get("meaning", "")))
    aside = " · ".join(
        esc(v) for v in (info.get("pronunciation", ""), info.get("tone", "")) if v
    )
    body += _row(c["D_DIM"], aside)
    body += _row(c["D_ROW"], esc(info.get("nuance", "")))

    withs = [w for w in (info.get("collocations") or [])[: c["DETAIL_WITH"]] if w]
    if withs:
        body += _row(c["D_HEAD"], "goes with")
        body += _row(c["D_ROW"], " · ".join(esc(w) for w in withs))

    nots = [n for n in (info.get("confusables") or [])[: c["DETAIL_LIST"]] if n and n.get("word")]
    if nots:
        body += _row(c["D_HEAD"], "not to be confused with")
        for item in nots:
            body += _row(c["D_ROW"],
                         f"<b>{esc(item['word'])}</b> — {esc(item.get('difference', ''))}")

    shown = [e for e in (info.get("examples") or [])[: c["DETAIL_LIST"]] if e]
    if shown:
        body += _row(c["D_HEAD"], "elsewhere")
        for sentence in shown:
            body += _row(c["D_ROW"], f"“{esc(sentence)}”")

    if info.get("memory_hook"):
        body += (f'<div style="{c["D_ROW"]};font-style:italic;margin-top:.5em">'
                 f'{esc(info["memory_hook"])}</div>')

    if info.get("korean"):
        body += _row(f'{c["D_ROW"]};margin-top:.5em', f"한국어 · {esc(info['korean'])}")

    if not body.strip():
        return ""
    html = (f'<details class="{DETAIL_MARK}" style="{c["D_WRAP"]}">'
            f'<summary style="{c["D_SUM"]}">in detail</summary>'
            f'<div style="{c["D_BODY"]}">{body}</div>'
            f"</details>")
    return "" if len(html) > c["DETAIL_MAX"] else html


# ---- reading the collection ----------------------------------------------

def word_of(raw: str) -> str:
    """The word this card is for, as generously as is safe.

    headword() takes the text before the first block tag, which is the word on
    all but a handful of cards. On those, Anki's editor wrote the field as
    <div>word</div><div>hook</div> and there is no text before the first tag at
    all - headword() answers "" and the card is passed over for no reason a
    person would recognise. So the first block with anything in it, which is
    what the voice has always done with a sentence.
    """
    head = headword(raw)
    if head:
        return head
    for part in BLOCK_BOUNDARY.split(str(raw or "")):
        text = MARKUP.sub(" ", part)
        text = re.sub(r"\s+", " ", text).strip()
        if text:
            return text
    return ""


def cached(cache_dir: Path, word: str) -> dict | None:
    key = slug(word)
    if not key:
        return None
    target = cache_dir / f"{key}.json"
    if not target.exists():
        return None
    try:
        return json.loads(target.read_text(encoding="utf-8"))
    except Exception:
        return None


def plan(col: Collection, note_ids: list[int], field: str, cache_dir: Path) -> tuple[list, dict]:
    """Every card that could take a fold, and why the rest cannot."""
    items: list[dict] = []
    skipped = {"a note type without that field": 0, "no word to look up": 0,
               "already folded": 0, "the word carries formatting": 0}
    for note_id in note_ids:
        note = col.get_note(note_id)
        if field not in note:
            skipped["a note type without that field"] += 1
            continue
        raw = note[field]
        if DETAIL_BLOCK.search(raw):
            # Run twice and the second run is a no-op. That is the point: this
            # is meant to be safe to re-run as the cache fills up.
            skipped["already folded"] += 1
            continue
        word = word_of(raw)
        if not word:
            skipped["no word to look up"] += 1
            continue
        items.append({"note_id": note_id, "guid": note.guid, "word": word,
                      "key": slug(word), "have": cached(cache_dir, word) is not None})
    return items, skipped


# ---- asking for the ones nobody has looked up ----------------------------

# Haiku 4.5, at $1 per million tokens in and $5 per million out. An explanation
# is a few hundred tokens each way, so this is about a fifth of a cent a word -
# and once, ever, because the answer is committed.
COST_IN = 1.0 / 1_000_000
COST_OUT = 5.0 / 1_000_000
TOKENS_IN = 400
TOKENS_OUT = 500


def estimate(count: int) -> str:
    money = count * (TOKENS_IN * COST_IN + TOKENS_OUT * COST_OUT)
    return f"about ${money:.2f}"


# A key with no credit, a revoked key, an outage: whatever it is, it is the same
# for every word, and grinding through nine hundred of them to say so nine
# hundred times helps nobody. Five in a row with nothing in between is enough.
GIVE_UP_AFTER = 5


def ask_for(words: list[str], cache_dir: Path, model: str = "") -> tuple[int, list[str]]:
    """Look up the words nobody has, and commit each answer as it lands.

    Written one at a time rather than at the end on purpose: a run that dies
    forty words in has still paid for forty words, and they are on disk.
    """
    conversation = client()
    asked, failed, in_a_row = 0, [], 0
    for index, word in enumerate(words, 1):
        key = slug(word)
        try:
            answer = ask_about(word, conversation, model=model)
        except Exception as exc:
            failed.append(f"{word} ({type(exc).__name__})")
            log(f"  {index}/{len(words)} {word}: {type(exc).__name__}: {exc}")
            in_a_row += 1
            if in_a_row >= GIVE_UP_AFTER:
                log(f"::warning::{GIVE_UP_AFTER} in a row failed, so the rest were not "
                    "attempted. Whatever is wrong is not about these particular words.")
                failed.append(f"…and {len(words) - index} not attempted")
                break
            continue
        in_a_row = 0
        info = answer.model_dump()
        if not info.get("recognised", True):
            # Asked and told it is not a word. Not cached: cached, it would
            # answer for the correct spelling too.
            failed.append(f"{word} (not recognised)")
            log(f"  {index}/{len(words)} {word}: not a word, so not cached")
            continue
        cache_dir.mkdir(parents=True, exist_ok=True)
        (cache_dir / f"{key}.json").write_text(
            json.dumps(info, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        asked += 1
        if index % 25 == 0 or index == len(words):
            log(f"  {index}/{len(words)}")
    return asked, failed


# ---- writing it ----------------------------------------------------------

WHY = ("Something other than this job changed the collection while it was running, "
       "which usually means a phone was mid-sync. Nothing was sent to AnkiWeb. Let "
       "the phone finish and run it again.")


def guarded(col: Collection, field: str, change, note_ids: list[int] | None = None) -> dict:
    """Make a change to the answer field, and refuse to sync if anything else moved.

    `change` is handed each note and returns the new value for the field, or
    None to leave it alone. Everything around it is the same refusal the rest of
    this directory makes: the note list, every other note's mod time, every
    card's scheduling, and every [sound:] tag in the whole collection - the
    whole collection and not just the notes in scope, because a job that only
    ever checks its own notes is exactly how a thousand recordings went missing
    once with nothing to say which job had done it.
    """
    before_notes = existing_notes(col)
    before_audio = recordings(col)
    before_cards = {row[0]: row for row in col.db.all(
        "select id, nid, did, ord, type, queue, due, ivl, factor, reps, lapses from cards")}

    touched: list[int] = []
    for note_id in (list(before_notes) if note_ids is None else note_ids):
        if note_id not in before_notes:
            continue
        note = col.get_note(note_id)
        if field not in note:
            continue
        was = note[field]
        now = change(note_id, note, was)
        if now is None or now == was:
            continue
        # The one thing this must never do. The word is what every other script
        # here reads out of this field, and a fold that lands in front of it
        # takes the card away from all of them at once.
        if word_of(now) != word_of(was):
            fail(f"Note {note_id} would have changed its word from {word_of(was)!r} "
                 f"to {word_of(now)!r}. Nothing was synced.",
                 "The block goes after the word, never before it. Whatever was "
                 "written to the runner's copy before this stops here and is "
                 "thrown away with it; your AnkiWeb collection is untouched.")
        note[field] = now
        col.update_note(note)
        touched.append(note_id)

    after_notes = existing_notes(col)
    if set(after_notes) != set(before_notes):
        fail("The note list changed while folding, so nothing was synced.", WHY)
    moved = [n for n, mod in before_notes.items()
             if after_notes[n] != mod and n not in touched]
    if moved:
        fail(f"{len(moved)} notes changed that should not have. Nothing was synced.", WHY)
    after_cards = {row[0]: row for row in col.db.all(
        "select id, nid, did, ord, type, queue, due, ivl, factor, reps, lapses from cards")}
    if after_cards != before_cards:
        fail("Card scheduling moved while folding, so nothing was synced.", WHY)
    # This touches no sentence and no [sound:] tag, so it can take a recording
    # off nothing.
    check_recordings_kept(before_audio, recordings(col))
    return {"touched": len(touched)}


def fold_all(col: Collection, blocks: dict[int, str], field: str) -> dict:
    return guarded(col, field, lambda note_id, note, was:
                   (was + blocks[note_id]) if note_id in blocks and not DETAIL_BLOCK.search(was)
                   else None,
                   note_ids=list(blocks))


def unfold_all(col: Collection, field: str, note_ids: list[int] | None = None) -> dict:
    return guarded(col, field, lambda note_id, note, was:
                   DETAIL_BLOCK.sub("", was) if DETAIL_BLOCK.search(was) else None,
                   note_ids=note_ids)


def folded_already(col: Collection, note_ids: list[int], field: str) -> int:
    """How many of these carry a fold, for saying so before taking any out."""
    count = 0
    for note_id in note_ids:
        note = col.get_note(note_id)
        if field in note and DETAIL_BLOCK.search(note[field]):
            count += 1
    return count


# ---- the report ----------------------------------------------------------

def report_undo(carrying: int, undid: int, wrote: bool, synced: bool = False) -> None:
    lines = ["# The meaning, folded onto the back", ""]
    if wrote:
        lines += [f"Took the fold back out of **{plural(undid, 'card')}**"
                  + (" and synced." if synced else "."), "",
                  "Nothing else on those cards was touched: the word, any memory hook "
                  "written under it, the sentence, the recording and the scheduling "
                  "are all exactly as they were.", ""]
    else:
        lines += [f"**{plural(carrying, 'card')}** carry a fold. Nothing was written — "
                  "tick *apply* as well to take them out.", ""]
    write_summary(lines)


def write_report(items: list[dict], skipped: dict, looked_at: int, folded: int,
                 asked: int, failed: list[str], wrote: bool,
                 field: str, cache_dir: Path, synced: bool = False) -> None:
    lines = ["# The meaning, folded onto the back", ""]

    ready = [i for i in items if i["have"]]
    waiting = [i for i in items if not i["have"]]
    lines += [
        f"Looked at **{plural(looked_at, 'card')}**. "
        f"{plural(len(items), 'card')} could take a fold, "
        f"and **{len(ready)}** of those have an explanation already.",
        "",
    ]

    if wrote:
        lines += [f"**Folded {plural(folded, 'card')}**"
                  + (" and synced." if synced else "."), ""]
    elif folded:
        lines += [f"**{plural(folded, 'card')} would be folded.** "
                  "Nothing was written — tick *apply* to do it.", ""]
    else:
        lines += ["Nothing to fold.", ""]

    if asked:
        lines += [f"Asked about **{plural(asked, 'new word')}** and committed the "
                  f"answers to `{cache_dir}`, where they are free from now on.", ""]
    if failed:
        lines += ["Could not look up:", ""]
        lines += [f"- {name}" for name in failed[:20]]
        if len(failed) > 20:
            lines += [f"- …and {len(failed) - 20} more"]
        lines += [""]

    if waiting:
        words = sorted({i["word"] for i in waiting}, key=str.lower)
        lines += [
            f"**{plural(len(waiting), 'card')}** are waiting on a word nobody has looked "
            f"up yet ({plural(len(words), 'distinct word')}). Run this again with *ask* "
            f"set to how many you are willing to pay for — {estimate(len(words))} for all "
            "of them, once, ever.",
            "",
            "<details><summary>the words</summary>",
            "",
            ", ".join(f"`{w}`" for w in words[:400]),
            "",
            "</details>",
            "",
        ]

    counted = {name: n for name, n in skipped.items() if n}
    if counted:
        lines += ["Passed over:", ""]
        lines += [f"- {plural(n, 'card')} — {name}" for name, n in counted.items()]
        lines += [""]

    lines += [
        "---",
        "",
        f"The block is appended to `{field}`, after the word, inside a `<details>` "
        "that reads **in detail** until it is tapped. Run it again and nothing "
        "happens twice; run it with *undo* and it comes back out.",
    ]
    write_summary(lines)


# ---- the command line ----------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="Fold the generated meaning onto the back.")
    parser.add_argument("--deck", default="*")
    parser.add_argument("--field", default="Back")
    parser.add_argument("--cache-dir", default="docs/lookups")
    parser.add_argument("--out-dir", default="fold")
    parser.add_argument("--limit", type=int, default=0,
                        help="Only the first N cards that could take a fold (0 = all).")
    parser.add_argument("--ask", type=int, default=0,
                        help="Look up at most N words nobody has explained yet. Costs money.")
    parser.add_argument("--model", default="")
    parser.add_argument("--apply", action="store_true", help="Write the folds and sync.")
    parser.add_argument("--undo", action="store_true",
                        help="Take every fold back out. Needs --apply to be written.")
    parser.add_argument("--local-collection", default="")
    args = parser.parse_args()

    username = os.environ.get("ANKIWEB_USERNAME", "").strip()
    password = os.environ.get("ANKIWEB_PASSWORD", "")
    if not args.local_collection and (not username or not password):
        fail("ANKIWEB_USERNAME / ANKIWEB_PASSWORD are not set.",
             "Add them under Settings -> Secrets and variables -> Actions.")
    if args.ask and not os.environ.get("ANTHROPIC_API_KEY", "").strip():
        fail("ANTHROPIC_API_KEY is not set, so no new word can be looked up.",
             "Leave ask at 0 to fold the words already in the cache, which costs nothing.")

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    cache_dir = Path(args.cache_dir)

    if args.local_collection:
        work = Path(args.local_collection)
        col, auth = Collection(str(work)), None
    else:
        work = Path("anki-work") / "collection.anki2"
        work.parent.mkdir(exist_ok=True)
        col = Collection(str(work))
        auth = col.sync_login(username, password, os.environ.get("ANKIWEB_ENDPOINT") or None)
        sync_down(col, username, password, os.environ.get("ANKIWEB_ENDPOINT") or None)

    if args.apply and auth:
        backup = out_dir / "before-fold.colpkg"
        col.export_collection_package(str(backup), False, True)
        col = Collection(str(work))
        log(f"Backup written to {backup} ({backup.stat().st_size // 1024}KB, no media).")

    query = build_deck_query(parse_deck_list(args.deck), deck_inventory(col))
    note_ids = list(col.find_notes(query))
    log(f"{plural(len(note_ids), 'note')} in scope.")

    if args.undo:
        carrying = folded_already(col, note_ids, args.field)
        if not args.apply:
            log(f"{plural(carrying, 'card')} carry a fold. Nothing was written — add --apply.")
            col.close()
            report_undo(carrying, 0, False)
            return 0
        outcome = unfold_all(col, args.field, note_ids)
        if auth:
            sync_up(col, auth)
            log("Synced to AnkiWeb.")
        col.close()
        log(f"Took the fold out of {plural(outcome['touched'], 'card')}.")
        report_undo(carrying, outcome["touched"], True, bool(auth))
        return 0

    insist_on_fields(col, note_ids, args.field)
    items, skipped = plan(col, note_ids, args.field, cache_dir)
    if args.limit:
        items = items[: args.limit]
    log(f"{plural(len(items), 'card')} could take a fold; "
        f"{sum(1 for i in items if i['have'])} have an explanation already.")

    asked, failed = 0, []
    if args.ask:
        wanted, seen = [], set()
        for item in items:
            if item["have"] or item["key"] in seen:
                continue
            seen.add(item["key"])
            wanted.append(item["word"])
        wanted = wanted[: args.ask]
        if wanted:
            log(f"Looking up {plural(len(wanted), 'word')} ({estimate(len(wanted))})...")
            asked, failed = ask_for(wanted, cache_dir, args.model)
            for item in items:
                if not item["have"]:
                    item["have"] = cached(cache_dir, item["word"]) is not None

    blocks: dict[int, str] = {}
    for item in items:
        if not item["have"]:
            continue
        html = block(cached(cache_dir, item["word"]) or {})
        if html:
            blocks[item["note_id"]] = html
            item["folded"] = True

    (out_dir / "fold.jsonl").write_text(
        "\n".join(json.dumps(i, ensure_ascii=False) for i in items) + "\n", encoding="utf-8")
    log(f"{plural(len(blocks), 'card')} would be folded.")

    wrote, written = False, len(blocks)
    if args.apply and blocks:
        outcome = fold_all(col, blocks, args.field)
        written = outcome["touched"]
        log(f"Folded {plural(written, 'card')}.")
        if auth:
            sync_up(col, auth)
            log("Synced to AnkiWeb.")
        wrote = True

    col.close()
    write_report(items, skipped, len(note_ids), written, asked, failed,
                 wrote, args.field, cache_dir, bool(auth))
    return 0


if __name__ == "__main__":
    sys.exit(main())
