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

    with t.page.expect_download(timeout=180000) as dl:
        t.page.locator("#export-apkg-btn").click()
    apkg = dl.value.path()
    with zipfile.ZipFile(apkg) as z:
        names = z.namelist()
    t.truthy("the .apkg carries a collection", "collection.anki2" in names)
    t.truthy("and a media map", "media" in names)
    t.truthy("the export says how to make Anki keep the scheduling",
             "Import any learning progress" in t.page.locator("#settings-note").inner_text())
