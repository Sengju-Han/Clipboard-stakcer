"""Putting the export in Drive, against a server that answers like Drive does.

Not mocked at the function level: a real socket, real requests, real multipart
bodies. The mistakes this can make are all in the shape of what goes over the
wire — a query that breaks on an apostrophe, an upload that adds a second copy
instead of replacing the first — and none of those show up if the thing under
test is only asked what it intended to send.
"""

import json
import re
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
