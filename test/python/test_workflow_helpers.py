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
