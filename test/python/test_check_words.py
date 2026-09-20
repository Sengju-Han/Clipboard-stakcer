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

import json
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
    # And it stops being something the report can call corrected, which is the
    # difference between saying what happened and saying what was planned.
    assert fixes[0]["corrected"] == ""
    assert "changed somewhere else" in fixes[0]["why"]
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


# ---- the whole thing, end to end ----------------------------------------

class _Parsed:
    def __init__(self, results):
        self.parsed_output = type("Out", (), {"results": results})()
        self.stop_reason = "end_turn"


class _FakeClaude:
    """Answers the way the real one does, from a table keyed by the word.

    The judging is the one part of this that cannot be checked for real without
    spending money, and it is also the part that decides what gets written into
    somebody's collection. So it is faked at the transport rather than at the
    function: the prompt is built, the batching happens, and the answers come
    back through the same parsing the API's would.
    """

    ANSWERS = {
        "hidious": ("typo", "hideous", ""),
        "entiments": ("typo", "sentiments", ""),
        "avow": ("form", "", "the sentence has avowed"),
        "beatnik": ("typo", "hipster", "the sentence describes one without naming it"),
        "alliteration": ("fine", "", "the sentence demonstrates it"),
    }

    def __init__(self):
        self.asked = []
        self.messages = self

    def parse(self, *, model, max_tokens, system, messages, output_format):
        import re as _re

        text = messages[0]["content"]
        self.asked.append(text)
        results = []
        for index, word in enumerate(_re.findall(r"<word>(.*?)</word>", text, _re.S)):
            verdict, corrected, why = self.ANSWERS.get(word.strip(), ("fine", "", ""))
            results.append(words.Judged(index=index, verdict=verdict,
                                        corrected=corrected, why=why))
        return _Parsed(results)


def _run(tmp_path, monkeypatch, col_path, apply=False):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api03-not-a-real-key")
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(tmp_path / f"summary-{apply}.md"))
    fake = _FakeClaude()
    monkeypatch.setattr(words, "client", lambda: fake)
    argv = ["check_words.py", "--local-collection", str(col_path),
            "--out-dir", str(tmp_path / "out")]
    if apply:
        argv.append("--apply")
    monkeypatch.setattr(sys, "argv", argv)
    assert words.main() == 0
    return fake, (tmp_path / f"summary-{apply}.md").read_text(encoding="utf-8")


def _deck(tmp_path):
    col = _collection(tmp_path, [
        ("a", "hidious", "That's funny because I'm hideous."),
        ("b", "entiments<br><i>감정</i>", "He hid his sentiments well."),
        ("c", "avow", "The politician avowed his commitment."),
        ("d", "beatnik", "there was this cool unconventional type guy"),
        ("e", "alliteration", "Peter Piper picked a peck of pickled peppers."),
        ("f", "hideous", "That's funny because I'm hideous."),
        # A second card for a word that is already here, and one with no
        # sentence at all: the half that needs no key has to have something to
        # find, or a run that reports nothing proves nothing.
        ("g", "hideous", "A quite separate hideous thing."),
        ("h", "transduction", ""),
    ])
    path = col.path
    col.close()
    return path


def test_looking_changes_nothing(tmp_path, monkeypatch):
    from anki.collection import Collection

    path = _deck(tmp_path)
    fake, said = _run(tmp_path, monkeypatch, path)

    # Only the cards whose word is missing were sent; the one that is fine
    # never left the machine.
    asked = "\n".join(fake.asked)
    assert "hidious" in asked and "entiments" in asked
    assert asked.count("<word>hideous</word>") == 0

    assert "2 misspelled words" in said
    assert "`hidious`" in said and "**hideous**" in said
    assert "Re-run with **apply**" in said
    # The half that needs no key rides along on a judged run too.
    assert "And while it was looking" in said
    assert "`hideous` — 2 cards" in said
    assert "`transduction`" in said

    col = Collection(str(path))
    assert _field(col, "a", "Back") == "hidious"     # untouched
    col.close()


def test_applying_writes_only_the_respellings(tmp_path, monkeypatch):
    from anki.collection import Collection

    path = _deck(tmp_path)
    _run(tmp_path, monkeypatch, path, apply=True)

    col = Collection(str(path))
    assert _field(col, "a", "Back") == "hideous"
    # The hook under the word survives the correction.
    assert _field(col, "b", "Back") == "sentiments<br><i>감정</i>"
    # An inflection is not a typo, and is left exactly as it was.
    assert _field(col, "c", "Back") == "avow"
    # And the one the model wanted to replace rather than respell is refused,
    # which is the only thing here that could quietly ruin a card.
    assert _field(col, "d", "Back") == "beatnik"
    assert _field(col, "e", "Back") == "alliteration"
    # No sentence moved.
    assert _field(col, "a", "Example") == "That's funny because I'm hideous."
    col.close()


def test_what_it_refused_is_written_down(tmp_path, monkeypatch):
    path = _deck(tmp_path)
    _, said = _run(tmp_path, monkeypatch, path)
    assert "not a respelling" in said
    assert "beatnik" in said

    ledger = (tmp_path / "out" / "words.jsonl").read_text(encoding="utf-8")
    rows = [json.loads(line) for line in ledger.splitlines() if line.strip()]
    beatnik = next(r for r in rows if r["word"] == "beatnik")
    assert beatnik["corrected"] == ""
    assert "hipster" in beatnik["why"], "the answer it refused has to be recoverable"


# ---- what a rule can see on its own --------------------------------------

def test_the_same_word_on_two_cards_is_named(tmp_path):
    col = _collection(tmp_path, [
        ("a", "revelation", "It was a revelation."),
        ("b", "revelation", "Another revelation entirely."),
        ("c", "avow", "He avowed it."),
    ])
    faults = words.other_faults(col, list(col.find_notes("")), "Back", "Example")

    assert [r["word"] for r in faults["twice"]] == ["revelation"]
    assert faults["twice"][0]["count"] == 2
    col.close()


def test_a_card_with_no_sentence_is_named(tmp_path):
    col = _collection(tmp_path, [
        ("a", "transduction", ""),
        ("b", "avow", "He avowed it."),
        ("c", "twitching", "   <br>  "),     # markup and nothing else
    ])
    faults = words.other_faults(col, list(col.find_notes("")), "Back", "Example")

    assert sorted(r["word"] for r in faults["no_example"]) == ["transduction", "twitching"]
    col.close()


def test_a_clue_that_says_the_answer_is_named(tmp_path):
    col = _collection(tmp_path, [
        # The front of the card is the word it is asking for.
        ("ditch", "ditch", "They ditched the plan."),
        ("jockey on back of his horse", "jockey", "The jockey rode well."),
        ("포기하다", "abandon", "They abandoned it."),
    ])
    faults = words.other_faults(col, list(col.find_notes("")), "Back", "Example")

    given = sorted(r["word"] for r in faults["gives_it_away"])
    assert given == ["ditch", "jockey"]
    assert "abandon" not in given
    col.close()


def test_a_short_word_inside_a_longer_one_is_not_a_giveaway(tmp_path):
    # "vice" is inside "advice"; the clue does not give the answer away.
    col = _collection(tmp_path, [("some advice for you", "vice", "He had his vices.")])
    faults = words.other_faults(col, list(col.find_notes("")), "Back", "Example")
    assert faults["gives_it_away"] == []
    col.close()


def test_the_report_carries_them(tmp_path, monkeypatch):
    where = tmp_path / "with-faults.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(where))
    words.write_report([], {"the word is there": 10}, 10, applied=False, faults={
        "twice": [{"word": "revelation", "count": 2}],
        "no_example": [{"word": "transduction"}],
        "gives_it_away": [{"word": "ditch", "field": "Front", "clue": "ditch"}],
    })
    said = where.read_text(encoding="utf-8")

    assert "And while it was looking" in said
    # The heading counts the words, not the cards: "1 word on more than one
    # card" is the fact; "the same word on 1 card" is a different and wronger one.
    assert "1 word on more than one card" in said
    assert "`revelation` — 2 cards" in said
    assert "`transduction`" in said
    assert "`ditch`" in said
    assert "None of this is changed" in said


def test_without_a_key_it_still_says_what_it_can(tmp_path, monkeypatch):
    # The rule-based half needs nothing, and a run with no key is worth more
    # than a refusal: the duplicates and the empty cards are still findings.
    path = _deck(tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(tmp_path / "nokey.md"))
    monkeypatch.setattr(words, "client", lambda: pytest.fail("it must not ask without a key"))
    monkeypatch.setattr(sys, "argv", ["check_words.py", "--local-collection", str(path),
                                      "--out-dir", str(tmp_path / "out2")])
    assert words.main() == 0
    said = (tmp_path / "nokey.md").read_text(encoding="utf-8")

    # It must not say "0 misspelled", which reads as "checked, and they were
    # all fine" when the truth is that nothing was checked. That is the worse
    # of the two wrong answers because it is the reassuring one.
    assert "misspelled" not in said.split("And while it was looking")[0].lower() \
        or "unjudged" in said
    assert "unjudged" in said
    assert "ANTHROPIC_API_KEY" in said
    # The words are still listed, with what their sentence has instead.
    assert "`hidious`" in said and "hideous" in said
    # But nothing is claimed as a correction.
    assert "**hideous**" not in said
    # And the rule-based half, which needs no key, is all there.
    assert "And while it was looking" in said


def test_without_a_key_apply_is_refused(tmp_path, monkeypatch):
    path = _deck(tmp_path)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(tmp_path / "nokey2.md"))
    monkeypatch.setattr(sys, "argv", ["check_words.py", "--local-collection", str(path),
                                      "--out-dir", str(tmp_path / "out3"), "--apply"])
    with pytest.raises(SystemExit):
        words.main()


def test_a_sentence_used_by_two_cards_is_named(tmp_path):
    # Mining two words from one sentence is efficient and it is also two cards
    # that give each other away: seeing "the gymnast showed incredible agility"
    # on the card for `gymnast` answers the card for `agility`.
    shared = "The gymnast showed incredible agility during her routine."
    col = _collection(tmp_path, [
        ("a", "agility", shared),
        ("b", "gymnast", shared),
        ("c", "avow", "He avowed it plainly."),
    ])
    faults = words.other_faults(col, list(col.find_notes("")), "Back", "Example")

    assert len(faults["shared"]) == 1
    assert sorted(faults["shared"][0]["words"]) == ["agility", "gymnast"]
    col.close()


def test_two_cards_with_a_very_short_sentence_in_common_are_not(tmp_path):
    # Three words is the floor. Below it, two cards sharing "they're loaded"
    # is a coincidence rather than a mined pair.
    col = _collection(tmp_path, [("a", "loaded", "they're loaded"),
                                 ("b", "flush", "they're loaded")])
    faults = words.other_faults(col, list(col.find_notes("")), "Back", "Example")
    assert faults["shared"] == []
    col.close()


def test_an_example_that_is_only_the_word_is_named(tmp_path):
    col = _collection(tmp_path, [
        ("a", "ensconce", "ensconce"),
        ("b", "Ambassador", "ambassador"),
        ("c", "casserole", "a chicken casserole"),   # short, but a sentence
        ("d", "avow", "He avowed it plainly."),
    ])
    faults = words.other_faults(col, list(col.find_notes("")), "Back", "Example")

    assert sorted(r["word"] for r in faults["no_context"]) == ["Ambassador", "ensconce"]
    # And it is not counted as having no example, which is a different problem.
    assert faults["no_example"] == []
    col.close()


def test_the_report_carries_the_two_new_ones(tmp_path, monkeypatch):
    where = tmp_path / "more-faults.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(where))
    words.write_report([], {"the word is there": 10}, 10, applied=False, faults={
        "no_context": [{"word": "ensconce"}],
        "shared": [{"words": ["agility", "gymnast"],
                    "sentence": "the gymnast showed incredible agility"}],
    })
    said = where.read_text(encoding="utf-8")

    assert "whose example is just the word" in said and "`ensconce`" in said
    assert "used by two cards or more" in said
    assert "`agility`" in said and "`gymnast`" in said


def test_a_list_that_is_cut_short_says_so(tmp_path, monkeypatch):
    # A report that stops at sixty of a hundred and sixty-seven looks like a
    # report of sixty, and the rest are exactly the ones nobody looks at.
    where = tmp_path / "long.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(where))
    words.write_report(
        [{"word": f"word{i:03d}", "nearest": "x"} for i in range(167)],
        {"the word is there": 1000}, 1240, applied=False,
        faults={"no_example": [{"word": f"e{i}"} for i in range(55)]})
    said = where.read_text(encoding="utf-8")

    assert "…and 107 more" in said, "167 listed 60 at a time leaves 107"
    assert "…and 15 more" in said, "55 empty cards listed 40 at a time leaves 15"
    assert "words.jsonl" in said


def test_a_list_that_fits_says_nothing_extra(tmp_path, monkeypatch):
    where = tmp_path / "short.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(where))
    words.write_report([{"word": "hidious", "nearest": "hideous"}],
                       {"the word is there": 10}, 11, applied=False, faults={})
    assert "more. The whole list" not in where.read_text(encoding="utf-8")


def test_a_field_name_that_matches_nothing_stops_the_run(tmp_path):
    # The likeliest way to run this for nothing. Every note is skipped, every
    # count is zero, and the report reads as a collection with nothing wrong
    # in it - wrong in the same reassuring direction as everything else here.
    col = _collection(tmp_path, [("a", "hidious", "I'm hideous.")])
    ids = list(col.find_notes(""))

    with pytest.raises(SystemExit):
        words.insist_on_fields(col, ids, "Word")       # it is called Back
    with pytest.raises(SystemExit):
        words.insist_on_fields(col, ids, "back")       # and names are case-sensitive
    # The ones that are really there pass.
    words.insist_on_fields(col, ids, "Back", "Example")
    assert words.field_names(col, ids) == {"Front", "Back", "Example"}
    col.close()


def test_the_wrong_field_name_says_what_is_there(tmp_path, capsys):
    col = _collection(tmp_path, [("a", "hidious", "I'm hideous.")])
    with pytest.raises(SystemExit):
        words.insist_on_fields(col, list(col.find_notes("")), "Vocabulary")
    said = capsys.readouterr().out
    assert "'Vocabulary'" in said
    assert "Back" in said and "Example" in said and "Front" in said
    col.close()


def test_a_run_that_stopped_partway_says_how_much_it_did_not_do(tmp_path, monkeypatch):
    # A failing batch breaks the loop, so a run can end with some words judged
    # and some not. Those belong to none of the four verdict lists and would
    # simply vanish from the counts - "quietly incomplete" arriving by another
    # door.
    where = tmp_path / "partial.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(where))
    rows = [
        {"word": "hidious", "nearest": "hideous", "verdict": "typo", "corrected": "hideous"},
        {"word": "avow", "nearest": "avowed", "verdict": "form", "corrected": ""},
        {"word": "sraggly", "nearest": "scraggly"},        # never reached
        {"word": "survile", "nearest": "servile"},          # never reached
    ]
    words.write_report(rows, {"the word is there": 100}, 110, applied=False)
    said = where.read_text(encoding="utf-8")

    assert "**2 were not judged at all**" in said
    assert "Run it again to carry on" in said
    # And it is not the unjudged layout: two of them were judged.
    assert "unjudged" not in said.split("\n")[0]


def test_a_run_that_finished_says_nothing_about_stopping(tmp_path, monkeypatch):
    where = tmp_path / "whole.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(where))
    words.write_report(
        [{"word": "hidious", "nearest": "hideous", "verdict": "typo", "corrected": "hideous"}],
        {"the word is there": 100}, 110, applied=False)
    assert "not judged at all" not in where.read_text(encoding="utf-8")


def test_a_second_note_type_is_counted_not_swallowed(tmp_path):
    """A collection with two note types, which is the ordinary case.

    The deck this was written against has 1,067 cards on one note type and 173
    on another. A note lacking the field was skipped with no counter at all, so
    a whole note type could vanish from every number on the report and the
    report would still look complete.
    """
    from anki.collection import Collection

    col = _collection(tmp_path, [("a", "hidious", "I'm hideous.")])
    other = col.models.new("Different")
    for name in ("Term", "Usage"):
        col.models.add_field(other, col.models.new_field(name))
    template = col.models.new_template("Card 1")
    template["qfmt"], template["afmt"] = "{{Term}}", "{{Usage}}"
    col.models.add_template(other, template)
    col.models.add(other)
    other = col.models.by_name("Different")
    for term in ("sraggly", "survile"):
        note = col.new_note(other)
        note["Term"], note["Usage"] = term, "a sentence without it"
        col.add_note(note, col.decks.id("Default"))

    found, skipped = words.collect(col, list(col.find_notes("")), "Back", "Example")

    assert [f["word"] for f in found] == ["hidious"]
    assert skipped["a note type without these fields"] == 2
    col.close()


def test_the_report_says_a_note_type_was_passed_over(tmp_path, monkeypatch):
    where = tmp_path / "other-type.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(where))
    words.write_report(
        [{"word": "hidious", "nearest": "hideous"}],
        {"the word is there": 1000, "a note type without these fields": 173},
        1240, applied=False, fields=("Back", "Example"))
    said = where.read_text(encoding="utf-8")

    assert "173 cards had no word checked" in said
    # Not "not looked at at all": the checks that need no model did walk them.
    assert "not looked at at all" not in said
    assert "`Back`" in said and "`Example`" in said


def test_nothing_is_said_when_every_note_had_the_fields(tmp_path, monkeypatch):
    where = tmp_path / "one-type.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(where))
    words.write_report([{"word": "hidious", "nearest": "hideous"}],
                       {"the word is there": 10, "a note type without these fields": 0},
                       11, applied=False, fields=("Back", "Example"))
    assert "had no word checked" not in where.read_text(encoding="utf-8")


# ---- an accent is a spelling ---------------------------------------------

def test_either_spelling_of_an_accented_word_matches():
    # A card can say `séance` while its sentence says `seance`, and they are
    # the same word. Without folding, the accent is not a letter, so it became
    # a space and split the word in half — `s` and `ance` — and whether a card
    # matched its own sentence depended on whether both were typed the same way.
    assert words.missing_from("séance", "We went to a seance last night.") == []
    assert words.missing_from("séance", "We went to a séance last night.") == []
    assert words.missing_from("seance", "We went to a séance last night.") == []
    # And a word that really is absent still reports as absent.
    assert words.missing_from("séance", "We went to a party last night.") == ["seance"]


def test_folding_leaves_korean_alone():
    # NFD takes Hangul apart as readily as it takes an accent off an e, and
    # every clue in this collection is Korean. The recomposition at the end is
    # the whole reason this is three steps rather than one.
    for korean in ("공언하다", "대화에 끼어들다", "감정"):
        assert words.fold(korean) == korean
    assert words.fold("séance") == "seance"
    assert words.fold("naïve café") == "naive cafe"


def test_the_accent_survives_into_the_card():
    # Folded for comparing, never for writing: the correction that goes back
    # into the collection is the word as a person spells it.
    assert words.headword("séance<br><i>a hook</i>") == "séance"
    assert words.tidy("séance") == "seance"
    # A correction is still judged on the word as written, accent and all, so
    # dropping the accent counts as a respelling and swapping the word does not.
    assert words.a_respelling("séance", "seance")
    assert not words.a_respelling("séance", "gathering")
