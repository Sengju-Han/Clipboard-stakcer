"""The string handling in the workflows that talk to Anki.

None of this needs a collection or a key. All of it decides what ends up in
somebody's cards, and the mistakes it can make are quiet ones: a filename that
changes when the text has not, a field split on the wrong character, a sound
tag read back into the text it was supposed to be stripped from.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "anki"))

# Every script that touches a collection imports the Anki library at the top,
# so even its pure string helpers cannot be reached without it. It is in
# test/python/requirements.txt; this keeps the file runnable without it rather
# than failing to collect.
pytest.importorskip("anki.collection",
                    reason="the Anki library is not installed: pip install -r test/python/requirements.txt")

import add_card                                                  # noqa: E402
import build_tts_apkg as tts                                     # noqa: E402


def test_a_field_splits_on_the_first_equals_only():
    # A value can contain an equals sign; a field name cannot.
    assert add_card.parse_field("Front=hello") == ("Front", "hello")
    assert add_card.parse_field("Back=a = b") == ("Back", "a = b")
    assert add_card.parse_field(" Example = spaced ") == ("Example", " spaced ")
    assert add_card.parse_field("Empty=") == ("Empty", "")


def test_a_field_without_a_name_is_refused():
    with pytest.raises(SystemExit):
        add_card.parse_field("no-equals-sign")
    with pytest.raises(SystemExit):
        add_card.parse_field("=value")


def test_what_gets_spoken_is_the_first_block_with_anything_in_it():
    # The hook and everything after the first break belong to the reader, not
    # to the speech engine.
    assert tts.speakable("Just tell me.<br>a hunter beating the bush") == "Just tell me."

    # Anki's editor writes a multi-line field as <div>line</div><div>line</div>,
    # so the text before the first break is the empty string. Taking it gave
    # nothing to speak, and two fields in a 1,177-card collection start that
    # way — they would have been skipped with no explanation.
    assert tts.speakable("<div>One</div><div>Two</div>") == "One"
    assert tts.speakable("<br>One") == "One"
    assert tts.speakable("<div>Only one line</div>") == "Only one line"
    assert tts.speakable("   <div>After some space</div>") == "After some space"
    # A block holding nothing but a sound tag is not something to speak either.
    assert tts.speakable("<div>[sound:x.mp3]</div><div>Real words</div>") == "Real words"
    # And nothing at all is still nothing.
    assert tts.speakable("<div></div><br>") == ""


def test_what_gets_spoken_has_no_markup_or_media_in_it():
    assert tts.speakable("<b>Hello</b> there") == "Hello there"
    assert tts.speakable("Hello [sound:old.mp3]") == "Hello"
    assert tts.speakable("a &amp; b") == "a & b"
    assert tts.speakable("spaced   out") == "spaced out"
    assert tts.speakable("non breaking") == "non breaking"
    assert tts.speakable("") == ""


def test_the_filename_follows_the_text_and_nothing_else():
    # Content-addressed on purpose: a re-run skips what is already generated,
    # and that only holds if the same sentence always maps to the same file.
    one = tts.audio_name("The politician avowed his commitment.")
    again = tts.audio_name("The politician avowed his commitment.")
    other = tts.audio_name("The politician avowed his commitments.")
    assert one == again
    assert one != other
    assert one.endswith(".mp3")
    assert one.startswith(tts.NAME_PREFIX)


def test_markup_around_a_sentence_does_not_change_its_filename():
    # The name hashes what is spoken, so wrapping a sentence in bold must not
    # make the whole collection regenerate.
    plain = tts.speakable("The politician avowed it.")
    bolded = tts.speakable("<b>The politician avowed it.</b>")
    assert plain == bolded
    assert tts.audio_name(plain) == tts.audio_name(bolded)


def test_the_voice_is_part_of_the_filename():
    # Without this a second voice can never be heard: the file the first voice
    # made is already there, so the note is skipped as done. Changing voices
    # was impossible for exactly this reason.
    text = "The politician avowed it."
    ava = tts.audio_name(text, "en-US-AvaNeural")
    andrew = tts.audio_name(text, "en-US-AndrewNeural")
    assert ava != andrew
    assert ava == tts.audio_name(text, "en-US-AvaNeural")
    assert ava.startswith(tts.NAME_PREFIX) and ava.endswith(".mp3")

    # And the files made before voices were named still hash the text alone,
    # so nothing already in a collection changes name under its own rules.
    assert tts.audio_name(text, "") == tts.audio_name(text)


def test_our_own_recording_is_told_apart_from_somebody_elses():
    ours = "[sound:ttsex-0123456789abcdef.mp3]"
    theirs = "[sound:my-own-voice.mp3]"
    assert tts.OUR_SOUND.findall(f"A sentence. {ours}") == ["ttsex-0123456789abcdef.mp3"]
    assert tts.OUR_SOUND.findall(f"A sentence. {theirs}") == []
    # Stripping ours leaves theirs, which is how a field holding both is caught.
    assert "[sound:" in tts.OUR_SOUND.sub("", f"A sentence. {ours} {theirs}")


def test_re_voicing_replaces_the_tag_instead_of_adding_a_second():
    before = "The politician avowed it. [sound:ttsex-old.mp3]"
    after = tts.wants(before, "ttsex-new.mp3", True)
    assert after == "The politician avowed it. [sound:ttsex-new.mp3]"
    assert after.count("[sound:") == 1

    # A first recording still appends, and the space before the tag is the
    # same one either way - the field is compared byte for byte after import.
    assert tts.wants("The politician avowed it.", "ttsex-new.mp3", False) == \
        "The politician avowed it. [sound:ttsex-new.mp3]"


def _collection(tmp_path, examples):
    """A real collection holding one note per example, on the Basic note type."""
    from anki.collection import Collection

    col = Collection(str(tmp_path / "collection.anki2"))
    notetype = col.models.new("Speakable")
    for name in ("Front", "Example"):
        col.models.add_field(notetype, col.models.new_field(name))
    template = col.models.new_template("Card 1")
    template["qfmt"], template["afmt"] = "{{Front}}", "{{Example}}"
    col.models.add_template(notetype, template)
    col.models.add(notetype)
    notetype = col.models.by_name("Speakable")
    for index, value in enumerate(examples):
        note = col.new_note(notetype)
        note["Front"], note["Example"] = f"word {index}", value
        col.add_note(note, col.decks.id("Default"))
    return col


def test_re_voicing_leaves_a_recording_somebody_made_themselves_alone(tmp_path):
    # The one thing here that cannot be regenerated. A note holding a hand-made
    # recording must come out of planning untouched, even though the run was
    # asked to replace audio - including when it holds one of each.
    sentence = "The politician avowed it."
    ours = tts.audio_name(sentence)  # named the old way, before voices
    col = _collection(tmp_path, [
        f"{sentence} [sound:{ours}]",
        f"{sentence} [sound:i-said-this-myself.mp3]",
        f"{sentence} [sound:{ours}] [sound:i-said-this-myself.mp3]",
        sentence,
    ])
    note_ids = list(col.find_notes(""))

    items, skipped = tts.plan_notes(col, note_ids, "Example", "en-US-AndrewNeural", revoice=True)
    planned = {item["note_id"] for item in items}

    assert skipped["has a recording we did not make"] == 2
    assert note_ids[1] not in planned and note_ids[2] not in planned
    # Ours is re-recorded, and the note with no audio at all still gets some.
    assert planned == {note_ids[0], note_ids[3]}
    assert {i["replaces"] for i in items} == {ours, ""}
    col.close()


def test_re_voicing_twice_in_the_same_voice_changes_nothing(tmp_path):
    # Idempotency: the second run has to be a no-op, or every run rewrites
    # every note and the .apkg stops being a small update.
    sentence = "The politician avowed it."
    voice = "en-US-AndrewNeural"
    col = _collection(tmp_path, [f"{sentence} [sound:{tts.audio_name(sentence, voice)}]"])
    note_ids = list(col.find_notes(""))

    items, skipped = tts.plan_notes(col, note_ids, "Example", voice, revoice=True)
    assert items == []
    assert skipped["already in this voice"] == 1

    # And a different voice is work again.
    items, _ = tts.plan_notes(col, note_ids, "Example", "en-US-EmmaNeural", revoice=True)
    assert len(items) == 1
    col.close()


def test_without_re_voicing_a_note_with_audio_is_still_skipped(tmp_path):
    # The default has to stay what it was: running the workflow twice must not
    # give anybody a note with two play buttons.
    col = _collection(tmp_path, ["A sentence. [sound:ttsex-old.mp3]", "No audio here."])
    note_ids = list(col.find_notes(""))

    items, skipped = tts.plan_notes(col, note_ids, "Example", "en-US-AvaNeural")
    assert skipped["already has audio"] == 1
    assert [i["note_id"] for i in items] == [note_ids[1]]
    col.close()
