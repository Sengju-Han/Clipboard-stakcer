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
import urllib.error
import urllib.parse
import urllib.request
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


def folder_id(token: str, name: str, parent: str = "root") -> str:
    """The folder, made if it is not there yet.

    Reused rather than remade, so running this every week leaves one folder
    with the newest export in it instead of a column of identical folders.
    """
    query = (f"name = '{quote_for_query(name)}' and mimeType = '{FOLDER_TYPE}' "
             f"and '{quote_for_query(parent)}' in parents and trashed = false")
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


def files_in(folder: Path) -> list[Path]:
    return sorted(p for p in folder.rglob("*") if p.is_file())


def wanted_files(from_dir: str, named: list[str]) -> list[Path]:
    """What to upload: a whole folder, particular files, or both.

    Named files matter because a build directory is not always all wanted. The
    audio build holds a cache of thousands of mp3 files beside the one package
    that is the point of it, and uploading the folder would put every one of
    them in somebody's Drive.
    """
    found = []
    if from_dir:
        found.extend(files_in(Path(from_dir)))
    for name in named:
        path = Path(name)
        if not path.is_file():
            fail(f"{path} is not there.",
                 "Nothing was uploaded. A named file that is missing is more likely a "
                 "step that did not run than a file worth skipping.")
        found.append(path)
    # One name twice - named and inside the folder - would otherwise upload the
    # same bytes twice and be replaced by itself.
    seen, tidy = set(), []
    for path in found:
        key = path.resolve()
        if key in seen:
            continue
        seen.add(key)
        tidy.append(path)
    return tidy


def main() -> int:
    parser = argparse.ArgumentParser(description="Upload a folder to Google Drive.")
    parser.add_argument("--from-dir", default="", help="Upload everything in this folder.")
    parser.add_argument("--file", action="append", default=[],
                        help="Upload this one file. May be given more than once.")
    parser.add_argument("--folder", default="Anki exports",
                        help="Folder in your Drive. Made if it is not there.")
    parser.add_argument("--summary", default="", help="Write a report here (the job summary).")
    args = parser.parse_args()

    client_id = os.environ.get("GOOGLE_CLIENT_ID", "").strip()
    client_secret = os.environ.get("GOOGLE_CLIENT_SECRET", "").strip()
    refresh = os.environ.get("GOOGLE_REFRESH_TOKEN", "").strip()
    if not (client_id and client_secret and refresh):
        fail("Google Drive is not connected.",
             "Run the 'Connect Google Drive' workflow once. Until then the export is "
             "still downloadable from the artifacts below.")

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
             "OAuth client was deleted. Run 'Connect Google Drive' again.")

    parent = folder_id(token, args.folder)
    log(f"Uploading {len(wanted)} file(s) to Drive / {args.folder}...")

    done = []
    for path in wanted:
        file_id, what = put_file(token, path, parent)
        size = path.stat().st_size
        log(f"  {what}: {path.name} ({size // 1024}KB)")
        done.append({"name": path.name, "what": what, "bytes": size, "id": file_id})

    if args.summary:
        lines = [
            f"## In your Google Drive — {args.folder}",
            "",
            f"[Open the folder](https://drive.google.com/drive/folders/{parent})",
            "",
        ] + [f"- `{d['name']}` — {d['what']}, {d['bytes'] // 1024}KB" for d in done]
        Path(args.summary).write_text("\n".join(lines) + "\n", encoding="utf-8")
    log("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
