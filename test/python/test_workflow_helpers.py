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


def test_a_voice_the_service_refuses_is_left_off_the_page(tmp_path, monkeypatch):
    # Voice names come and go, and one that no longer exists must not take the
    # whole page down with it: the point of the page is to compare the rest.
    import voice_samples

    async def fake(text, voice):
        if voice == "en-US-GoneNeural":
            raise RuntimeError("no such voice")
        if voice == "en-US-TruncatedNeural":
            return b"\x00" * 10  # the failure that imports fine and is silent
        return b"\x00" * (voice_samples.MIN_BYTES + 1)

    monkeypatch.setattr(voice_samples, "edge_audio", fake)
    monkeypatch.setattr(sys, "argv", [
        "voice_samples.py", "--out-dir", str(tmp_path),
        "--voices", "en-US-AvaNeural,en-US-GoneNeural,en-US-TruncatedNeural,en-GB-SoniaNeural",
    ])
    assert voice_samples.main() == 0

    import json
    data = json.loads((tmp_path / "index.json").read_text())
    assert [v["voice"] for v in data["voices"]] == ["en-US-AvaNeural", "en-GB-SoniaNeural"]
    # And nothing half-written is left on disk for the page to offer.
    assert sorted(f.name for f in tmp_path.glob("*.mp3")) == [
        "en-GB-SoniaNeural-1.mp3", "en-GB-SoniaNeural-2.mp3", "en-GB-SoniaNeural-3.mp3",
        "en-US-AvaNeural-1.mp3", "en-US-AvaNeural-2.mp3", "en-US-AvaNeural-3.mp3",
    ]


def test_the_samples_are_not_taken_from_a_collection():
    # This page is published. A sentence out of somebody's cards on it is a
    # study note on the open internet, so the sentences are fixed and neutral.
    import voice_samples

    assert len(voice_samples.SENTENCES) == 3
    assert all(s.strip() and s[0].isupper() for s in voice_samples.SENTENCES)

# --------------------------------------------------------------------------
# the guard on a correction that replaces the word the card exists for
# --------------------------------------------------------------------------

import proofread                                                 # noqa: E402


def test_a_correction_may_not_take_away_the_word_the_card_is_for():
    # All three came back from a real audit of a real collection, against a
    # prompt that already forbids exactly this. A prompt is not a guarantee.
    assert not proofread.keeps_the_word(
        "Mary deposited the baby in the crib.",
        "Mary placed the baby in the crib.", "deposit")
    assert not proofread.keeps_the_word(
        "At prima facie, the project seemed successful.",
        "At first glance, the project seemed successful.", "prima facie")
    assert not proofread.keeps_the_word(
        "She has a shiesty reputation for cheating in games.",
        "She has a shady reputation for cheating in games.", "shiesty")


def test_an_ordinary_correction_is_not_refused():
    # The guard is worth nothing if it also blocks the typo fixes, which are
    # most of what an audit finds. Every one of these is from the same run.
    kept = [
        ("The artist's lamboyant style", "The artist's flamboyant style", "flamboyant"),
        ("he threatend to disown her", "he threatened to disown her", "threaten"),
        ("the ice glistened under the stars", "The ice glistened under the stars.", "glisten"),
        ("He intimated that he might be leaving soon",
         "He intimated that he might be leaving soon.", "intimate"),
        ("she suffered form ectopic pregnancy",
         "she suffered from ectopic pregnancy", "ectopic pregnancy"),
        ("they have way too much ball of neuroticism",
         "They have way too much ball of neuroticism.", "ball of neuroticism"),
        ("I demurred when Jobs aske me to write his biograhpy",
         "I demurred when Jobs asked me to write his biography", "demur"),
    ]
    for was, now, target in kept:
        assert proofread.keeps_the_word(was, now, target), target


def test_a_word_the_sentence_never_had_is_not_guarded():
    # The card for 'flamboyant' whose sentence says 'lamboyant' is the reason:
    # the word was never in the sentence to lose, and the correction putting it
    # there is the whole point.
    assert proofread.keeps_the_word("a lamboyant style", "a flamboyant style", "flamboyant")
    # And a word that was there and stays, however the rest is rewritten.
    assert proofread.keeps_the_word(
        "he has been scattershot friendly to me",
        "he has been scattershot in his friendliness to me", "scattershot")


def test_short_words_in_a_target_prove_nothing():
    # 'of', 'up' and 'a' are in every sentence. Counting them would make the
    # guard say yes to everything.
    assert proofread._stem("of") == "of"
    assert not proofread.keeps_the_word(
        "she put off the meeting", "she postponed the meeting", "put off")
    # ...but a target that is only short words cannot be checked at all, and
    # saying so is better than refusing every correction on those cards.
    assert proofread.keeps_the_word("it is up to you", "It is up to you.", "up to")


def test_an_inflection_still_counts_as_the_word():
    assert proofread.keeps_the_word("he deposits it", "He deposits it.", "deposited")
    assert proofread.keeps_the_word("the glistening ice", "The glistening ice.", "glisten")
    assert proofread.keeps_the_word("she caressed him", "She caressed him.", "caress")


# --------------------------------------------------------------------------
# recordings that go missing
#
# Every job here checks its own notes and none of them looks any further, which
# is the blind spot a thousand [sound:] tags disappeared through between the
# evening of one day and the afternoon of the next. Nothing reported it: the
# files stayed in the media folder, the scheduling was untouched, and the cards
# simply stopped having a play button.
# --------------------------------------------------------------------------

def _with_audio(tmp_path, sentences):
    """A collection where every note's Example ends in a recording."""
    return _collection(tmp_path, [f"{s} [sound:ttsex-{i}.mp3]" for i, s in enumerate(sentences)])


def test_every_recording_in_the_collection_is_counted(tmp_path):
    col = _with_audio(tmp_path, ["One.", "Two."])
    # And one note carrying two, which is what re-voicing looks like mid-flight.
    note = col.get_note(list(col.find_notes(""))[0])
    note["Front"] = note["Front"] + " [sound:extra.mp3]"
    col.update_note(note)

    found = add_card.recordings(col)
    assert sorted(name for names in found.values() for name in names) == \
        ["extra.mp3", "ttsex-0.mp3", "ttsex-1.mp3"]
    col.close()


def test_a_note_with_no_audio_is_simply_absent(tmp_path):
    col = _collection(tmp_path, ["No audio here.", "Nor here."])
    assert add_card.recordings(col) == {}
    col.close()


def test_losing_a_recording_stops_the_sync(tmp_path):
    col = _with_audio(tmp_path, ["One.", "Two.", "Three."])
    before = add_card.recordings(col)

    note = col.get_note(list(col.find_notes(""))[1])
    note["Example"] = "Two."          # the tag, quietly gone
    col.update_note(note)

    with pytest.raises(SystemExit):
        add_card.check_recordings_kept(before, add_card.recordings(col))
    col.close()


def test_a_loss_that_was_asked_for_is_allowed(tmp_path):
    # The audit drops the recording on every sentence it corrects, because the
    # recording says the old wording. That many, and not one more.
    col = _with_audio(tmp_path, ["One.", "Two.", "Three."])
    before = add_card.recordings(col)

    ids = list(col.find_notes(""))
    for note_id in ids[:2]:
        note = col.get_note(note_id)
        note["Example"] = note["Example"].split(" [sound:")[0]
        col.update_note(note)

    after = add_card.recordings(col)
    add_card.check_recordings_kept(before, after, expected=2)     # fine
    with pytest.raises(SystemExit):
        add_card.check_recordings_kept(before, after, expected=1)  # one too many
    col.close()


def test_a_recording_swapped_for_another_counts_as_lost(tmp_path):
    # Re-voicing replaces the tag, and the new file must actually be there. A
    # note pointing at a recording nobody made is a play button that does
    # nothing, which is worse than one that is absent.
    col = _with_audio(tmp_path, ["One."])
    before = add_card.recordings(col)

    note = col.get_note(list(col.find_notes(""))[0])
    note["Example"] = "One. [sound:ttsex-somewhere-else.mp3]"
    col.update_note(note)

    with pytest.raises(SystemExit):
        add_card.check_recordings_kept(before, add_card.recordings(col))
    # Unless that is what the run was for, and it says so.
    add_card.check_recordings_kept(before, add_card.recordings(col), expected=1)
    col.close()


def test_a_whole_collection_going_quiet_is_caught(tmp_path):
    # The shape of the thing that actually happened, at the scale it happened.
    col = _with_audio(tmp_path, [f"Sentence {i}." for i in range(50)])
    before = add_card.recordings(col)
    col.db.execute("update notes set flds = replace(flds, ' [sound:', ' [was:')")

    with pytest.raises(SystemExit):
        add_card.check_recordings_kept(before, add_card.recordings(col), expected=3)
    col.close()


def test_the_notes_a_run_never_touches_are_in_the_snapshot(tmp_path):
    # snapshot() records the planned notes' fields and everybody else's audio.
    # The second half is what makes "no note outside this run lost a recording"
    # possible at all.
    col = _with_audio(tmp_path, ["One.", "Two.", "Three."])
    ids = list(col.find_notes(""))
    snap = tts.snapshot(col, ids[:1])

    assert set(snap["fields"]) == {ids[0]}
    assert set(snap["elsewhere"]) == set(ids[1:])
    assert snap["elsewhere"][ids[1]] == ["ttsex-1.mp3"]
    # And the planned note is not counted twice, in both halves.
    assert ids[0] not in snap["elsewhere"]
    col.close()
