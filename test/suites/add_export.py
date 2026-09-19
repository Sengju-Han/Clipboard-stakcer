"""Adding a card, and every way of getting your deck back out."""

import csv
import io
import json
import zipfile


def run(t):
    t.open_app()
    total = len(t.cards())

    t.page.locator("#add-btn").click()
    t.page.wait_for_timeout(600)
    t.check("the add screen opens", t.screen(), "screen-add")

    t.page.locator("#a-word").fill("beat around the bush")
    t.page.locator("#a-hook").fill("a hunter beating the bush instead of aiming")
    t.page.locator("#a-clue").fill("우회적으로 말하다")
    t.page.locator("#a-example").fill("Just tell me — stop beating around the bush.")
    t.page.locator("#a-deck").fill("Podcasts")
    t.page.locator("#add-form button[type=submit]").click()
    t.page.wait_for_timeout(900)
    t.truthy("it says the card was added",
             "added to Podcasts" in t.page.locator("#add-note").inner_text())
    t.check("and there is one more card", len(t.cards()), total + 1)

    t.page.locator("#a-word").fill("beat around the bush")
    t.page.locator("#add-form button[type=submit]").click()
    t.page.wait_for_timeout(700)
    t.truthy("adding it twice is refused",
             "already in" in t.page.locator("#add-note").inner_text())
    t.check("and nothing was added", len(t.cards()), total + 1)

    t.page.locator("#add-close").click()
    t.page.wait_for_timeout(600)
    t.open_settings()

    with t.page.expect_download(timeout=120000) as dl:
        t.page.locator("#export-json-btn").click()
    path = dl.value.path()
    backup = json.loads(open(path, encoding="utf8").read())
    t.check("the JSON backup holds every card", backup["card_count"], total + 1)
    t.truthy("including the one just added",
             any(c["word"] == "beat around the bush" for c in backup["cards"]))
    t.truthy("and the review log", "reviews" in backup)

    with t.page.expect_download(timeout=120000) as dl:
        t.page.locator("#export-csv-btn").click()
    text = open(dl.value.path(), encoding="utf8").read()
    head = text.splitlines()[:6]
    t.truthy("the CSV declares its own shape to Anki",
             head[0] == "#separator:Comma" and any(h.startswith("#notetype") for h in head))
    t.truthy("and where the deck column is", any(h.startswith("#deck column") for h in head))
    rows = list(csv.reader(io.StringIO("\n".join(
        l for l in text.splitlines() if not l.startswith("#")))))
    t.check("one row per card", len(rows), total + 1)
    t.check("four columns, so nothing spills into tags", len(rows[0]), 4)
    _anki_reads_the_csv(t, dl.value.path())

    with t.page.expect_download(timeout=180000) as dl:
        t.page.locator("#export-apkg-btn").click()
    apkg = dl.value.path()
    with zipfile.ZipFile(apkg) as z:
        names = z.namelist()
    t.truthy("the .apkg carries a collection", "collection.anki2" in names)
    t.truthy("and a media map", "media" in names)
    t.truthy("the export says how to make Anki keep the scheduling",
             "Import any learning progress" in t.page.locator("#settings-note").inner_text())


def _anki_reads_the_csv(t, path):
    """Hand the CSV to Anki itself rather than to a reading of the spec.

    The first version of this export put four columns of content onto Basic's
    two fields, and Anki did what it always does with the surplus: every
    example sentence arrived as nine tags. Counting columns catches nothing
    about that. Anki counting them does.
    """
    try:
        from anki.collection import Collection
        from anki.import_export_pb2 import ImportCsvRequest
    except ImportError:
        t.note("skipped", "the Anki library is not installed; see test/python/requirements.txt")
        return

    import shutil
    import tempfile

    mine = {tag for card in t.cards() for tag in (card.get("tags") or [])}

    work = tempfile.mkdtemp(prefix="lexis-csv-")
    try:
        col = Collection(f"{work}/c.anki2")
        meta = col.get_csv_metadata(path=str(path), delimiter=None)
        t.check("Anki reads the separator out of the header", meta.delimiter, 4)  # comma
        t.check("and that the fields hold HTML", meta.is_html, True)
        t.check("and which column is the tags", meta.tags_column, 3)
        t.check("and which is the deck", meta.deck_column, 4)

        col.import_csv(ImportCsvRequest(path=str(path), metadata=meta))
        notes = col.find_notes("")
        t.check("every card arrives", len(notes), len(t.cards()))
        # The app tags what it created with "lexis" on purpose, so the test is
        # not "no tags" but "no tags that are really a sentence": the original
        # bug turned "Just tell me — stop beating around the bush." into the
        # nine tags ['around','beating','bush.','just','me','stop','tell','The','—'].
        tags = {tag for nid in notes for tag in col.get_note(nid).tags}
        t.note("every tag in the imported collection", ", ".join(sorted(tags)) or "none")
        # Compared against the deck rather than against nothing. The deck used
        # to carry no tags at all, so "no tags but lexis" was the same check as
        # this one and shorter to write; it stopped being true the moment a
        # rebuilt deck brought the collection's own tags across, and a suite
        # that goes red because somebody's data changed is a suite nobody
        # believes. What must hold is that the round trip invents nothing.
        t.check("every tag came from a card, none from a sentence",
                sorted(tags - {"lexis"} - mine), [])
        t.truthy(f"and the ones the cards had survived it ({len(mine & tags)} of {len(mine)})",
                 mine <= tags or not mine)
        t.note("why that matters",
               "four columns onto a two-field notetype turns every sentence into nine tags")
        decks = sorted(d["name"] for d in col.decks.all())
        t.truthy(f"and the decks come with them ({', '.join(decks)})", len(decks) > 1)
        t.truthy("with a clean database afterwards",
                 "Database rebuilt" in col.fix_integrity()[0])
        col.close()
    finally:
        shutil.rmtree(work, ignore_errors=True)
