"""Putting the export in Drive, against a server that answers like Drive does.

Not mocked at the function level: a real socket, real requests, real multipart
bodies. The mistakes this can make are all in the shape of what goes over the
wire — a query that breaks on an apostrophe, an upload that adds a second copy
instead of replacing the first — and none of those show up if the thing under
test is only asked what it intended to send.
"""

import json
import re
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "anki"))

import to_drive  # noqa: E402


class Drive:
    """Just enough of Drive to be wrong against."""

    def __init__(self):
        self.files = {}          # id -> {name, parents, body}
        self.seen = []           # every request, to look at afterwards
        self.next_id = 0
        self.refresh_seen = []
        self.refuse_lookups = None   # (status, payload) instead of answering a search
        self.break_uploads_after = None   # let this many through, then fall over
        self.uploads = 0

    def broken(self):
        """True once this has let break_uploads_after files through."""
        if self.break_uploads_after is None:
            return False
        self.uploads += 1
        return self.uploads > self.break_uploads_after

    def make(self, name, parents, body=b""):
        self.next_id += 1
        ident = f"id-{self.next_id}"
        self.files[ident] = {"name": name, "parents": parents, "body": body}
        return ident


def _serve(drive):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _send(self, payload, status=200):
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _read(self):
            length = int(self.headers.get("content-length") or 0)
            return self.rfile.read(length) if length else b""

        def do_GET(self):
            url = urlparse(self.path)
            drive.seen.append(("GET", url.path, url.query))
            if drive.refuse_lookups:
                status, payload = drive.refuse_lookups
                return self._send(payload, status)
            query = parse_qs(url.query).get("q", [""])[0]
            name = re.search(r"name = '((?:\\.|[^'\\])*)'", query)
            wanted = name.group(1).replace("\\'", "'").replace("\\\\", "\\") if name else None
            parent = re.search(r"'((?:\\.|[^'\\])*)' in parents", query)
            in_folder = parent.group(1) if parent else None
            hits = [
                {"id": i, "name": f["name"]} for i, f in drive.files.items()
                if f["name"] == wanted and (in_folder is None or in_folder in f["parents"])
            ]
            self._send({"files": hits[:1]})

        def do_POST(self):
            url = urlparse(self.path)
            raw = self._read()
            drive.seen.append(("POST", url.path, raw))
            if url.path == "/token":
                fields = parse_qs(raw.decode())
                drive.refresh_seen.append(fields.get("refresh_token", [""])[0])
                return self._send({"access_token": "an-access-token", "expires_in": 3599})
            if url.path.endswith("/upload"):
                if drive.broken():
                    return self._send({"error": {"message": "Backend Error"}}, 500)
                # multipart/related: metadata first, then the bytes.
                head, _, rest = raw.partition(b"\r\n\r\n")
                meta_raw, _, tail = rest.partition(b"\r\n--")
                meta = json.loads(meta_raw.decode())
                body = tail.split(b"\r\n\r\n", 1)[1].rsplit(b"\r\n--", 1)[0]
                return self._send({"id": drive.make(meta["name"], meta.get("parents", []), body)})
            meta = json.loads(raw.decode())
            return self._send({"id": drive.make(meta["name"], meta.get("parents", []))})

        def do_PATCH(self):
            url = urlparse(self.path)
            raw = self._read()
            drive.seen.append(("PATCH", url.path, raw))
            if drive.broken():
                return self._send({"error": {"message": "Backend Error"}}, 500)
            ident = url.path.rsplit("/", 1)[-1]
            drive.files[ident]["body"] = raw
            self._send({"id": ident})

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


@pytest.fixture
def drive(monkeypatch):
    state = Drive()
    server = _serve(state)
    base = f"http://127.0.0.1:{server.server_port}"
    monkeypatch.setattr(to_drive, "TOKEN_URL", f"{base}/token")
    monkeypatch.setattr(to_drive, "FILES_URL", f"{base}/files")
    monkeypatch.setattr(to_drive, "UPLOAD_URL", f"{base}/upload")
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "a-client")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "a-secret")
    monkeypatch.setenv("GOOGLE_REFRESH_TOKEN", "the-refresh-token")
    yield state
    server.shutdown()


def _export(tmp_path, **files):
    folder = tmp_path / "anki-export"
    folder.mkdir(exist_ok=True)
    for name, body in files.items():
        (folder / name).write_text(body, encoding="utf-8")
    return folder


def _run(tmp_path, monkeypatch, folder="Anki exports"):
    monkeypatch.setattr(sys, "argv", [
        "to_drive.py", "--from-dir", str(tmp_path / "anki-export"), "--folder", folder])
    return to_drive.main()


def _run_files(monkeypatch, *paths, folder="Anki audio"):
    argv = ["to_drive.py", "--folder", folder]
    for path in paths:
        argv += ["--file", str(path)]
    monkeypatch.setattr(sys, "argv", argv)
    return to_drive.main()


def test_the_export_arrives(tmp_path, drive, monkeypatch):
    _export(tmp_path, **{"deck.csv": "word,due\navow,2026-09-20\n", "deck.json": "{}"})
    assert _run(tmp_path, monkeypatch) == 0

    landed = {f["name"]: f["body"] for f in drive.files.values() if f["name"].startswith("deck")}
    assert sorted(landed) == ["deck.csv", "deck.json"]
    assert b"avow" in landed["deck.csv"]
    # In the folder, not loose at the top of somebody's Drive.
    folder = next(i for i, f in drive.files.items() if f["name"] == "Anki exports")
    assert all(folder in f["parents"] for f in drive.files.values() if f["name"].startswith("deck"))


def test_running_it_again_replaces_rather_than_piles_up(tmp_path, drive, monkeypatch):
    _export(tmp_path, **{"deck.csv": "first"})
    _run(tmp_path, monkeypatch)
    _export(tmp_path, **{"deck.csv": "second"})
    _run(tmp_path, monkeypatch)

    copies = [f for f in drive.files.values() if f["name"] == "deck.csv"]
    assert len(copies) == 1, "a weekly export would otherwise leave a column of them"
    assert copies[0]["body"] == b"second"
    # And the folder is reused too.
    assert len([f for f in drive.files.values() if f["name"] == "Anki exports"]) == 1
    assert any(method == "PATCH" for method, _, _ in drive.seen)


def test_a_folder_with_an_apostrophe_in_it(tmp_path, drive, monkeypatch):
    # Drive's query language is single-quoted. "Dad's words" is not exotic, and
    # without escaping it comes back as a 400 that names nothing.
    _export(tmp_path, **{"deck.csv": "x"})
    assert _run(tmp_path, monkeypatch, folder="Dad's words") == 0
    assert any(f["name"] == "Dad's words" for f in drive.files.values())

    _run(tmp_path, monkeypatch, folder="Dad's words")
    assert len([f for f in drive.files.values() if f["name"] == "Dad's words"]) == 1


def test_the_refresh_token_only_ever_goes_to_google(tmp_path, drive, monkeypatch):
    _export(tmp_path, **{"deck.csv": "x"})
    _run(tmp_path, monkeypatch)

    assert drive.refresh_seen == ["the-refresh-token"]
    # Everything else carries the short-lived access token instead.
    elsewhere = [raw for method, path, raw in drive.seen
                 if path != "/token" and isinstance(raw, bytes)]
    assert not any(b"the-refresh-token" in raw for raw in elsewhere)


def test_not_connected_says_so_rather_than_crashing(tmp_path, drive, monkeypatch):
    monkeypatch.delenv("GOOGLE_REFRESH_TOKEN")
    _export(tmp_path, **{"deck.csv": "x"})
    with pytest.raises(SystemExit) as stopped:
        _run(tmp_path, monkeypatch)
    assert stopped.value.code == 1


def test_an_empty_export_is_refused(tmp_path, drive, monkeypatch):
    # Uploading nothing quietly would leave yesterday's file in Drive looking
    # like today's.
    _export(tmp_path)
    with pytest.raises(SystemExit):
        _run(tmp_path, monkeypatch)


def test_the_query_escaping_itself():
    assert to_drive.quote_for_query("Dad's") == "Dad\\'s"
    assert to_drive.quote_for_query("a\\b") == "a\\\\b"
    assert to_drive.quote_for_query("plain") == "plain"


def test_named_files_go_up_and_their_neighbours_do_not(tmp_path, drive, monkeypatch):
    # The audio build directory is a package worth 27MB and a cache of some
    # thousands of mp3 files. Uploading the folder would put the cache in
    # somebody's Drive, which is the whole reason --file exists.
    build = tmp_path / "tts-build"
    (build / "audio").mkdir(parents=True)
    (build / "tts-update.apkg").write_bytes(b"a package")
    (build / "manifest.jsonl").write_text("{}\n", encoding="utf-8")
    for i in range(5):
        (build / "audio" / f"lexis-{i}.mp3").write_bytes(b"noise")

    assert _run_files(monkeypatch, build / "tts-update.apkg", build / "manifest.jsonl") == 0

    landed = sorted(f["name"] for f in drive.files.values() if not f["name"] == "Anki audio")
    assert landed == ["manifest.jsonl", "tts-update.apkg"]
    assert not any(f["name"].endswith(".mp3") for f in drive.files.values())


def test_a_named_file_that_is_missing_stops_everything(tmp_path, drive, monkeypatch):
    # A step that did not run looks exactly like this, and half an upload is
    # worse than none: yesterday's package would sit there looking like today's.
    here = tmp_path / "tts-build"
    here.mkdir()
    (here / "manifest.jsonl").write_text("{}\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        _run_files(monkeypatch, here / "manifest.jsonl", here / "tts-update.apkg")
    assert not [f for f in drive.files.values() if f["name"] == "manifest.jsonl"]


def test_the_same_file_named_twice_is_uploaded_once(tmp_path, drive, monkeypatch):
    folder = _export(tmp_path, **{"deck.csv": "x"})
    monkeypatch.setattr(sys, "argv", [
        "to_drive.py", "--from-dir", str(folder), "--file", str(folder / "deck.csv"),
        "--folder", "Anki exports"])
    assert to_drive.main() == 0
    assert len([f for f in drive.files.values() if f["name"] == "deck.csv"]) == 1


def test_asking_for_nothing_at_all_is_refused(tmp_path, drive, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["to_drive.py", "--folder", "Anki exports"])
    with pytest.raises(SystemExit):
        to_drive.main()


def test_a_big_upload_is_given_longer_than_a_small_one():
    # 120 seconds flat means a 27MB package must move at a quarter of a megabyte
    # a second from first byte to last, and a slow run fails as though the
    # connection had been refused.
    assert to_drive.timeout_for(b"") == 120
    assert to_drive.timeout_for(b"x" * (27 * 1024 * 1024)) > 20 * 60
    assert to_drive.timeout_for(None) == 120


# What happens when Drive does not work, which is the interesting half of
# "Drive works". None of these had a test, and all of them arrived as a Python
# traceback in the job log - which is the one kind of failure that reads as
# "this thing is broken" rather than "run it again".


def test_a_run_that_breaks_partway_says_what_did_go_up(tmp_path, drive, monkeypatch, capsys):
    # The reassuring half is not obvious and was never said: every upload
    # replaces the file of the same name, so a half-finished run is fixed by
    # pressing the button again, not by going and tidying Drive by hand.
    _export(tmp_path, **{"a.csv": "1", "b.csv": "2", "c.csv": "3"})
    drive.break_uploads_after = 1

    with pytest.raises(SystemExit) as stopped:
        _run(tmp_path, monkeypatch)
    assert stopped.value.code == 1

    out = capsys.readouterr().out
    assert "did not go up" in out
    assert "1 file(s) did go up first" in out
    assert "Running this again is safe" in out


def test_drive_not_answering_at_all_is_a_sentence(tmp_path, drive, monkeypatch, capsys):
    # A refused connection is a URLError, not an HTTP status, and nothing used
    # to catch it: the job ended on a traceback with urlopen at the bottom.
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        dead = sock.getsockname()[1]
    monkeypatch.setattr(to_drive, "FILES_URL", f"http://127.0.0.1:{dead}/files")
    _export(tmp_path, **{"deck.csv": "x"})

    with pytest.raises(SystemExit) as stopped:
        _run(tmp_path, monkeypatch)
    assert stopped.value.code == 1

    out = capsys.readouterr().out
    assert "could not reach" in out
    assert "Nothing was uploaded." in out


def test_the_drive_api_being_switched_off_points_at_the_switch(tmp_path, drive, monkeypatch, capsys):
    # The likeliest first-run failure there is. Connecting and enabling the API
    # are two different things in the Google console, and the second one is the
    # one that is easy to walk past - so the refusal should name it.
    drive.refuse_lookups = (403, {"error": {"message": (
        "Google Drive API has not been used in project 123456 before or it is disabled.")}})
    _export(tmp_path, **{"deck.csv": "x"})

    with pytest.raises(SystemExit) as stopped:
        _run(tmp_path, monkeypatch)
    assert stopped.value.code == 1

    out = capsys.readouterr().out
    assert "Could not open the Drive folder" in out
    assert "Drive API" in out and "Enable" in out
    # Google's own words as well as ours, because they carry the project number.
    assert "has not been used in project" in out


def test_nothing_is_uploaded_once_the_folder_is_refused(tmp_path, drive, monkeypatch):
    drive.refuse_lookups = (403, {"error": {"message": "nope"}})
    _export(tmp_path, **{"deck.csv": "x"})
    with pytest.raises(SystemExit):
        _run(tmp_path, monkeypatch)
    assert drive.files == {}


def test_what_already_went_up_reads_as_a_sentence():
    assert to_drive.already_up([]) == "Nothing was uploaded."
    one = to_drive.already_up([{"name": "deck.csv"}])
    assert "deck.csv" in one and "1 file(s)" in one
    two = to_drive.already_up([{"name": "a"}, {"name": "b"}])
    assert "a, b" in two and "2 file(s)" in two


# --check: whether this is connected, without running an export to find out.


def test_check_puts_one_file_in_the_real_folder(tmp_path, drive, monkeypatch):
    # The same token, the same folder, the same upload a real run does - because
    # a check that takes a different path can pass while the real thing fails.
    summary = tmp_path / "summary.md"
    monkeypatch.setattr(sys, "argv", [
        "to_drive.py", "--check", "--folder", "Anki exports", "--summary", str(summary)])
    assert to_drive.main() == 0

    landed = [f for f in drive.files.values() if f["name"] == "connected.txt"]
    assert len(landed) == 1
    assert b"connected" in landed[0]["body"].lower()
    folder = [i for i, f in drive.files.items() if f["name"] == "Anki exports"]
    assert folder and landed[0]["parents"] == folder

    said = summary.read_text(encoding="utf-8")
    assert "Google Drive is connected" in said
    assert "drive.google.com/drive/folders/" in said


def test_check_still_refuses_when_nothing_is_connected(tmp_path, drive, monkeypatch):
    monkeypatch.delenv("GOOGLE_REFRESH_TOKEN")
    monkeypatch.setattr(sys, "argv", ["to_drive.py", "--check"])
    with pytest.raises(SystemExit) as stopped:
        to_drive.main()
    assert stopped.value.code == 1
    assert drive.files == {}


def test_check_fails_loudly_when_drive_refuses(tmp_path, drive, monkeypatch, capsys):
    # The whole point of the check is that a no is a no. A green tick on a
    # broken connection would be worse than not having this at all.
    drive.refuse_lookups = (403, {"error": {"message": "Google Drive API has not been used"}})
    monkeypatch.setattr(sys, "argv", ["to_drive.py", "--check"])
    with pytest.raises(SystemExit):
        to_drive.main()
    assert "Drive API" in capsys.readouterr().out


def test_check_with_something_to_upload_is_refused_rather_than_ignored(tmp_path, drive, monkeypatch):
    # Quietly dropping the folder would look like a successful upload of it.
    _export(tmp_path, **{"deck.csv": "x"})
    monkeypatch.setattr(sys, "argv", [
        "to_drive.py", "--check", "--from-dir", str(tmp_path / "anki-export")])
    with pytest.raises(SystemExit):
        to_drive.main()
    assert drive.files == {}


def test_a_folder_moved_out_of_the_top_of_the_drive_is_still_found(tmp_path, drive, monkeypatch):
    # Tidying "Anki exports" into a subfolder is an ordinary thing to do, and
    # looking for it only at the top would quietly make a second one - leaving
    # the one being watched to stop filling up with no error anywhere.
    tidy = drive.make("Somewhere tidier", ["root"])
    moved = drive.make("Anki exports", [tidy])

    _export(tmp_path, **{"deck.csv": "x"})
    assert _run(tmp_path, monkeypatch) == 0

    folders = [i for i, f in drive.files.items() if f["name"] == "Anki exports"]
    assert folders == [moved], "a second folder was made instead of using the moved one"
    landed = [f for f in drive.files.values() if f["name"] == "deck.csv"]
    assert landed and landed[0]["parents"] == [moved]


def test_the_drive_report_is_added_to_the_summary_not_put_in_place_of_it(
        tmp_path, drive, monkeypatch):
    # export_deck.py appends its report - the counts, the per-deck table - to
    # the same file. Connecting Drive should not be the thing that takes it
    # away, and nobody would have seen that happen until Drive first worked.
    summary = tmp_path / "summary.md"
    summary.write_text("## The export\n\n| deck | cards |\n|---|---|\n| English | 1240 |\n",
                       encoding="utf-8")
    _export(tmp_path, **{"deck.csv": "x"})
    monkeypatch.setattr(sys, "argv", [
        "to_drive.py", "--from-dir", str(tmp_path / "anki-export"),
        "--folder", "Anki exports", "--summary", str(summary)])
    assert to_drive.main() == 0

    said = summary.read_text(encoding="utf-8")
    assert "| English | 1240 |" in said, "the export's own report was overwritten"
    assert "In your Google Drive" in said
    assert said.index("The export") < said.index("In your Google Drive")
