"""The conversion from an Anki export into a deck the app can review.

None of this needs Anki, a network or a key. It is arithmetic on a dict, and
every one of these cases is something that actually came out of a real export
and broke something downstream.
"""

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "anki"))

import build_deck as bd                                          # noqa: E402


def test_as_int_counts_a_list():
    # The export writes `reviews` as a list of ratings, and `lapses` as a
    # number. Both mean "how many", so both have to survive the same reader.
    assert bd.as_int([3, 1, 3]) == 3
    assert bd.as_int("4") == 4
    assert bd.as_int("") == 0
    assert bd.as_int(None, 7) == 7
    assert bd.as_int("not a number", 2) == 2


def test_as_float_tolerates_the_export():
    assert bd.as_float("2.5") == 2.5
    assert bd.as_float("") is None
    assert bd.as_float(None, 1.0) == 1.0
    assert bd.as_float("nonsense", 0.0) == 0.0


def test_plain_strips_what_a_reader_never_sees():
    assert bd.plain("<b>avow</b>") == "avow"
    assert bd.plain("hello [sound:x.mp3]") == "hello"
    assert bd.plain("one<br>two") == "one\ntwo"
    assert bd.plain("a &amp; b") == "a & b"
    assert bd.plain("<div>line</div><div>two</div>") == "line\ntwo"
    assert bd.plain(None) == ""


def test_stability_never_drops_below_a_day():
    # FSRS refuses a reviewed card whose stability is under one day: it is not
    # a weak memory, it is a contradiction, and it used to end the session with
    # "Invalid memory state" and no explanation.
    assert bd.stability_from({"fsrs_stability_days": "0.2"}) == 1.0
    assert bd.stability_from({"interval_days": 0}) == 1.0
    assert bd.stability_from({}) == 1.0
    # And what Anki already knows is preferred over a guess from the interval.
    assert bd.stability_from({"fsrs_stability_days": "12.5", "interval_days": 3}) == 12.5
    assert bd.stability_from({"interval_days": 9}) == 9.0


def test_difficulty_reads_fsrs_first_then_ease():
    assert bd.difficulty_from({"fsrs_difficulty_pct": 0}) == 1.0
    assert bd.difficulty_from({"fsrs_difficulty_pct": 100}) == 10.0
    assert bd.difficulty_from({}) == bd.DIFFICULTY_MID
    # The default ease is the middle of the range; below it is harder.
    assert bd.difficulty_from({"ease_pct": 250}) == bd.DIFFICULTY_MID
    assert bd.difficulty_from({"ease_pct": 130}) > bd.DIFFICULTY_MID
    assert bd.difficulty_from({"ease_pct": 350}) < bd.DIFFICULTY_MID
    # And it stays inside 1..10 whatever the export says.
    assert 1.0 <= bd.difficulty_from({"ease_pct": 10}) <= 10.0
    assert 1.0 <= bd.difficulty_from({"ease_pct": 9000}) <= 10.0


def test_due_ignores_a_date_a_century_away():
    today = date(2026, 9, 15)
    # Anki writes a far-future date for a card it will never show again. That
    # is a tombstone, not a schedule, and treating it as one puts a card out of
    # reach forever.
    assert bd.due_from({"due_date": "2381-06-15"}, today).startswith("2026-09-15")
    assert bd.due_from({"due_date": "2026-10-01"}, today).startswith("2026-10-01")
    assert bd.due_from({"due_date": ""}, today).startswith("2026-09-15")
    assert bd.due_from({"due_date": "not a date"}, today).startswith("2026-09-15")


def test_last_review_is_none_rather_than_a_guess():
    assert bd.last_review_from({"last_review": ""}) is None
    assert bd.last_review_from({}) is None
    assert bd.last_review_from({"last_review": "rubbish"}) is None
    assert bd.last_review_from({"last_review": "2026-09-01"}).startswith("2026-09-01")


def test_a_card_comes_out_the_shape_the_app_expects():
    today = date(2026, 9, 15)
    card = bd.build_card({
        "guid": "abc", "deck": "duo", "notetype": "Basic",
        "fields_raw": {"Front": "공언하다", "Back": "avow<br><i>a public vow</i>",
                       "Example": "He avowed it. [sound:x.mp3]"},
        "word_field": "Back",
        "state": "review", "interval_days": 6, "ease_pct": 250,
        "reviews": [3, 3], "lapses": 1, "due_date": "2026-09-20",
    }, today)

    assert card is not None
    assert card["word"] == "avow"
    assert card["hook"] == "a public vow"
    assert card["clue"] == "공언하다"
    assert card["example"] == "He avowed it."
    assert card["audio"] == "x.mp3"
    assert card["fsrs"]["state"] == 2
    assert card["fsrs"]["reps"] == 2
    assert card["fsrs"]["lapses"] == 1
    assert card["fsrs"]["stability"] >= 1.0
    assert card["fsrs"]["due"].startswith("2026-09-20")


def test_a_card_with_no_word_is_left_out_rather_than_half_built():
    assert bd.build_card({"guid": "x", "fields_raw": {"Front": "", "Back": ""}}, date.today()) is None
    assert bd.build_card({"guid": "x"}, date.today()) is None


def test_a_useless_due_date_is_counted_rather_than_swallowed():
    # 1,004 cards of 1,177 quietly landing on today once went unremarked, and
    # the deck that came out of it told somebody a thousand cards were owed.
    today = date(2026, 9, 15)
    tally = {}
    bd.due_from({"due_date": "2381-06-15"}, today, tally)
    bd.due_from({"due_date": ""}, today, tally)
    bd.due_from({"due_date": "rubbish"}, today, tally)
    bd.due_from({"due_date": "2026-10-01"}, today, tally)   # fine, not counted
    assert tally == {"far_future": 1, "fell_back": 3, "missing": 1, "unreadable": 1}
