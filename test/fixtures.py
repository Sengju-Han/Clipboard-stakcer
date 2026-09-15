"""Files the suites need, built rather than committed.

Everything here is generated so the tests carry no binaries and no copy of
anybody's collection. The .apkg in particular is written by hand — SQLite and
a zip, at schema 11 — which makes it an independent check of the reader rather
than a round trip through this project's own writer.
"""

import json
import math
import random
import sqlite3
import struct
import time
import wave
import zipfile
from pathlib import Path


def episode_srt(path: Path, lines: int = 600):
    """A plausible subtitle file: ordinary speech, a few deck words, a phrase."""
    rng = random.Random(20260915)          # the same episode every run
    common = "I think we should talk about this before it gets any worse than it already is".split()
    rare = ["quokka", "photobombed", "bewildered", "ostracised", "perfunctory",
            "obsequious", "avow", "chime", "bum", "haggle"]

    def stamp(x):
        h, m, s = int(x // 3600), int(x % 3600 // 60), x % 60
        return f"{h:02d}:{m:02d}:{s:06.3f}".replace(".", ",")

    out, at = [], 1.0
    for i in range(lines):
        words = [rng.choice(common) for _ in range(rng.randint(5, 12))]
        if i % 7 == 0:
            words.insert(rng.randrange(len(words)), rng.choice(rare))
        if i % 23 == 0:
            words = "He chimed in with an opinion nobody asked for".split()
        out.append(f"{i + 1}\n{stamp(at)} --> {stamp(at + 2.4)}\n"
                   + " ".join(words).capitalize() + ".\n")
        at += 2.8
    path.write_text("\n".join(out), encoding="utf8")
    return path


def tone_wav(path: Path, seconds: int = 40):
    """Audible between 0.5s and 6s, silent after, so a captured clip of the
    first line is distinguishable from a clip of nothing."""
    rate = 22050
    with wave.open(str(path), "w") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        frames = []
        for i in range(rate * seconds):
            t = i / rate
            amp = 12000 if 0.5 < t < 6.0 else 0
            frames.append(struct.pack("<h", int(amp * math.sin(2 * math.pi * 440 * t))))
        w.writeframes(b"".join(frames))
    return path


# ---- a small .apkg, written by hand -------------------------------------

SCHEMA = """
CREATE TABLE col (id integer primary key, crt integer not null, mod integer not null,
  scm integer not null, ver integer not null, dty integer not null, usn integer not null,
  ls integer not null, conf text not null, models text not null, decks text not null,
  dconf text not null, tags text not null);
CREATE TABLE notes (id integer primary key, guid text not null, mid integer not null,
  mod integer not null, usn integer not null, tags text not null, flds text not null,
  sfld integer not null, csum integer not null, flags integer not null, data text not null);
CREATE TABLE cards (id integer primary key, nid integer not null, did integer not null,
  ord integer not null, mod integer not null, usn integer not null, type integer not null,
  queue integer not null, due integer not null, ivl integer not null, factor integer not null,
  reps integer not null, lapses integer not null, left integer not null, odue integer not null,
  odid integer not null, flags integer not null, data text not null);
CREATE TABLE revlog (id integer primary key, cid integer not null, usn integer not null,
  ease integer not null, ivl integer not null, lastIvl integer not null, factor integer not null,
  time integer not null, type integer not null);
CREATE TABLE graves (usn integer not null, oid integer not null, type integer not null);
"""

# word, hook, clue, example, anki card type, interval days, ease x10, lapses
SAMPLE = [
    ("avow", "vow and a! to the public", "공언하다",
     "The politician avowed his commitment to improving education.", 2, 11, 2500, 0),
    ("chime in", "", "대화에 끼어들다",
     "He chimed in with an opinion nobody asked for.", 2, 3, 2300, 4),
    ("quokka", "", "쿼카", "A quokka photobombed a bewildered tourist.", 0, 0, 0, 0),
]


def small_apkg(path: Path):
    """Three notes at schema 11: one settled, one lapsing, one never seen."""
    db_path = path.with_suffix(".anki2")
    if db_path.exists():
        db_path.unlink()
    con = sqlite3.connect(db_path)
    con.executescript(SCHEMA)

    now = int(time.time())
    crt = now - 86400 * 30                       # the collection is a month old
    mid = 1234567890123
    model = {
        str(mid): {
            "id": mid, "name": "Test", "type": 0, "mod": now, "usn": -1, "sortf": 0, "did": 1,
            "tmpls": [{"name": "Card 1", "ord": 0, "qfmt": "{{Front}}",
                       "afmt": "{{FrontSide}}<hr id=answer>{{Back}}", "did": None,
                       "bqfmt": "", "bafmt": "", "bfont": "", "bsize": 0}],
            "flds": [{"name": n, "ord": i, "sticky": False, "rtl": False,
                      "font": "Arial", "size": 20, "media": []}
                     for i, n in enumerate(["Front", "Back", "Example"])],
            "css": ".card{}", "latexPre": "", "latexPost": "", "latexsvg": False,
            "req": [[0, "any", [0]]], "tags": [], "vers": [],
        }
    }
    decks = {
        "1": {"id": 1, "name": "Default", "mod": now, "usn": -1, "desc": "", "dyn": 0,
              "conf": 1, "collapsed": False, "lrnToday": [0, 0], "revToday": [0, 0],
              "newToday": [0, 0], "timeToday": [0, 0]},
        "2": {"id": 2, "name": "Testing", "mod": now, "usn": -1, "desc": "", "dyn": 0,
              "conf": 1, "collapsed": False, "lrnToday": [0, 0], "revToday": [0, 0],
              "newToday": [0, 0], "timeToday": [0, 0]},
    }
    con.execute("INSERT INTO col VALUES (1,?,?,?,11,0,-1,0,?,?,?,?,'')",
                (crt, now, now, json.dumps({"nextPos": 1, "curModel": str(mid)}),
                 json.dumps(model), json.dumps(decks), json.dumps({"1": {"id": 1, "name": "Default"}})))

    today = (int(time.time()) - crt) // 86400
    for i, (word, hook, clue, example, ctype, ivl, factor, lapses) in enumerate(SAMPLE):
        nid = now * 1000 + i
        back = word + ("<br><i>" + hook + "</i>" if hook else "")
        flds = "\x1f".join([clue, back, example])
        con.execute("INSERT INTO notes VALUES (?,?,?,?,-1,'',?,?,?,0,'')",
                    (nid, f"guid{i:04d}", mid, now, flds, clue, 0))
        due = (today + ivl) if ctype == 2 else i
        con.execute("INSERT INTO cards VALUES (?,?,?,0,?,-1,?,?,?,?,?,?,?,0,0,0,0,'')",
                    (nid + 1, nid, 2, now, ctype, ctype, due, ivl, factor,
                     3 if ctype == 2 else 0, lapses))
    con.commit()
    con.close()

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(db_path, "collection.anki2")
        z.writestr("media", "{}")
    db_path.unlink()
    return path


def build_all(where: Path):
    where.mkdir(parents=True, exist_ok=True)
    return {
        "episode": episode_srt(where / "episode.srt"),
        "tone": tone_wav(where / "tone.wav"),
        "apkg": small_apkg(where / "small.apkg"),
    }
