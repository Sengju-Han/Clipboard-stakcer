"""Somebody else's Anki file, read — and one written back out.

The file read here is built by hand at schema 11 rather than by this project's
own writer, so it is a check of the reader against the format rather than a
round trip through one set of assumptions.
"""

import sqlite3
import zipfile


def run(t):
    t.open_app()

    t.open_settings()
    t.page.locator("#apkg-file").set_input_files(str(t.fixtures["apkg"]))
    t.page.wait_for_timeout(20000)
    said = t.page.locator("#settings-note").inner_text()
    t.note("the import says", said[:120])
    t.truthy("three cards arrive", "3 added" in said or "3 refreshed" in said)

    cards = {c["word"]: c for c in t.cards()}
    t.truthy("the word from the answer field", "avow" in cards)
    t.truthy("a phrase kept whole", "chime in" in cards)
    t.truthy("and one never reviewed", "quokka" in cards)

    avow = cards.get("avow", {})
    t.check("its memory hook was split off the word",
            avow.get("hook"), "vow and a! to the public")
    t.check("its clue came from the front", avow.get("clue"), "공언하다")
    t.truthy("its example came too", "politician" in (avow.get("example") or ""))
    t.check("the recording of the sentence came with it", avow.get("audio"), "ttsex-avow.mp3")
    # The note also carries the word said on its own, on the answer field. Only
    # the first of a note's recordings used to survive being read, so that one
    # was dropped here and then written back out as though it had never been
    # there - and the file would sit in the media folder looking healthy.
    t.check("and so did the one of the word itself",
            avow.get("audioMore"), ["say-avow.mp3"])
    t.check("it is in the deck it was in", avow.get("deck"), "Testing")
    t.check("a reviewed card arrives reviewed", avow.get("fsrs", {}).get("state"), 2)
    t.truthy("with a stability derived from its interval",
             avow.get("fsrs", {}).get("stability", 0) >= 1)
    t.check("a new card arrives new", cards.get("quokka", {}).get("fsrs", {}).get("state"), 0)
    t.check("and lapses travel", cards.get("chime in", {}).get("fsrs", {}).get("lapses"), 4)

    # Now write one, and read it with SQLite rather than with this app.
    with t.page.expect_download(timeout=180000) as dl:
        t.page.locator("#export-apkg-btn").click()
    out = dl.value.path()
    t.page.wait_for_timeout(500)

    with zipfile.ZipFile(out) as z:
        data = z.read("collection.anki2")
    written = t.fixtures["apkg"].parent / "written.anki2"
    written.write_bytes(data)
    con = sqlite3.connect(written)
    notes = con.execute("select count() from notes").fetchone()[0]
    cardrows = con.execute("select count() from cards").fetchone()[0]
    version = con.execute("select ver from col").fetchone()[0]
    guids = {g for (g,) in con.execute("select guid from notes")}
    import re
    wrote_sounds = []
    for (flds,) in con.execute("select flds from notes where flds like '%avow%'"):
        wrote_sounds += re.findall(r"\[sound:[^\]]*\]", flds)
    reviewed = con.execute("select count() from cards where type = 2").fetchone()[0]
    con.close()

    t.check("what comes out is a schema 11 collection", version, 11)
    t.check("both of the recordings are written back", sorted(wrote_sounds), sorted([
        "[sound:say-avow.mp3]", "[sound:ttsex-avow.mp3]"]))
    t.check("one note per card", notes, cardrows)
    t.check("and every card is in it", notes, len(t.cards()))
    t.truthy("the note ids from the file that was read travel back out",
             "guid0000" in guids)
    t.note("why", "Anki identifies a note by its guid, so this updates rather than duplicates")
    t.truthy("and the scheduling goes with them", reviewed > 0)

    _modern(t)
    _clip_from_the_other_phone(t)


def _modern(t):
    """The file a phone actually hands over: zstd inside, and a trap beside it.

    Anki 2.1.50 and later write the real collection as collection.anki21b,
    compressed, and put a decoy collection.anki2 next to it holding one note
    that says "update Anki". Reading the decoy would import that one junk card
    and report success — worse than failing, because nothing on screen would
    say the deck had not arrived. So the newest collection in the file has to
    win, and this is what proves it does.
    """
    modern = t.fixtures.get("modern_apkg")
    if not modern:
        t.note("skipped", "zstandard is not installed, so no modern package was built")
        return

    page = t.browser.new_context(viewport={"width": 390, "height": 844}).new_page()
    was, t.page = t.page, page
    try:
        t.open_app()
        t.open_settings()
        page.locator("#apkg-file").set_input_files(str(modern))
        page.wait_for_timeout(20000)
        t.note("the import says", page.locator("#settings-note").inner_text()[:110])

        words = {c["word"] for c in t.cards()}
        t.truthy("the real collection was read", "avow" in words)
        t.truthy("all of it", {"avow", "chime in", "quokka"} <= words)
        t.check("and the decoy's warning did not come in as a card",
                [w for w in words if "update to the latest" in w.lower()], [])
        t.note("why that matters",
               "reading the decoy imports one junk card and reports success")
    finally:
        t.page = was
        page.close()


def _clip_from_the_other_phone(t):
    """Clips do not sync, so a card mined elsewhere arrives without its audio.

    Writing [sound:…] for a file that is not in the package makes a card that
    is silent in Anki and a missing-media warning that is not the person's
    fault. A reference to a file from their own collection is a different
    thing: that one is already on the other side and stays.
    """
    page = t.browser.new_context(viewport={"width": 390, "height": 844},
                                 accept_downloads=True).new_page()
    was, t.page = t.page, page
    try:
        t.open_app()
        # One card says it has a captured clip; nothing was ever captured here.
        # Another carries a filename from the collection it came from.
        t.page.evaluate("""async () => {
          const db = await new Promise(r => { const q = indexedDB.open('lexis'); q.onsuccess = () => r(q.result); });
          const all = await new Promise(r => {
            const g = db.transaction('cards').objectStore('cards').getAll(); g.onsuccess = () => r(g.result); });
          const live = all.filter(c => !c.deleted);
          const store = db.transaction('cards', 'readwrite').objectStore('cards');
          live[0].word = 'minedelsewhere'; live[0].audio = 'lexis-gone.webm'; live[0].audioLocal = true;
          live[1].word = 'fromankiweb'; live[1].audio = 'ttsex-real.mp3'; live[1].audioLocal = false;
          store.put(live[0]); store.put(live[1]);
          await new Promise(r => { store.transaction.oncomplete = () => r(); });
        }""")
        t.page.reload()
        t.page.wait_for_selector("#screen-home:not([hidden])", timeout=90000)
        t.page.wait_for_timeout(900)
        t.open_settings()

        with t.page.expect_download(timeout=180000) as dl:
            t.page.locator("#export-apkg-btn").click()
        import zipfile
        out = t.fixtures["apkg"].parent / "orphan.apkg"
        dl.value.save_as(str(out))
        with zipfile.ZipFile(out) as z:
            data = z.read("collection.anki2")
        written = t.fixtures["apkg"].parent / "orphan.anki2"
        written.write_bytes(data)

        import sqlite3
        con = sqlite3.connect(written)
        fields = {}
        for (flds,) in con.execute("select flds from notes"):
            parts = flds.split("\x1f")
            for part in parts:
                if "minedelsewhere" in part or "fromankiweb" in part:
                    fields[("minedelsewhere" if "minedelsewhere" in part else "fromankiweb")] = flds
        con.close()

        orphan = fields.get("minedelsewhere", "")
        kept = fields.get("fromankiweb", "")
        t.check("a clip this phone does not have is not referenced",
                "lexis-gone.webm" in orphan, False)
        t.check("but the card still goes", bool(orphan), True)
        t.check("and a filename from their own collection is kept",
                "ttsex-real.mp3" in kept, True)
    finally:
        t.page = was
        page.close()
