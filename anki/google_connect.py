#!/usr/bin/env python3
"""Connect Google Drive once, without a computer.

Two halves, run as two goes at the same workflow.

Without a code it prints the link to approve. With the code that link gives
back, it swaps that for a refresh token - the lasting half of the connection -
and writes it straight into this repository's secrets.

Straight into the secrets, rather than printed for copying, because a refresh
token printed into the log of a public repository is a refresh token somebody
else now has. The code in between is single-use and worth nothing once spent,
which is why that one is safe to read off a screen.

    GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, GITHUB_TOKEN, GITHUB_REPOSITORY
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"

# The narrowest scope that can do the job: files this made, and nothing else in
# the Drive. It cannot read what was already there.
SCOPE = "https://www.googleapis.com/auth/drive.file"

SECRET_NAME = "GOOGLE_REFRESH_TOKEN"


def log(message: str) -> None:
    print(message, flush=True)


def fail(message: str, detail: str = "") -> None:
    log(f"::error::{message}")
    if detail:
        log(detail)
    sys.exit(1)


def consent_url(client_id: str, redirect: str) -> str:
    return AUTH_URL + "?" + urllib.parse.urlencode({
        "client_id": client_id,
        "redirect_uri": redirect,
        "response_type": "code",
        "scope": SCOPE,
        # Offline is what asks for a refresh token at all, and consent forces
        # one to be issued again even for an account that has approved before -
        # without it a second attempt returns an access token and no refresh
        # token, and there is nothing to store.
        "access_type": "offline",
        "prompt": "consent",
    })


def post_form(url: str, fields: dict) -> dict:
    body = urllib.parse.urlencode(fields).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as err:
        raise RuntimeError(f"{err.code}: {err.read().decode('utf-8', 'replace')[:400]}") from None


def exchange(client_id: str, client_secret: str, code: str, redirect: str) -> str:
    out = post_form(TOKEN_URL, {
        "client_id": client_id,
        "client_secret": client_secret,
        "code": code,
        "redirect_uri": redirect,
        "grant_type": "authorization_code",
    })
    token = out.get("refresh_token", "")
    if not token:
        raise RuntimeError(
            "Google returned no refresh token. That happens when the code has already "
            "been used - they work once - or when this account approved before and was "
            "not asked again. Start over from the link; it asks for consent every time.")
    return token


# ---- writing it into the repository's secrets --------------------------------

def github(url: str, token: str, method: str = "GET", payload: dict | None = None) -> dict:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
        "Content-Type": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            body = response.read().decode("utf-8")
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as err:
        raise RuntimeError(f"{err.code}: {err.read().decode('utf-8', 'replace')[:400]}") from None


def seal(public_key: str, value: str) -> str:
    """Encrypt to the repository's public key, which is how GitHub takes secrets.

    The plain text never reaches GitHub, only something only the repository can
    open - so the token is not readable even in the request that stores it.
    """
    from nacl import encoding, public

    box = public.SealedBox(public.PublicKey(public_key.encode("utf-8"), encoding.Base64Encoder()))
    return base64.b64encode(box.encrypt(value.encode("utf-8"))).decode("utf-8")


def store_secret(repo: str, token: str, name: str, value: str) -> None:
    key = github(f"https://api.github.com/repos/{repo}/actions/secrets/public-key", token)
    github(
        f"https://api.github.com/repos/{repo}/actions/secrets/{name}", token, method="PUT",
        payload={"encrypted_value": seal(key["key"], value), "key_id": key["key_id"]},
    )


def write_summary(lines: list[str]) -> None:
    where = os.environ.get("GITHUB_STEP_SUMMARY")
    text = "\n".join(lines) + "\n"
    if where:
        with open(where, "a", encoding="utf-8") as handle:
            handle.write(text)
    else:
        log(text)


def main() -> int:
    parser = argparse.ArgumentParser(description="Connect Google Drive, in two goes.")
    parser.add_argument("--code", default="", help="The code from the page Google sent you to.")
    parser.add_argument("--redirect", required=True)
    args = parser.parse_args()

    client_id = os.environ.get("GOOGLE_CLIENT_ID", "").strip()
    client_secret = os.environ.get("GOOGLE_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        fail("GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET are not set.",
             "Make an OAuth client of type 'Web application' in the Google Cloud console, "
             f"with {args.redirect} as an authorised redirect URI, and put the two values "
             "in this repository's Actions secrets.")

    code = args.code.strip()
    if not code:
        write_summary([
            "## Approve it, then come back",
            "",
            f"1. [Open this link and approve]({consent_url(client_id, args.redirect)})",
            "2. Google sends you to a page showing a code. Tap **Copy the code**.",
            "3. Run this workflow again, with the code pasted into **code**.",
            "",
            "The link asks for one thing only: the files this makes in your Drive. It "
            "cannot see anything that is already there.",
        ])
        log("Printed the link. Approve it, then run this again with the code.")
        return 0

    repo = os.environ.get("GITHUB_REPOSITORY", "").strip()
    gh_token = os.environ.get("GITHUB_TOKEN", "").strip()
    if not repo or not gh_token:
        fail("There is nowhere to store the result.",
             "GITHUB_REPOSITORY and GITHUB_TOKEN are set by Actions; this needs to run there.")

    try:
        refresh = exchange(client_id, client_secret, code, args.redirect)
    except RuntimeError as err:
        fail("Google would not swap that code.", str(err))

    try:
        store_secret(repo, gh_token, SECRET_NAME, refresh)
    except RuntimeError as err:
        fail(f"Could not store {SECRET_NAME}.",
             f"{err}\n\nThe token this workflow runs with needs permission to write "
             "secrets. In the workflow that is `permissions: secrets: write`; for a "
             "fine-grained personal token it is 'Secrets: Read and write'.")

    write_summary([
        "## Connected",
        "",
        f"`{SECRET_NAME}` is stored. It was never printed anywhere, including here.",
        "",
        "The export workflow will put its files straight into your Drive from now on.",
        "",
        "The code you pasted is spent and does nothing now.",
    ])
    log("Stored the refresh token. Nothing was printed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
