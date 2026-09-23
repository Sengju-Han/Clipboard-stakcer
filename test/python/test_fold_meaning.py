"""Folding the meaning onto cards that were made before there was one.

The page does this for new cards. This is the same block, written by a workflow,
onto the roughly 1,240 that already exist - which are the ones actually being
reviewed, so they are the half that matters.

Three things are worth testing and the rest is arithmetic:

  - the markup is the same markup. Two implementations in two languages is how
    a card folded on the phone ends up looking different from one folded by the
    workflow, and nobody notices until they are side by side.
  - running it twice does nothing twice, and --undo takes it back out. This
    writes to a collection with a year of scheduling on it and no undo on the
    other side.
  - the word stays the word. Everything else in this directory reads it out of
    the same field.
"""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "anki"))

pytest.importorskip("anki.collection",
                    reason="the Anki library is not installed: pip install -r test/python/requirements.txt")

import fold_meaning as fold                                      # noqa: E402
import proofread                                                 # noqa: E402

FIXTURE = json.loads((ROOT / "test" / "fixtures" / "folded-meaning.json")
                     .read_text(encoding="utf-8"))


def _collection(tmp_path, words, field="Back"):
    from anki.collection import Collection

    col = Collection(str(tmp_path / "collection.anki2"))
    notetype = col.models.new("Folded")
    for name in ("Front", "Back", "Example"):
        col.models.add_field(notetype, col.models.new_field(name))
    template = col.models.new_template("Card 1")
    template["qfmt"], template["afmt"] = "{{Front}}", "{{Back}}"
    col.models.add_template(notetype, template)
    col.models.add(notetype)
    notetype = col.models.by_name("Folded")
    for word in words:
        note = col.new_note(notetype)
        note["Front"] = f"clue for {word}"
        note[field] = word
        note["Example"] = f"A sentence with {word} in it. [sound:ttsex-{word}.mp3]"
        col.add_note(note, col.decks.id("Default"))
    return col


def _cache(tmp_path, *words):
    """A lookup cache with a real answer in it, keyed the way the page keys them."""
    where = tmp_path / "lookups"
    where.mkdir(exist_ok=True)
    for word in words:
        info = dict(FIXTURE["info"], word=word)
        (where / f"{fold.slug(word)}.json").write_text(
            json.dumps(info, ensure_ascii=False), encoding="utf-8")
    return where


def _back(col, word):
    flds = col.db.scalar("select flds from notes where flds like ?", f"clue for {word}\x1f%")
    return flds.split("\x1f")[1]


# ---- one block, two authors ----------------------------------------------

def test_python_writes_the_same_block_the_page_writes():
    """The fixture is what the page actually produced, byte for byte. If this
    fails, one of the two changed and the other did not."""
    assert fold.block(FIXTURE["info"]) == FIXTURE["html"]


def test_the_styles_are_read_off_the_page_rather_than_copied():
    page = (ROOT / "docs" / "index.html").read_text(encoding="utf-8")
    for name in fold.STYLE_NAMES:
        assert f'const {name} = "{fold.PAGE_CONSTANTS[name]}"' in page


def test_an_apostrophe_is_escaped_the_way_the_page_escapes_it():
    """html.escape() writes &#x27; and the page writes &#39;. Both are right and
    they are different bytes, which is all it takes to disagree."""
    out = fold.block({"recognised": True, "meaning": "it's a feeling"})
    assert "it&#39;s a feeling" in out
    assert "&#x27;" not in out


def test_markup_in_an_answer_cannot_escape_the_block():
    out = fold.block({"recognised": True, "meaning": "</details><script>x</script>"})
    assert "<script>" not in out
    assert out.count("</details>") == 1


def test_nothing_to_say_is_nothing_written():
    assert fold.block({}) == ""
    assert fold.block({"recognised": False, "meaning": "not a word"}) == ""
    assert fold.block({"recognised": True}) == ""


def test_a_runaway_answer_is_refused_rather_than_written():
    huge = {"recognised": True, "meaning": "x", "examples": ["y" * 300] * 3,
            "collocations": ["z" * 300] * 6,
            "confusables": [{"word": "w" * 300, "difference": "d" * 300}] * 3}
    # Every item is capped, so the cap is what keeps this under the limit at all.
    assert len(fold.block(huge)) <= fold.PAGE_CONSTANTS["DETAIL_MAX"]


# ---- the word stays the word ---------------------------------------------

def test_the_word_survives_a_fold(tmp_path):
    col = _collection(tmp_path, ["chagrin"])
    where = _cache(tmp_path, "chagrin")
    items, skipped = fold.plan(col, list(col.find_notes("")), "Back", where)
    assert [i["word"] for i in items] == ["chagrin"]
    assert all(i["have"] for i in items)

    blocks = {i["note_id"]: fold.block(json.loads(
        (where / f"{i['key']}.json").read_text(encoding="utf-8"))) for i in items}
    assert fold.fold_all(col, blocks, "Back")["touched"] == 1

    back = _back(col, "chagrin")
    assert back.startswith("chagrin<details")
    assert proofread.headword(back) == "chagrin"
    col.close()


def test_a_field_written_as_divs_is_not_passed_over(tmp_path):
    """Anki's editor writes a multi-line field as <div>word</div><div>hook</div>,
    so there is no text at all before the first block tag. headword() answers ""
    for those, and they would be skipped for no reason a person would recognise."""
    col = _collection(tmp_path, ["halcyon"])
    note_id = list(col.find_notes(""))[0]
    note = col.get_note(note_id)
    note["Back"] = "<div>halcyon</div><div>the kingfisher's calm sea</div>"
    col.update_note(note)

    items, _ = fold.plan(col, [note_id], "Back", _cache(tmp_path, "halcyon"))
    assert [i["word"] for i in items] == ["halcyon"]
    col.close()


def test_a_fold_that_would_change_the_word_is_refused(tmp_path):
    col = _collection(tmp_path, ["chagrin"])
    note_id = list(col.find_notes(""))[0]
    with pytest.raises(SystemExit):
        fold.guarded(col, "Back", lambda nid, note, was: "something else<br>" + was)
    col.close()


# ---- twice is once -------------------------------------------------------

def test_running_it_twice_folds_nothing_twice(tmp_path):
    col = _collection(tmp_path, ["chagrin", "halcyon"])
    where = _cache(tmp_path, "chagrin", "halcyon")
    note_ids = list(col.find_notes(""))

    items, _ = fold.plan(col, note_ids, "Back", where)
    blocks = {i["note_id"]: fold.block(json.loads(
        (where / f"{i['key']}.json").read_text(encoding="utf-8"))) for i in items}
    assert fold.fold_all(col, blocks, "Back")["touched"] == 2
    once = _back(col, "chagrin")

    again, skipped = fold.plan(col, note_ids, "Back", where)
    assert again == []
    assert skipped["already folded"] == 2
    assert fold.fold_all(col, blocks, "Back")["touched"] == 0
    assert _back(col, "chagrin") == once
    col.close()


def test_undo_takes_it_back_out(tmp_path):
    col = _collection(tmp_path, ["chagrin"])
    where = _cache(tmp_path, "chagrin")
    note_ids = list(col.find_notes(""))
    before = _back(col, "chagrin")

    items, _ = fold.plan(col, note_ids, "Back", where)
    blocks = {i["note_id"]: fold.block(json.loads(
        (where / f"{i['key']}.json").read_text(encoding="utf-8"))) for i in items}
    fold.fold_all(col, blocks, "Back")
    assert _back(col, "chagrin") != before

    assert fold.unfold_all(col, "Back")["touched"] == 1
    assert _back(col, "chagrin") == before
    # And a second undo is not an error, it is nothing.
    assert fold.unfold_all(col, "Back")["touched"] == 0
    col.close()


def test_undo_leaves_a_hand_written_hook_alone(tmp_path):
    col = _collection(tmp_path, ["chagrin"])
    note_id = list(col.find_notes(""))[0]
    note = col.get_note(note_id)
    note["Back"] = "chagrin<br>the grin you hold while mortified"
    col.update_note(note)
    where = _cache(tmp_path, "chagrin")

    items, _ = fold.plan(col, [note_id], "Back", where)
    blocks = {i["note_id"]: fold.block(json.loads(
        (where / f"{i['key']}.json").read_text(encoding="utf-8"))) for i in items}
    fold.fold_all(col, blocks, "Back")
    fold.unfold_all(col, "Back")
    assert _back(col, "chagrin") == "chagrin<br>the grin you hold while mortified"
    col.close()


# ---- what it refuses to do -----------------------------------------------

def test_a_word_nobody_looked_up_is_reported_rather_than_folded(tmp_path):
    col = _collection(tmp_path, ["chagrin", "sesquipedalian"])
    where = _cache(tmp_path, "chagrin")
    items, _ = fold.plan(col, list(col.find_notes("")), "Back", where)
    assert {i["word"]: i["have"] for i in items} == {"chagrin": True, "sesquipedalian": False}
    col.close()


def test_the_other_fields_and_the_scheduling_are_untouched(tmp_path):
    col = _collection(tmp_path, ["chagrin", "halcyon"])
    where = _cache(tmp_path, "chagrin", "halcyon")
    before_cards = {row[0]: row for row in col.db.all(
        "select id, nid, did, ord, type, queue, due, ivl, factor, reps, lapses from cards")}
    before_fronts = {n: col.get_note(n)["Front"] for n in col.find_notes("")}
    before_examples = {n: col.get_note(n)["Example"] for n in col.find_notes("")}

    items, _ = fold.plan(col, list(col.find_notes("")), "Back", where)
    blocks = {i["note_id"]: fold.block(json.loads(
        (where / f"{i['key']}.json").read_text(encoding="utf-8"))) for i in items}
    fold.fold_all(col, blocks, "Back")

    assert {n: col.get_note(n)["Front"] for n in col.find_notes("")} == before_fronts
    # Including every [sound:] tag: this touches no sentence, so it can take a
    # recording off nothing, and that is checked before anything is synced.
    assert {n: col.get_note(n)["Example"] for n in col.find_notes("")} == before_examples
    assert {row[0]: row for row in col.db.all(
        "select id, nid, did, ord, type, queue, due, ivl, factor, reps, lapses from cards"
    )} == before_cards
    col.close()


def test_a_note_type_without_the_field_is_counted_not_dropped(tmp_path):
    col = _collection(tmp_path, ["chagrin"])
    other = col.models.new("No Back")
    col.models.add_field(other, col.models.new_field("Front"))
    template = col.models.new_template("Card 1")
    template["qfmt"], template["afmt"] = "{{Front}}", "{{Front}}"
    col.models.add_template(other, template)
    col.models.add(other)
    note = col.new_note(col.models.by_name("No Back"))
    note["Front"] = "on its own"
    col.add_note(note, col.decks.id("Default"))

    _, skipped = fold.plan(col, list(col.find_notes("")), "Back", _cache(tmp_path, "chagrin"))
    assert skipped["a note type without that field"] == 1
    col.close()


# ---- what it says it will cost -------------------------------------------

def test_the_cost_is_stated_before_anything_is_spent():
    assert fold.estimate(0) == "about $0.00"
    # Around a fifth of a cent a word, so the whole collection is a few dollars
    # once and never again. If this number moves, the report moves with it.
    assert fold.estimate(1240).startswith("about $")
    assert 1.0 < float(fold.estimate(1240).removeprefix("about $")) < 10.0


def test_undo_stays_inside_the_decks_it_was_given(tmp_path):
    """--deck is a promise. An undo that ignored it would take folds off cards
    in a deck nobody asked about, which is the sort of thing you find out about
    a month later."""
    col = _collection(tmp_path, ["chagrin", "halcyon"])
    where = _cache(tmp_path, "chagrin", "halcyon")
    note_ids = list(col.find_notes(""))
    items, _ = fold.plan(col, note_ids, "Back", where)
    blocks = {i["note_id"]: fold.block(json.loads(
        (where / f"{i['key']}.json").read_text(encoding="utf-8"))) for i in items}
    fold.fold_all(col, blocks, "Back")

    keep = [i["note_id"] for i in items if i["word"] == "halcyon"]
    drop = [i["note_id"] for i in items if i["word"] == "chagrin"]
    assert fold.unfold_all(col, "Back", drop)["touched"] == 1
    assert _back(col, "chagrin") == "chagrin"
    assert "<details" in _back(col, "halcyon")
    assert fold.folded_already(col, keep, "Back") == 1
    col.close()


# ---- the whole thing, run the way the workflow runs it -------------------

def _run(monkeypatch, tmp_path, *args):
    monkeypatch.setattr(sys, "argv", ["fold_meaning.py", *args])
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    return fold.main()


def test_a_dry_run_writes_the_plan_and_changes_nothing(tmp_path, monkeypatch):
    col = _collection(tmp_path, ["chagrin", "sesquipedalian"])
    col.close()
    where = _cache(tmp_path, "chagrin")
    out = tmp_path / "out"

    assert _run(monkeypatch, tmp_path,
                "--local-collection", str(tmp_path / "collection.anki2"),
                "--cache-dir", str(where), "--out-dir", str(out)) == 0

    plan = [json.loads(line) for line in
            (out / "fold.jsonl").read_text(encoding="utf-8").splitlines() if line]
    assert {i["word"]: i["have"] for i in plan} == {"chagrin": True, "sesquipedalian": False}
    assert not (out / "before-fold.colpkg").exists()

    from anki.collection import Collection
    col = Collection(str(tmp_path / "collection.anki2"))
    assert _back(col, "chagrin") == "chagrin"
    col.close()


def test_apply_folds_it_and_a_second_run_does_nothing(tmp_path, monkeypatch):
    col = _collection(tmp_path, ["chagrin"])
    col.close()
    where = _cache(tmp_path, "chagrin")
    out = tmp_path / "out"
    flags = ("--local-collection", str(tmp_path / "collection.anki2"),
             "--cache-dir", str(where), "--out-dir", str(out), "--apply")

    assert _run(monkeypatch, tmp_path, *flags) == 0

    from anki.collection import Collection
    col = Collection(str(tmp_path / "collection.anki2"))
    folded = _back(col, "chagrin")
    assert folded == "chagrin" + FIXTURE["html"]
    col.close()

    assert _run(monkeypatch, tmp_path, *flags) == 0
    col = Collection(str(tmp_path / "collection.anki2"))
    assert _back(col, "chagrin") == folded
    col.close()


def test_undo_through_the_command_line_needs_apply_as_well(tmp_path, monkeypatch):
    col = _collection(tmp_path, ["chagrin"])
    col.close()
    where = _cache(tmp_path, "chagrin")
    out = tmp_path / "out"
    base = ("--local-collection", str(tmp_path / "collection.anki2"),
            "--cache-dir", str(where), "--out-dir", str(out))

    _run(monkeypatch, tmp_path, *base, "--apply")

    from anki.collection import Collection
    # Undo without apply looks and reports, and takes nothing out.
    assert _run(monkeypatch, tmp_path, *base, "--undo") == 0
    col = Collection(str(tmp_path / "collection.anki2"))
    assert "<details" in _back(col, "chagrin")
    col.close()

    assert _run(monkeypatch, tmp_path, *base, "--undo", "--apply") == 0
    col = Collection(str(tmp_path / "collection.anki2"))
    assert _back(col, "chagrin") == "chagrin"
    col.close()


def test_asking_without_a_key_stops_before_anything_is_opened(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(SystemExit):
        _run(monkeypatch, tmp_path,
             "--local-collection", str(tmp_path / "nothing.anki2"),
             "--out-dir", str(tmp_path / "out"), "--ask", "5")
    said = capsys.readouterr().out
    assert "ANTHROPIC_API_KEY is not set" in said
    # And it says the thing that still works, rather than only what does not.
    assert "costs nothing" in said


# ---- buying the ones nobody has looked up --------------------------------

class _Answer:
    def __init__(self, word, recognised=True):
        self.word, self.recognised = word, recognised

    def model_dump(self):
        return dict(FIXTURE["info"], word=self.word, recognised=self.recognised)


def test_each_answer_is_committed_as_it_lands(tmp_path, monkeypatch):
    """Not at the end. A run that dies forty words in has still paid for forty,
    and they should be on disk rather than in a traceback."""
    where = tmp_path / "lookups"
    asked = []

    def fake(word, conversation, model=""):
        asked.append(word)
        if word == "third":
            raise RuntimeError("the model was busy")
        if word == "fourth":
            return _Answer(word, recognised=False)
        return _Answer(word)

    monkeypatch.setattr(fold, "ask_about", fake)
    monkeypatch.setattr(fold, "client", lambda: object())
    count, failed = fold.ask_for(["first", "second", "third", "fourth"], where)

    assert count == 2
    assert asked == ["first", "second", "third", "fourth"]
    assert sorted(p.name for p in where.glob("*.json")) == ["first.json", "second.json"]
    # A word the model says is not a word is not cached: cached, it would answer
    # for the correct spelling too.
    assert [f.split(" ")[0] for f in failed] == ["third", "fourth"]


def test_a_word_bought_here_is_readable_by_the_page(tmp_path, monkeypatch):
    """Same shape, same indentation, same directory as anki/explain.py writes,
    because the page reads both without knowing which wrote them."""
    where = tmp_path / "lookups"
    monkeypatch.setattr(fold, "ask_about", lambda w, c, model="": _Answer(w))
    monkeypatch.setattr(fold, "client", lambda: object())
    fold.ask_for(["chagrin"], where)

    on_disk = json.loads((where / "chagrin.json").read_text(encoding="utf-8"))
    assert on_disk["word"] == "chagrin"
    assert fold.block(on_disk) == FIXTURE["html"]
    assert (where / "chagrin.json").read_text(encoding="utf-8").endswith("\n")


def test_it_gives_up_rather_than_failing_nine_hundred_times(tmp_path, monkeypatch):
    """A key with no credit is the same answer for every word. Saying so once
    per word, nine hundred times, is a report nobody can read."""
    tried = []

    def broken(word, conversation, model=""):
        tried.append(word)
        raise RuntimeError("your credit balance is too low")

    monkeypatch.setattr(fold, "ask_about", broken)
    monkeypatch.setattr(fold, "client", lambda: object())
    count, failed = fold.ask_for([f"word{n}" for n in range(40)], tmp_path / "lookups")

    assert count == 0
    assert len(tried) == fold.GIVE_UP_AFTER
    assert failed[-1] == f"…and {40 - fold.GIVE_UP_AFTER} not attempted"


def test_one_bad_word_in_the_middle_does_not_stop_the_rest(tmp_path, monkeypatch):
    def flaky(word, conversation, model=""):
        if word == "third":
            raise RuntimeError("just that one")
        return _Answer(word)

    monkeypatch.setattr(fold, "ask_about", flaky)
    monkeypatch.setattr(fold, "client", lambda: object())
    count, failed = fold.ask_for(["first", "second", "third", "fourth", "fifth"],
                                 tmp_path / "lookups")
    assert count == 4
    assert len(failed) == 1
