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


def test_a_due_date_is_written_at_noon_so_it_survives_being_read_elsewhere():
    # Midnight UTC is nine in the morning in Seoul, which held every card due
    # today back until mid-morning; and after the four-hour shift the app's day
    # begins with, it falls into the day before anywhere west of UTC. Noon does
    # neither, from UTC-8 to UTC+15.
    written = bd.due_from({"due_date": "2026-10-01"}, date(2026, 9, 15))
    assert written == "2026-10-01T12:00:00+00:00"
    assert bd.at_noon(date(2026, 1, 2)) == "2026-01-02T12:00:00+00:00"


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


# ---- a deck that lost most of itself -------------------------------------
#
# The workflow runs this and then commits and pushes docs/deck/deck.json with
# nothing in between, so whatever it writes is what the next phone downloads.
# Two ways it can be wrong and still look like a build, and neither of them
# raises anything on its own.

import json                                                      # noqa: E402


def test_a_loss_within_reason_is_allowed():
    assert bd.losing_too_much(1000, 1000, "went") == ""
    assert bd.losing_too_much(995, 1000, "went") == ""
    assert bd.losing_too_much(900, 1000, "went") == ""      # exactly the limit
    # Nothing to compare against is not evidence of a loss.
    assert bd.losing_too_much(0, 0, "went") == ""


def test_a_loss_beyond_it_is_named_with_numbers():
    said = bd.losing_too_much(12, 1200, "in the export did not become cards")
    assert "1188 of 1200" in said
    assert "12 left" in said
    assert "1%" in said


def test_a_deck_that_shrank_against_the_one_on_disk(tmp_path):
    # The proportion cannot see this one: twelve rows in, twelve cards out,
    # nothing skipped — and a deck of twelve published over a deck of twelve
    # hundred. What the app reads today is the only record of yesterday.
    target = tmp_path / "deck.json"
    target.write_text(json.dumps({"card_count": 1200, "cards": []}), encoding="utf-8")

    assert "1188 of 1200" in bd.was_bigger(target, 12)
    assert bd.was_bigger(target, 1150) == ""
    # A first build has nothing to compare against and must not be blocked.
    assert bd.was_bigger(tmp_path / "not-here.json", 12) == ""


def test_an_unreadable_deck_is_not_treated_as_evidence(tmp_path):
    # A half-written or hand-edited file says nothing about how big the
    # collection was, and refusing to build because of it would be worse than
    # the thing being guarded against.
    target = tmp_path / "deck.json"
    target.write_text("{ not json", encoding="utf-8")
    assert bd.was_bigger(target, 12) == ""


def _export(tmp_path, n, start=0):
    rows = [{
        "guid": f"g{i}", "word_field": "Back", "fields_raw": {
            "Front": f"clue {i}", "Back": f"word{i}", "Example": f"A sentence with word{i}."},
        "deck": "Testing", "type": 0, "queue": 0, "due": 0, "ivl": 0, "factor": 0,
        "reps": 0, "lapses": 0,
    } for i in range(start, start + n)]
    path = tmp_path / "cards.json"
    path.write_text(json.dumps({"cards": rows, "collection_source": "test"}), encoding="utf-8")
    return path


def _run(monkeypatch, export, out, *extra):
    monkeypatch.setattr(sys, "argv", ["build_deck.py", "--export", str(export),
                                      "--out", str(out), *extra])
    return bd.main()


def test_a_first_build_is_written(tmp_path, monkeypatch):
    out = tmp_path / "deck"
    assert _run(monkeypatch, _export(tmp_path, 200), out) == 0
    assert json.loads((out / "deck.json").read_text(encoding="utf-8"))["card_count"] == 200


def test_a_build_that_lost_the_collection_writes_nothing(tmp_path, monkeypatch, capsys):
    out = tmp_path / "deck"
    _run(monkeypatch, _export(tmp_path, 200), out)
    before = (out / "deck.json").read_text(encoding="utf-8")

    # The next run finds twelve cards where there were two hundred.
    assert _run(monkeypatch, _export(tmp_path, 12), out) == 1
    said = capsys.readouterr().out
    assert "188 of 200" in said
    assert "Nothing was written" in said
    # And the deck the app is reading is untouched, which is the whole point.
    assert (out / "deck.json").read_text(encoding="utf-8") == before


def test_it_can_be_overridden_for_a_collection_that_really_shrank(tmp_path, monkeypatch, capsys):
    out = tmp_path / "deck"
    _run(monkeypatch, _export(tmp_path, 200), out)
    assert _run(monkeypatch, _export(tmp_path, 12), out, "--allow-loss") == 0

    said = capsys.readouterr().out
    assert "--allow-loss was given" in said
    assert json.loads((out / "deck.json").read_text(encoding="utf-8"))["card_count"] == 12


def test_a_growing_collection_is_never_in_the_way(tmp_path, monkeypatch):
    out = tmp_path / "deck"
    _run(monkeypatch, _export(tmp_path, 200), out)
    assert _run(monkeypatch, _export(tmp_path, 260), out) == 0
    assert json.loads((out / "deck.json").read_text(encoding="utf-8"))["card_count"] == 260
