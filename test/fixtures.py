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
# A note can carry more than one recording: the sentence read aloud, and the
# word itself said on its own. Both have to survive a trip through here, so one
# of these has both and the tags are written into the fields the way Anki does.
SAY = "[sound:say-avow.mp3]"
READ = "[sound:ttsex-avow.mp3]"

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
    # A full deck configuration, not a stub. The app's reader goes straight to
    # the SQLite and never looks at this, but real Anki refuses a package whose
    # config is missing a field — and a fixture real Anki will not open is a
    # fixture that proves less than it appears to.
    dconf = {
        "1": {
            "id": 1, "name": "Default", "mod": now, "usn": -1, "maxTaken": 60,
            "autoplay": True, "timer": 0, "replayq": True, "dyn": 0,
            "new": {"bury": False, "delays": [1, 10], "initialFactor": 2500,
                    "ints": [1, 4, 0], "order": 1, "perDay": 20},
            "rev": {"bury": False, "ease4": 1.3, "ivlFct": 1, "maxIvl": 36500,
                    "perDay": 200, "hardFactor": 1.2},
            "lapse": {"delays": [10], "leechAction": 1, "leechFails": 8,
                      "minInt": 1, "mult": 0},
        }
    }
    # The last column is the tag list, and it is JSON like the four before it.
    # An empty string there reads as end-of-input and Anki refuses the file.
    con.execute("INSERT INTO col VALUES (1,?,?,?,11,0,-1,0,?,?,?,?,'{}')",
                (crt, now, now, json.dumps({"nextPos": 1, "curModel": str(mid), "schedVer": 2}),
                 json.dumps(model), json.dumps(decks), json.dumps(dconf)))

    today = (int(time.time()) - crt) // 86400
    for i, (word, hook, clue, example, ctype, ivl, factor, lapses) in enumerate(SAMPLE):
        nid = now * 1000 + i
        back = word + ("<br><i>" + hook + "</i>" if hook else "")
        if word == "avow":
            back += " " + SAY
            example += " " + READ
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


# The decoy a modern export carries: a valid SQLite file holding one warning
# row, so that an Anki too old to read the real collection says "upgrade"
# rather than crashing. Reading it would import one junk card and report
# success, which is worse than failing — so the reader has to prefer the newest
# collection in the file, and this is what proves it does.
DECOY_WARNING = "Please update to the latest Anki version, then import this file again."


def modern_apkg(path: Path):
    """What AnkiDroid actually exports: zstd inside, and a decoy beside it.

    The hand-written legacy package covers the format Anki has always read.
    This covers the one a person's phone will actually hand over, including the
    trap in it. Skipped rather than faked when zstandard is not installed: a
    fixture that quietly wrote something else would test the wrong thing.
    """
    try:
        import zstandard
    except ImportError:
        return None

    legacy = small_apkg(path.with_name("inner.apkg"))
    with zipfile.ZipFile(legacy) as z:
        real = z.read("collection.anki2")
    legacy.unlink()

    # The decoy: a real SQLite file with one note in it, saying the wrong thing
    # on purpose, exactly as Anki writes it.
    decoy_path = path.with_name("decoy.anki2")
    if decoy_path.exists():
        decoy_path.unlink()
    con = sqlite3.connect(decoy_path)
    con.executescript(SCHEMA)
    now = int(time.time())
    mid = 1
    model = {"1": {"id": 1, "name": "Basic", "type": 0, "mod": now, "usn": -1, "sortf": 0,
                   "did": 1, "tmpls": [{"name": "Card 1", "ord": 0, "qfmt": "{{Front}}",
                                        "afmt": "{{Front}}", "did": None, "bqfmt": "",
                                        "bafmt": "", "bfont": "", "bsize": 0}],
                   "flds": [{"name": "Front", "ord": 0, "sticky": False, "rtl": False,
                             "font": "Arial", "size": 20, "media": []}],
                   "css": "", "latexPre": "", "latexPost": "", "latexsvg": False,
                   "req": [[0, "any", [0]]], "tags": [], "vers": []}}
    con.execute("INSERT INTO col VALUES (1,?,?,?,11,0,-1,0,'{}',?,?,'{}','{}')",
                (now, now, now, json.dumps(model),
                 json.dumps({"1": {"id": 1, "name": "Default", "mod": now, "usn": -1,
                                   "desc": "", "dyn": 0, "conf": 1, "collapsed": False,
                                   "lrnToday": [0, 0], "revToday": [0, 0],
                                   "newToday": [0, 0], "timeToday": [0, 0]}})))
    con.execute("INSERT INTO notes VALUES (?,?,?,?,-1,'',?,?,0,0,'')",
                (now, "decoyguid", mid, now, DECOY_WARNING, DECOY_WARNING))
    con.execute("INSERT INTO cards VALUES (?,?,1,0,?,-1,0,0,1,0,0,0,0,0,0,0,0,'')",
                (now + 1, now, now))
    con.commit()
    con.close()

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("meta", b"\x08\x03")                       # the version marker
        z.writestr("collection.anki21b", zstandard.ZstdCompressor().compress(real))
        z.write(decoy_path, "collection.anki2")                # the trap
        z.writestr("media", "{}")
    decoy_path.unlink()
    return path


# A subtitle file is as likely to be Windows-1252 as UTF-8, and a Korean one is
# often EUC-KR. The same two lines in four encodings, so the reader's guess is
# checked against text whose right answer is known.
ENCODED_SRT = ("1\n00:00:01,000 --> 00:00:04,000\n{line}\n\n"
               "2\n00:00:05,000 --> 00:00:07,000\nHe chimed in with an opinion.\n")

ENCODINGS = {
    "utf8.srt": ("Café — naïve, résumé.", "utf-8", "Café"),
    "utf8bom.srt": ("Café — naïve, résumé.", "utf-8-sig", "Café"),
    "cp1252.srt": ("Cafe - naive, resume. Ärger", "cp1252", "Ärger"),
    "euckr.srt": ("공언하다 - 우회적으로 말하다", "euc-kr", "공언하다"),
}


def encoded_subtitles(where: Path):
    """One file per encoding, with what each must read as."""
    where.mkdir(parents=True, exist_ok=True)
    out = {}
    for name, (line, encoding, expected) in ENCODINGS.items():
        path = where / name
        path.write_bytes(ENCODED_SRT.format(line=line).encode(encoding))
        out[name] = {"path": str(path), "expect": expected, "encoding": encoding}
    return out


def unplayable(path: Path):
    """Something with a video extension that no browser can decode.

    Picking a .mkv on Android leaves the element black and silent with nothing
    on screen to say why, and this is how that is held to saying something.
    """
    path.write_bytes(bytes(range(256)) * 2)
    return path


def build_all(where: Path):
    where.mkdir(parents=True, exist_ok=True)
    return {
        "episode": episode_srt(where / "episode.srt"),
        "tone": tone_wav(where / "tone.wav"),
        "apkg": small_apkg(where / "small.apkg"),
        "modern_apkg": modern_apkg(where / "modern.apkg"),
        "encoded": encoded_subtitles(where / "encodings"),
        "unplayable": unplayable(where / "broken.mkv"),
    }
