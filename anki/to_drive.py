#!/usr/bin/env python3
"""Put a folder of files into Google Drive.

An export that ends up as a GitHub artifact is a zip behind a login, with a
seven-day fuse, downloaded onto a phone that then has to unzip it. Drive is
where the file actually wants to be.

It signs in as you, not as a robot. A service account would be less setup, but
a service account has no storage of its own, so a file it uploads into a folder
you shared with it is refused - "Service Accounts do not have storage quota" -
and the fix is always the same: use the account that owns the folder.

The scope is drive.file, which is the narrowest one that works: this can see
and change the files it made itself and nothing else in your Drive. It cannot
read anything that was already there.

    GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, GOOGLE_REFRESH_TOKEN
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

TOKEN_URL = "https://oauth2.googleapis.com/token"
FILES_URL = "https://www.googleapis.com/drive/v3/files"
UPLOAD_URL = "https://www.googleapis.com/upload/drive/v3/files"
FOLDER_TYPE = "application/vnd.google-apps.folder"

# Anything bigger than this deserves a resumable upload rather than one request.
# The exports are kilobytes and the audio package is tens of megabytes, both of
# which are fine in one go; this is here so that a surprise is a clear error
# rather than a timeout.
MAX_SIMPLE = 100 * 1024 * 1024


def log(message: str) -> None:
    print(message, flush=True)


def fail(message: str, detail: str = "") -> None:
    log(f"::error::{message}")
    if detail:
        log(detail)
    sys.exit(1)


def quote_for_query(value: str) -> str:
    """Drive's query language is single-quoted, so a name with one breaks it.

    A deck called Dad's words is not exotic, and the failure without this is a
    400 from the API rather than anything that names the problem.
    """
    return value.replace("\\", "\\\\").replace("'", "\\'")


def timeout_for(payload: bytes | None) -> int:
    """Long enough for what is actually being sent.

    One number cannot suit both a kilobyte of CSV and a 27MB audio package: at
    120 seconds the package needs a quarter of a megabyte a second throughout,
    and a run that is merely slow fails as though the connection were refused.
    Two minutes, plus a minute for every megabyte.
    """
    return 120 + len(payload or b"") // (1024 * 1024) * 60


def request(url: str, *, token: str = "", method: str = "GET",
            data: bytes | None = None, headers: dict | None = None) -> dict:
    head = {"Accept": "application/json", **(headers or {})}
    if token:
        head["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, headers=head, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout_for(data)) as response:
            body = response.read().decode("utf-8")
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as err:
        text = err.read().decode("utf-8", "replace")[:500]
        # Google says why in the body; the status alone never does.
        raise RuntimeError(f"{err.code} from {urllib.parse.urlparse(url).netloc}: {text}") from None
    except (urllib.error.URLError, TimeoutError) as err:
        # No reply at all - DNS, TLS, a refused connection, a run that timed
        # out mid-upload. Left to itself this arrives as a traceback, and a
        # traceback is the one kind of failure that reads as "this is broken"
        # rather than "the network was having a moment, run it again".
        why = getattr(err, "reason", err) or err
        raise RuntimeError(
            f"could not reach {urllib.parse.urlparse(url).netloc}: {why}") from None


def access_token(client_id: str, client_secret: str, refresh_token: str) -> str:
    body = urllib.parse.urlencode({
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }).encode("utf-8")
    out = request(TOKEN_URL, method="POST", data=body,
                  headers={"Content-Type": "application/x-www-form-urlencoded"})
    token = out.get("access_token", "")
    if not token:
        raise RuntimeError("The refresh token was accepted but no access token came back.")
    return token


def multipart(metadata: dict, payload: bytes, content_type: str) -> tuple[bytes, str]:
    """One request carrying both what the file is and what is in it."""
    boundary = "----anki-export-boundary-7f3a9c"
    parts = [
        f"--{boundary}\r\n".encode(),
        b"Content-Type: application/json; charset=UTF-8\r\n\r\n",
        json.dumps(metadata).encode("utf-8"),
        f"\r\n--{boundary}\r\n".encode(),
        f"Content-Type: {content_type}\r\n\r\n".encode(),
        payload,
        f"\r\n--{boundary}--\r\n".encode(),
    ]
    return b"".join(parts), f"multipart/related; boundary={boundary}"


def find_one(token: str, query: str) -> dict | None:
    url = f"{FILES_URL}?{urllib.parse.urlencode({'q': query, 'fields': 'files(id,name)', 'pageSize': 1})}"
    files = request(url, token=token).get("files", [])
    return files[0] if files else None


def folder_id(token: str, name: str, parent: str = "root", *, anywhere: bool = False) -> str:
    """The folder, made if it is not there yet.

    Reused rather than remade, so running this every week leaves one folder
    with the newest export in it instead of a column of identical folders.

    `anywhere` is for the one folder at the top, which is looked for by name
    and not by where it sits. Under drive.file the only folders this can see at
    all are ones it made itself, so the name is already as specific as it needs
    to be - and tidying "Anki exports" into a subfolder of your Drive is a thing
    people do. Insisting it be at the top would mean the moved folder quietly
    stops filling up while a fresh one appears beside it.

    The folders inside it are not looked for that way: `by-deck` is only ever
    this export's `by-deck`.
    """
    query = (f"name = '{quote_for_query(name)}' and mimeType = '{FOLDER_TYPE}' "
             "and trashed = false")
    if not anywhere:
        query += f" and '{quote_for_query(parent)}' in parents"
    found = find_one(token, query)
    if found:
        return found["id"]
    made = request(FILES_URL, token=token, method="POST",
                   data=json.dumps({"name": name, "mimeType": FOLDER_TYPE,
                                    "parents": [parent]}).encode("utf-8"),
                   headers={"Content-Type": "application/json"})
    return made["id"]


def put_file(token: str, path: Path, parent: str) -> tuple[str, str]:
    """Upload, replacing a file of the same name rather than adding a second.

    Returns the file id and what happened, because "updated" and "created" are
    the difference between a folder that stays tidy and one that fills up.
    """
    payload = path.read_bytes()
    if len(payload) > MAX_SIMPLE:
        raise RuntimeError(
            f"{path.name} is {len(payload) // (1024 * 1024)}MB, which is more than this "
            "uploads in one request.")
    kind = mimetypes.guess_type(path.name)[0] or "application/octet-stream"

    query = (f"name = '{quote_for_query(path.name)}' and '{quote_for_query(parent)}' in parents "
             "and trashed = false")
    existing = find_one(token, query)
    if existing:
        # Replacing the contents keeps the id, so a link to it keeps working.
        out = request(f"{UPLOAD_URL}/{existing['id']}?uploadType=media",
                      token=token, method="PATCH", data=payload,
                      headers={"Content-Type": kind})
        return out.get("id", existing["id"]), "updated"

    body, content_type = multipart({"name": path.name, "parents": [parent]}, payload, kind)
    out = request(f"{UPLOAD_URL}?uploadType=multipart", token=token, method="POST",
                  data=body, headers={"Content-Type": content_type})
    return out.get("id", ""), "created"


def already_up(done: list[dict]) -> str:
    """What made it, for a run that stopped partway.

    A half-finished upload looks like something to go and tidy by hand, and it
    is not: every upload replaces the file of the same name, so the fix is to
    run it again. That is worth saying at the moment it stops, because it is
    the difference between a person editing their Drive and a person pressing
    the button twice.
    """
    if not done:
        return "Nothing was uploaded."
    names = ", ".join(d["name"] for d in done)
    return (f"{len(done)} file(s) did go up first: {names}. Running this again is safe: "
            "each file replaces the one of the same name rather than adding a second.")


def add_to_summary(where: str, lines: list[str]) -> None:
    """Added to the job summary, not put in place of it.

    export_deck.py appends its report - the card counts, the per-deck table -
    to the same file. Writing over it would mean that connecting Drive quietly
    took the report away, which is a strange price for a file arriving in a
    nicer place.
    """
    with open(where, "a", encoding="utf-8") as handle:
        handle.write("\n" + "\n".join(lines) + "\n")


def files_in(folder: Path) -> list[Path]:
    return sorted(p for p in folder.rglob("*") if p.is_file())


def wanted_files(from_dir: str, named: list[str]) -> list[tuple[Path, str]]:
    """What to upload, each with the folder it belongs in.

    The second half of each pair is where the file sits inside --from-dir, so
    that the shape of the export survives the trip. A deck export is not a flat
    pile: it is eight files at the top, a csv per deck in `by-deck` and a json
    per note type in `notetypes`. Flattening it would put thirty-odd files in
    one folder, and - worse - two files with the same name in different folders
    would land on each other, with the log calling the second one "updated".

    Named files matter because a build directory is not always all wanted. The
    audio build holds a cache of thousands of mp3 files beside the one package
    that is the point of it, and uploading the folder would put every one of
    them in somebody's Drive.
    """
    found = []
    if from_dir:
        base = Path(from_dir)
        for path in files_in(base):
            where = path.parent.relative_to(base).as_posix()
            found.append((path, "" if where == "." else where))
    for name in named:
        path = Path(name)
        if not path.is_file():
            fail(f"{path} is not there.",
                 "Nothing was uploaded. A named file that is missing is more likely a "
                 "step that did not run than a file worth skipping.")
        found.append((path, ""))
    # One name twice - named and inside the folder - would otherwise upload the
    # same bytes twice and be replaced by itself.
    seen, tidy = set(), []
    for path, where in found:
        key = path.resolve()
        if key in seen:
            continue
        seen.add(key)
        tidy.append((path, where))
    return tidy


def main() -> int:
    parser = argparse.ArgumentParser(description="Upload a folder to Google Drive.")
    parser.add_argument("--from-dir", default="", help="Upload everything in this folder.")
    parser.add_argument("--file", action="append", default=[],
                        help="Upload this one file. May be given more than once.")
    parser.add_argument("--folder", default="Anki exports",
                        help="Folder in your Drive. Made if it is not there.")
    parser.add_argument("--summary", default="",
                        help="Add a report to this file (the job summary).")
    parser.add_argument("--check", action="store_true",
                        help="Upload one small file to prove the connection works.")
    args = parser.parse_args()

    client_id = os.environ.get("GOOGLE_CLIENT_ID", "").strip()
    client_secret = os.environ.get("GOOGLE_CLIENT_SECRET", "").strip()
    refresh = os.environ.get("GOOGLE_REFRESH_TOKEN", "").strip()
    if not (client_id and client_secret and refresh):
        fail("Google Drive is not connected.",
             "Open https://sengju-han.github.io/Clipboard-stakcer/connected.html once "
             "and follow it; the steps are written out in anki/README.md."
             + ("" if args.check else " Until then the export is still downloadable "
                "from the artifacts below."))

    if args.check and (args.from_dir or args.file):
        fail("--check uploads its own file, so it cannot take --from-dir or --file.",
             "Nothing was uploaded. Run it with --check on its own to test the "
             "connection, or without it to upload something.")

    if args.check:
        # Everything a real run does - a token, the folder, an upload - on one
        # small file, so that finding out whether this is connected does not
        # mean a full export and a sync of the whole collection first.
        proof = Path(tempfile.mkdtemp()) / "connected.txt"
        proof.write_text(
            "Google Drive is connected to the Anki export.\n"
            f"Checked {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}.\n"
            "This file is only proof that it works. Deleting it changes nothing.\n",
            encoding="utf-8")
        wanted = [(proof, "")]
    else:
        if not args.from_dir and not args.file:
            fail("Nothing to upload.", "Give --from-dir, or --file, or both.")
        wanted = wanted_files(args.from_dir, args.file)
        if not wanted:
            fail(f"There is nothing in {args.from_dir} to upload.")

    try:
        token = access_token(client_id, client_secret, refresh)
    except RuntimeError as err:
        fail("Google would not accept the stored connection.",
             f"{err}\n\nThis usually means the refresh token was withdrawn, or the "
             "OAuth client was deleted — or the consent screen is still on Testing, "
             "which expires a connection after seven days. Publish the app in the "
             "Google Cloud console, then connect again at "
             "https://sengju-han.github.io/Clipboard-stakcer/connected.html")

    try:
        parent = folder_id(token, args.folder, anywhere=True)
    except RuntimeError as err:
        fail(f"Could not open the Drive folder “{args.folder}”.",
             f"{err}\n\nNothing was uploaded. If that is Google refusing rather than "
             "Google not answering, the likeliest reason is that the Drive API is not "
             "switched on for this Google Cloud project - which is a separate thing "
             "from connecting, and the step that is easiest to skip. APIs & Services "
             "→ Library → Google Drive API → Enable.")

    log(f"Uploading {len(wanted)} file(s) to Drive / {args.folder}...")

    # One lookup per folder rather than per file, and made in order so that
    # by-deck/ is created once and then reused for every deck in it.
    folders = {"": parent}

    def folder_for(where: str) -> str:
        if where not in folders:
            here = parent
            for part in where.split("/"):
                here = folder_id(token, part, here)
            folders[where] = here
        return folders[where]

    done = []
    for path, where in wanted:
        shown = f"{where}/{path.name}" if where else path.name
        try:
            file_id, what = put_file(token, path, folder_for(where))
        except RuntimeError as err:
            fail(f"{shown} did not go up.", f"{err}\n\n{already_up(done)}")
        size = path.stat().st_size
        log(f"  {what}: {shown} ({size // 1024}KB)")
        done.append({"name": shown, "what": what, "bytes": size, "id": file_id})

    if args.summary and args.check:
        lines = [
            "## Google Drive is connected",
            "",
            f"A file went into **{args.folder}** and came back with an id, which means "
            "the client ID, the client secret and the refresh token are all right and "
            "the Drive API is switched on.",
            "",
            f"[Open the folder](https://drive.google.com/drive/folders/{parent})",
            "",
            "The next **Anki deck export** goes here by itself.",
        ]
        add_to_summary(args.summary, lines)
    elif args.summary:
        lines = [
            f"## In your Google Drive — {args.folder}",
            "",
            f"[Open the folder](https://drive.google.com/drive/folders/{parent})",
            "",
        ] + [f"- `{d['name']}` — {d['what']}, {d['bytes'] // 1024}KB" for d in done]
        add_to_summary(args.summary, lines)
    log("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
