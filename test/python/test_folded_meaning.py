"""The generated meaning, folded onto the back of the card.

The Add to Anki page knows what a word means - it asks while you are typing it,
and writes the answer into this repository. Until now that was all it did with
it: the card you then met a hundred times had the word, your sentence, and
nothing else.

So the page now appends the answer to the back of the card inside a <details>,
where it costs one tap and nothing until then. The risk is entirely in what
else reads that field. Half this project asks "what word is this card for?" and
answers it by reading the answer field, and a paragraph of definition arriving
in there is wrong in four different places at once:

  - the proofreader is told the sentence practises the whole definition
  - keeps_the_word() then forbids a correction from dropping any word in it
  - the voice reads the definition out loud
  - the exporter picks the word column by measuring how long each field is

Every one of those fails quietly and none of them fails at the point of change,
which is what this file is for.
"""

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "anki"))

import proofread                                                 # noqa: E402

PAGE = ROOT / "docs" / "index.html"

# What the page actually writes, taken from a real answer. Written out rather
# than generated so that a change to the page's markup has to be looked at
# here too - this is the shape the rest of the project has agreed to skip.
BLOCK = (
    '<details class="lexis-detail" style="margin-top:.7em;text-align:left">'
    '<summary style="cursor:pointer;opacity:.55">in detail</summary>'
    '<div style="opacity:.85">'
    '<div>a feeling of embarrassment at having failed at something</div>'
    '<div>/ʃəˈɡrɪn/ · formal</div>'
    '<div>much to my chagrin</div>'
    '<div>한국어 · 분함</div>'
    "</div></details>"
)


def _page_constant(name):
    found = re.search(rf'const {name} = "([^"]+)"', PAGE.read_text(encoding="utf-8"))
    assert found, f"{name} is not in docs/index.html any more"
    return found.group(1)


# ---- the two sides agree on what the block is called ----------------------

def test_the_page_and_the_scripts_name_the_same_block():
    """A renamed class on one side and not the other is invisible until a card
    made afterwards turns up with its definition being read aloud."""
    mark = _page_constant("DETAIL_MARK")
    assert mark in BLOCK
    assert mark in (ROOT / "docs" / "app" / "apkg.js").read_text(encoding="utf-8")
    assert mark in (ROOT / "anki" / "export_deck.py").read_text(encoding="utf-8")


def test_the_page_writes_it_where_the_word_is_not():
    """The block is appended, never prepended. Everything below depends on it."""
    page = PAGE.read_text(encoding="utf-8")
    assert "return back + detailHtml(info);" in page


# ---- the word is still the word ------------------------------------------

def test_the_word_survives_the_fold():
    assert proofread.headword("chagrin" + BLOCK) == "chagrin"


def test_and_survives_it_with_a_memory_hook_in_between():
    assert proofread.headword("chagrin<br>the grin you hold" + BLOCK) == "chagrin"


def test_a_plain_answer_field_is_unchanged():
    assert proofread.headword("chagrin") == "chagrin"
    assert proofread.headword("bone marrow") == "bone marrow"


def test_the_fold_is_a_block_break():
    """Not a detail of the regex: it is what makes every stop-at-the-first-break
    reader in this project stop at the fold."""
    assert proofread.BLOCK_BOUNDARY.search(BLOCK)
    sentence, annotation = proofread.split_annotation("I was chagrined." + BLOCK)
    assert sentence == "I was chagrined."
    assert annotation == BLOCK


# ---- what it would have done to a correction ------------------------------

def test_a_correction_is_not_refused_because_of_the_fold():
    """keeps_the_word() guards every word in the target. Handed the whole answer
    field it guards the definition too, and then refuses corrections for
    dropping a word that was only ever in the explanation."""
    was = "I was chagrin when the train left."
    now = "I was chagrined when the train left."
    assert proofread.keeps_the_word(was, now, proofread.headword("chagrin" + BLOCK))
    # And the thing it is actually for still works: the card is for *chagrin*,
    # and a correction that takes the word out is still refused.
    assert not proofread.keeps_the_word(was, "I was upset when the train left.",
                                        proofread.headword("chagrin" + BLOCK))


def test_the_whole_field_would_have_refused_it():
    """The bug this is guarding against, stated as a test so the guard has a
    reason on the record rather than a comment."""
    was = "Much to my chagrin, the train left without me."
    now = "Much to my chagrin, the train left without me, and I waited."
    assert not proofread.keeps_the_word(was, "I missed the train.", "chagrin" + BLOCK)
    assert proofread.keeps_the_word(was, now, "chagrin" + BLOCK)


# ---- the voice ------------------------------------------------------------

def test_the_voice_does_not_read_the_definition_out():
    tts = pytest.importorskip(
        "build_tts_apkg",
        reason="the Anki library is not installed: pip install -r test/python/requirements.txt")
    assert tts.speakable("chagrin" + BLOCK) == "chagrin"
    assert tts.speakable("Much to my chagrin, I left it at home." + BLOCK) \
        == "Much to my chagrin, I left it at home."


# ---- the export -----------------------------------------------------------

def test_the_export_still_sees_the_answer_field_as_the_short_one():
    export = pytest.importorskip(
        "export_deck",
        reason="the Anki library is not installed: pip install -r test/python/requirements.txt")
    assert export.word_sample("chagrin" + BLOCK) == "chagrin"
    # The fold is in the export itself - it is part of the card - and only the
    # measurement that decides which field holds the word ignores it.
    assert "embarrassment" in export.clean("chagrin" + BLOCK)


def test_the_export_leaves_an_ordinary_field_alone():
    export = pytest.importorskip("export_deck", reason="the Anki library is not installed")
    assert export.word_sample("chagrin") == "chagrin"
    assert export.word_sample("<div>chagrin</div>") == "chagrin"
    assert export.word_sample("") == ""
