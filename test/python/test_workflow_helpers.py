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
