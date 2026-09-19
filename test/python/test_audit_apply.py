"""Writing corrections back, and what has to be true before any of it is sent.

The audit is the one job here that edits sentences in an existing collection,
and it runs against the real thing: a thousand notes with a year of scheduling
on them and no undo on the other side. Everything below perturbs a real
collection and asserts the specific check that has to notice.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "anki"))

pytest.importorskip("anki.collection",
                    reason="the Anki library is not installed: pip install -r test/python/requirements.txt")

import add_card                                                  # noqa: E402
import audit                                                     # noqa: E402


def _collection(tmp_path, rows):
    """One note per row, on a note type with a Back field the audit reads."""
    from anki.collection import Collection

    col = Collection(str(tmp_path / "collection.anki2"))
    notetype = col.models.new("Audited")
    for name in ("Front", "Back", "Example"):
        col.models.add_field(notetype, col.models.new_field(name))
    template = col.models.new_template("Card 1")
    template["qfmt"], template["afmt"] = "{{Front}}", "{{Example}}"
    col.models.add_template(notetype, template)
    col.models.add(notetype)
    notetype = col.models.by_name("Audited")
    for word, example in rows:
        note = col.new_note(notetype)
        note["Front"], note["Back"], note["Example"] = word, word, example
        col.add_note(note, col.decks.id("Default"))
    return col


def _guid(col, word):
    return col.db.scalar("select guid from notes where flds like ?", f"{word}\x1f%")


def _example(col, word):
    flds = col.db.scalar("select flds from notes where flds like ?", f"{word}\x1f%")
    return flds.split("\x1f")[2]


def test_a_correction_is_written_and_its_stale_recording_dropped(tmp_path):
    # The recording says the old wording, so it has to go; the TTS workflow
    # records the corrected sentence on its next run.
    col = _collection(tmp_path, [
        ("avow", "He avow his support. [sound:ttsex-avow.mp3]"),
        ("chisel", "She chiseled the stone. [sound:ttsex-chisel.mp3]"),
    ])
    changed = [{
        "guid": _guid(col, "avow"), "target": "avow",
        "was": "He avow his support.", "now": "He avowed his support.",
        "issues": ["grammar"],
    }]

    assert audit.apply_all(col, changed, "Example")["applied"] == 1
    assert _example(col, "avow") == "He avowed his support."
    # And the note nobody asked about is untouched, tag and all.
    assert _example(col, "chisel") == "She chiseled the stone. [sound:ttsex-chisel.mp3]"
    col.close()


def test_audio_lost_anywhere_else_stops_the_sync(tmp_path):
    """The check that did not exist when a thousand recordings went missing.

    The sabotage here is a raw UPDATE, which leaves `mod` alone - so the note
    list is unchanged, no unexpected note reports as modified, and no card has
    moved. Every check the audit already had passes. Only the count of
    recordings notices, which is the entire point of adding it.
    """
    col = _collection(tmp_path, [
        ("avow", "He avow his support. [sound:ttsex-avow.mp3]"),
        ("chisel", "She chiseled the stone. [sound:ttsex-chisel.mp3]"),
        ("glean", "They gleaned it. [sound:ttsex-glean.mp3]"),
    ])
    before_notes = add_card.existing_notes(col)
    changed = [{
        "guid": _guid(col, "avow"), "target": "avow",
        "was": "He avow his support.", "now": "He avowed his support.",
        "issues": ["grammar"],
    }]

    real = audit.apply_correction

    def also_strip_everything_else(text, now):
        col.db.execute("update notes set flds = replace(flds, ' [sound:', ' [gone:')")
        return real(text, now)

    audit.apply_correction = also_strip_everything_else
    try:
        with pytest.raises(SystemExit):
            audit.apply_all(col, changed, "Example")
    finally:
        audit.apply_correction = real

    # Proof that nothing else would have caught it: no note reports as touched.
    assert add_card.existing_notes(col) == before_notes
    col.close()


def test_a_note_holding_two_recordings_is_not_mistaken_for_a_loss(tmp_path):
    # A note part-way through a re-voice holds two tags. Correcting it drops
    # both, and counting cards instead of recordings would read the second one
    # as damage and refuse a run that did exactly what it meant to.
    col = _collection(tmp_path, [
        ("avow", "He avow it. [sound:ttsex-old.mp3] [sound:ttsex-new.mp3]"),
    ])
    changed = [{
        "guid": _guid(col, "avow"), "target": "avow",
        "was": "He avow it.", "now": "He avowed it.",
        "issues": ["grammar"],
    }]

    assert audit.apply_all(col, changed, "Example") == {"applied": 1, "silenced": 2, "cards": 1}
    assert _example(col, "avow") == "He avowed it."
    col.close()


def test_a_correction_that_would_take_the_word_away_is_still_refused(tmp_path):
    # The older guard, checked here against a real collection rather than as a
    # string function: a card exists for its word, and a correction that
    # removes the word leaves a card about nothing.
    col = _collection(tmp_path, [("avow", "He avow his support.")])
    changed = [{
        "guid": _guid(col, "avow"), "target": "avow",
        "was": "He avow his support.", "now": "He stated his support.",
        "issues": ["word choice"],
    }]

    assert audit.apply_all(col, changed, "Example")["applied"] == 0
    assert _example(col, "avow") == "He avow his support."
    col.close()


def test_the_recording_it_removes_is_actually_reported(tmp_path, capsys):
    """The warning that had never once been printed.

    apply_correction keeps the learner's own note after the sentence and
    nothing else, so a [sound:] tag on the sentence line was already gone by
    the time the count ran. It counted what was left, found none, and said
    nothing - so every correction this job has ever applied took the card's
    recording with it, in silence, and the only way to find out was to open
    the card and press a play button that was no longer there.
    """
    col = _collection(tmp_path, [
        ("avow", "He avow his support. [sound:ttsex-avow.mp3]"),
        ("glean", "They gleans it. [sound:ttsex-glean.mp3]"),
    ])
    changed = [
        {"guid": _guid(col, "avow"), "target": "avow",
         "was": "He avow his support.", "now": "He avowed his support.", "issues": ["g"]},
        {"guid": _guid(col, "glean"), "target": "glean",
         "was": "They gleans it.", "now": "They gleaned it.", "issues": ["g"]},
    ]

    assert audit.apply_all(col, changed, "Example") == {"applied": 2, "silenced": 2, "cards": 2}
    said = capsys.readouterr().out
    assert "2 cards had audio of the old wording" in said, said
    assert "re-run the TTS package workflow" in said
    col.close()


def test_a_recording_after_the_learners_own_note_is_counted_too(tmp_path, capsys):
    # This one survives apply_correction, because everything after the first
    # block boundary is kept verbatim. It is still audio of the old wording.
    col = _collection(tmp_path, [
        ("avow", "He avow it.<br><br>my own note [sound:ttsex-avow.mp3]"),
    ])
    changed = [{"guid": _guid(col, "avow"), "target": "avow",
                "was": "He avow it.", "now": "He avowed it.", "issues": ["g"]}]

    assert audit.apply_all(col, changed, "Example") == {"applied": 1, "silenced": 1, "cards": 1}
    assert "[sound:" not in _example(col, "avow")
    assert "1 card had audio of the old wording" in capsys.readouterr().out
    col.close()


def test_a_sentence_with_no_recording_reports_nothing(tmp_path, capsys):
    col = _collection(tmp_path, [("avow", "He avow his support.")])
    changed = [{"guid": _guid(col, "avow"), "target": "avow",
                "was": "He avow his support.", "now": "He avowed his support.",
                "issues": ["g"]}]

    assert audit.apply_all(col, changed, "Example") == {"applied": 1, "silenced": 0, "cards": 0}
    assert "audio of the old wording" not in capsys.readouterr().out
    col.close()


_reports = 0


def _report(tmp_path, monkeypatch, rows, **kwargs):
    # A fresh file each time: the job summary is appended to, not overwritten,
    # so reusing one path would let the first report answer for the second.
    global _reports
    _reports += 1
    where = tmp_path / f"summary-{_reports}.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(where))
    audit.write_report(rows, {}, len(rows), tmp_path, applied=True, **kwargs)
    return where.read_text(encoding="utf-8")


def test_the_report_says_which_recordings_it_took_away(tmp_path, monkeypatch):
    # The log is not what anybody reads on a phone. The job summary is, and it
    # said nothing about the one thing here that makes a card worse.
    rows = [{"deck": "English", "target": "avow", "issues": ["grammar"],
             "was": "He avow it.", "now": "He avowed it."}]

    said = _report(tmp_path, monkeypatch, rows, silenced=3)
    assert "3 recordings removed" in said
    assert "Anki TTS package" in said, "it has to say how to get them back"

    # And keeps quiet when it took nothing.
    assert "recordings removed" not in _report(tmp_path, monkeypatch, rows, silenced=0)
