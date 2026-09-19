"""The word on the card, checked against the sentence it comes with.

The sentence audit leaves the word alone on purpose, so nothing has ever looked
at the word itself - and a card whose word is misspelled teaches the
misspelling every time it comes up. The collection this was written against has
`entiments` for *sentiments*, `hidious` for *hideous*, `achipelago`, `sraggly`,
`survile` and `bone marrorw`.

The risk in fixing them is the obvious one: a card is its word, and a
"correction" that swaps the word for a different one leaves a card with a
year's scheduling on it that is now about something else. That is what most of
this file is about.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "anki"))

pytest.importorskip("anki.collection",
                    reason="the Anki library is not installed: pip install -r test/python/requirements.txt")

import check_words as words                                      # noqa: E402


# ---- finding them --------------------------------------------------------

def test_a_word_that_is_in_its_sentence_is_not_reported():
    assert words.missing_from("hideous", "That's funny because I'm hideous") == []
    assert words.missing_from("bone marrow", "It is the bone marrow of software") == []


def test_a_misspelled_word_is():
    assert words.missing_from("hidious", "That's funny because I'm hideous") == ["hidious"]
    assert words.missing_from("bone marrorw", "It is the bone marrow of it") == ["marrorw"]


def test_an_inflection_is_reported_too_because_a_rule_cannot_tell():
    # `avow` against "he avowed it" reads exactly like a misspelling to any
    # rule: one is a few letters from the other. Both come through, and the
    # judging happens afterwards.
    assert words.missing_from("avow", "The politician avowed his commitment") == ["avow"]


def test_short_words_prove_nothing():
    # "of", "up", "in": present in half of all sentences by accident.
    assert words.missing_from("of", "nothing here") == []
    assert words.missing_from("gobble up", "he gobbled it") == ["gobble"]


def test_markup_and_recordings_are_not_words():
    example = "He was <b>hideous</b>. [sound:ttsex-abc.mp3]"
    assert words.missing_from("hideous", example) == []
    assert "sound" not in words.tidy(example)
    assert "ttsex" not in words.tidy(example)


def test_the_memory_hook_under_the_word_is_not_part_of_it():
    assert words.headword("entiments<br><i>vow and a!</i>") == "entiments"
    assert words.headword("bone marrow<div>the vital part</div>") == "bone marrow"
    assert words.headword("plain") == "plain"


def test_the_sentence_is_asked_what_it_has_instead():
    near, score = words.nearest("achipelago", "The Caribbean is a beautiful archipelago")
    assert near == "archipelago"
    assert score > 0.9


# ---- and not breaking the card while fixing them -------------------------

def test_a_respelling_is_allowed():
    assert words.a_respelling("entiments", "sentiments")
    assert words.a_respelling("hidious", "hideous")
    assert words.a_respelling("achipelago", "archipelago")
    assert words.a_respelling("bone marrorw", "bone marrow")


def test_a_different_word_is_not():
    # The failure that matters. A card is its word: `beatnik` corrected to
    # `hipster` is a card with a year of scheduling on it that is now about
    # something else, and nothing downstream would ever notice.
    assert not words.a_respelling("beatnik", "hipster")
    assert not words.a_respelling("deficit", "detriment")
    assert not words.a_respelling("vein of something", "a great deal")


def test_no_change_at_all_is_not_a_correction():
    assert not words.a_respelling("hideous", "hideous")
    assert not words.a_respelling("hideous", "")
    assert not words.a_respelling("hideous", "   ")


# ---- writing it back -----------------------------------------------------

def _collection(tmp_path, rows):
    from anki.collection import Collection

    col = Collection(str(tmp_path / "collection.anki2"))
    notetype = col.models.new("Worded")
    for name in ("Front", "Back", "Example"):
        col.models.add_field(notetype, col.models.new_field(name))
    template = col.models.new_template("Card 1")
    template["qfmt"], template["afmt"] = "{{Front}}", "{{Example}}"
    col.models.add_template(notetype, template)
    col.models.add(notetype)
    notetype = col.models.by_name("Worded")
    for front, back, example in rows:
        note = col.new_note(notetype)
        note["Front"], note["Back"], note["Example"] = front, back, example
        col.add_note(note, col.decks.id("Default"))
    return col


def _field(col, front, name):
    flds = col.db.scalar("select flds from notes where flds like ?", f"{front}\x1f%")
    return flds.split("\x1f")[{"Front": 0, "Back": 1, "Example": 2}[name]]


def _guid(col, front):
    return col.db.scalar("select guid from notes where flds like ?", f"{front}\x1f%")


def test_the_word_is_corrected_and_its_hook_left_alone(tmp_path):
    col = _collection(tmp_path, [
        ("a", "entiments<br><i>감정, remember it</i>", "He hid his sentiments well."),
        ("b", "hideous", "That's funny because I'm hideous."),
    ])
    fixes = [{"guid": _guid(col, "a"), "word": "entiments", "corrected": "sentiments"}]

    assert words.apply_all(col, fixes, "Back")["applied"] == 1
    # The word is right and the hook underneath it is untouched, byte for byte.
    assert _field(col, "a", "Back") == "sentiments<br><i>감정, remember it</i>"
    assert _field(col, "b", "Back") == "hideous"
    col.close()


def test_the_sentence_is_never_touched(tmp_path):
    col = _collection(tmp_path, [("a", "hidious", "That's funny because I'm hideous.")])
    fixes = [{"guid": _guid(col, "a"), "word": "hidious", "corrected": "hideous"}]

    words.apply_all(col, fixes, "Back")
    assert _field(col, "a", "Example") == "That's funny because I'm hideous."
    col.close()


def test_a_word_that_moved_since_it_was_read_is_left_alone(tmp_path):
    # Somebody fixed it on their phone between the report and the apply. The
    # ledger's idea of what is on the card is stale, and writing over it would
    # undo their edit.
    col = _collection(tmp_path, [("a", "sentiments", "He hid his sentiments.")])
    fixes = [{"guid": _guid(col, "a"), "word": "entiments", "corrected": "sentiments"}]

    assert words.apply_all(col, fixes, "Back")["applied"] == 0
    col.close()


def test_scheduling_and_recordings_are_checked_before_anything_is_sent(tmp_path):
    col = _collection(tmp_path, [
        ("a", "hidious", "I'm hideous. [sound:ttsex-a.mp3]"),
        ("b", "avow", "He avowed it. [sound:ttsex-b.mp3]"),
    ])
    before = words.recordings(col)
    fixes = [{"guid": _guid(col, "a"), "word": "hidious", "corrected": "hideous"}]

    words.apply_all(col, fixes, "Back")
    # Correcting a word takes no recording off anything: the sentences are
    # untouched, so every tag is where it was.
    assert words.recordings(col) == before
    col.close()


def test_a_collection_that_moved_underneath_it_stops_the_sync(tmp_path):
    col = _collection(tmp_path, [
        ("a", "hidious", "I'm hideous."),
        ("b", "avow", "He avowed it. [sound:ttsex-b.mp3]"),
    ])
    fixes = [{"guid": _guid(col, "a"), "word": "hidious", "corrected": "hideous"}]

    real = words.recordings

    def also_strip_the_audio(collection):
        out = real(collection)
        collection.db.execute("update notes set flds = replace(flds, ' [sound:', ' [gone:')")
        return out

    words.recordings = also_strip_the_audio
    try:
        with pytest.raises(SystemExit):
            words.apply_all(col, fixes, "Back")
    finally:
        words.recordings = real
    col.close()


def test_a_word_wearing_formatting_is_left_as_typed(tmp_path):
    # Bold, colour, a ruby annotation. Rewriting the line as plain text to fix
    # one letter throws the formatting away, which is the same trade the
    # sentence audit already refuses.
    col = _collection(tmp_path, [("a", "<b>hidious</b><br>the hook", "I'm hideous.")])
    fixes = [{"guid": _guid(col, "a"), "word": "hidious", "corrected": "hideous"}]

    assert words.apply_all(col, fixes, "Back")["applied"] == 0
    assert _field(col, "a", "Back") == "<b>hidious</b><br>the hook"
    assert "formatting" in fixes[0]["why"]
    assert fixes[0]["corrected"] == ""
    col.close()


def test_collect_finds_the_cards_worth_judging(tmp_path):
    col = _collection(tmp_path, [
        ("a", "hidious", "That's funny because I'm hideous."),
        ("b", "hideous", "That's funny because I'm hideous."),
        ("c", "avow", "The politician avowed his commitment."),
        ("d", "", "A card with no word at all."),
        ("e", "quokka", ""),
    ])
    found, skipped = words.collect(col, list(col.find_notes("")), "Back", "Example")

    assert sorted(f["word"] for f in found) == ["avow", "hidious"]
    assert skipped["the word is there"] == 1
    assert skipped["no word"] == 1
    assert skipped["no example"] == 1
    # And it carries what the sentence has instead, which is the whole hint.
    assert next(f for f in found if f["word"] == "hidious")["nearest"] == "hideous"
    col.close()


def test_the_report_names_the_card_and_what_it_should_say(tmp_path, monkeypatch):
    where = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(where))
    rows = [
        {"word": "hidious", "corrected": "hideous", "nearest": "hideous",
         "verdict": "typo", "example": "I'm hideous."},
        {"word": "avow", "corrected": "", "nearest": "avowed",
         "verdict": "form", "example": "He avowed it."},
        {"word": "deficit", "corrected": "", "nearest": "detriment", "verdict": "typo",
         "why": "the sentence is about a detriment", "example": "to my detriment"},
    ]
    words.write_report(rows, {"the word is there": 1200}, 1240, applied=False)
    said = where.read_text(encoding="utf-8")

    assert "1 misspelled word" in said
    assert "`hidious`" in said and "**hideous**" in said
    # An inflection is counted, not listed as something to fix.
    assert "`avow`" not in said
    # And the one the model wanted to replace rather than respell is set apart.
    assert "not a respelling" in said and "`deficit`" in said
    assert "Re-run with **apply**" in said
