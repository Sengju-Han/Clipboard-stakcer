#!/usr/bin/env python3
"""Check example sentences for typos and unnatural phrasing.

Sentences typed by hand on a phone pick up the usual damage: transposed
letters, a missing space between two words, a verb that does not agree, or a
phrasing that parses but is not what anyone would actually say. A spell checker
catches the first kind and nothing else, and it flags correct words it does not
know, so this asks a model instead.

The rules it works under matter as much as the checking. A flashcard sentence is
not prose to be improved: the word being practised has to survive, the meaning
has to survive, and a blank the learner fills in themselves must not be helpfully
filled in.

Needs ANTHROPIC_API_KEY.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request

from pydantic import BaseModel

# Claude Haiku 4.5: $1 per million input tokens, $5 per million output - half
# of Sonnet 5, for work that is mostly spotting typos and writing a dictionary
# entry.
#
# It is not chosen for speed, and it did not deliver any: one word on each
# model measured 11s on Haiku against 8s on Sonnet. The model is not what makes
# a lookup feel slow. A run is about 23 seconds end to end and roughly two
# thirds of that is GitHub starting a runner, checking out and installing -
# a floor no model can get under. What actually removes the wait is that the
# page asks while you keep typing, and never asks twice for the same word.
#
# Set an ANTHROPIC_MODEL repository variable to "claude-sonnet-5" to trade the
# money back for more careful judgement, particularly on a whole-collection
# audit; do not edit this line.
MODEL = os.environ.get("ANTHROPIC_MODEL", "").strip() or "claude-haiku-4-5"

# Everything after the first block break is the learner's own note - a gloss, a
# definition, often in another language. It is not part of the sentence and is
# left exactly as found.
BLOCK_BOUNDARY = re.compile(r"<\s*(?:div|br|p|li|tr|h[1-6])\b[^>]*>", re.IGNORECASE)

MARKUP = re.compile(r"<[^>]+>|&[a-zA-Z]+;|&#\d+;")

# A key pasted on a phone arrives damaged in a handful of predictable ways, and
# the API answers every one of them with the same "API key is invalid." Stripping
# is the repair; the rest is so the log can say which damage it was without ever
# printing the key.
KEY_ALPHABET = re.compile(r"^[A-Za-z0-9_\-]+$")


def api_key() -> str:
    """The key, with the whitespace a paste picks up removed."""
    return os.environ.get("ANTHROPIC_API_KEY", "").strip()


def workspace_id() -> str:
    """Optional. Only keys that are not already tied to a workspace need it."""
    return os.environ.get("ANTHROPIC_WORKSPACE_ID", "").strip()


def client():
    """The Anthropic client, built from a repaired key.

    An organization-level key belongs to no workspace, and the API will not
    guess one: it refuses the request and asks for the workspace in a header.
    A key made inside a workspace carries it already and needs none of this,
    which is why ANTHROPIC_WORKSPACE_ID is optional.
    """
    import anthropic

    workspace = workspace_id()
    headers = {"anthropic-workspace-id": workspace} if workspace else None
    return anthropic.Anthropic(api_key=api_key(), default_headers=headers)


def key_shape() -> str:
    """Why a key looks wrong, said without revealing it."""
    raw = os.environ.get("ANTHROPIC_API_KEY", "")
    key = raw.strip()
    if not key:
        return "it is empty"

    # Whatever whitespace the paste carried was stripped before the key was
    # sent, so it is never the reason one is rejected. Say it as an aside, not
    # as the cause, or it sends you off cleaning a secret that is not at fault.
    aside = (
        " (the stored secret also has spaces or a line break around it, which "
        "were ignored)"
        if raw != key
        else ""
    )

    faults = []
    if not key.startswith("sk-ant-"):
        faults.append(
            f"it does not begin with 'sk-ant-' (it is {len(key)} characters long), "
            "so it may be a different kind of token altogether"
        )
    body = key[len("sk-ant-"):] if key.startswith("sk-ant-") else key
    if not KEY_ALPHABET.match(body or "x"):
        faults.append(
            "it contains characters a key never has - if it shows as dots or "
            "asterisks, that is the console's masked display, not the key itself"
        )
    elif key.startswith("sk-ant-") and len(key) < 90:
        faults.append(
            f"it is only {len(key)} characters, which is short for a key, so the "
            "paste was probably cut off"
        )
    if not faults:
        return (
            f"it looks well formed ({len(key)} characters), so it has most likely "
            "been revoked or belongs to a different organization" + aside
        )
    return "; ".join(faults) + aside

# LanguageTool: free, no account, no key. It reads grammar and spelling by rule,
# which is a different thing from judging whether a sentence sounds like English.
LANGUAGETOOL_URL = (
    os.environ.get("LANGUAGETOOL_URL", "").strip() or "https://api.languagetool.org/v2/check"
)

# What a rule engine may change unsupervised. "style" is deliberately absent: its
# suggestions are preferences, and a flashcard is not prose to be improved.
AUTO_FIX = {"misspelling", "grammar", "duplication", "whitespace", "typographical"}

# A sentence needing this many fixes is usually a checker having a bad time -
# a name it does not know, or another language - not a sentence that wrong.
MAX_AUTO_FIX = 5

# ' ' is the blank the learner fills in from memory. A checker that has never
# heard of the convention sees stray punctuation and tidies it away.
GAP = re.compile(r"'\s*'")

HANGUL = re.compile(r"[\uac00-\ud7a3\u1100-\u11ff\u3130-\u318f]")

SYSTEM = """You proofread example sentences on a language learner's flashcards.

For each sentence, return it corrected, or byte-for-byte unchanged when nothing \
is wrong, along with a short note for each thing you changed.

Fix: misspellings, two words run together or one word split in two, verb \
agreement, articles, prepositions, punctuation, and phrasing that is \
grammatical but not what a native speaker would say.

Do not:
- change or remove the word being practised, even for a better-fitting synonym
- change the meaning, or add or remove any information
- rewrite a correct sentence to sound more formal, literary, or elaborate
- expand contractions, or change the register or tense
- touch text in another language: leave it exactly as it is, uncorrected and \
untranslated
- fill in a blank. A quoted space -- ' ' -- marks a gap the learner fills in \
from memory. It must survive unchanged and in the same position.

A sentence that is already correct and natural is the normal case. Return it \
unchanged with no issues rather than finding something to say."""


class Checked(BaseModel):
    index: int
    corrected: str
    issues: list[str]


class Report(BaseModel):
    results: list[Checked]


def split_annotation(raw: str) -> tuple[str, str]:
    """The sentence, and the learner's own note after it, kept verbatim."""
    match = BLOCK_BOUNDARY.search(raw)
    if not match:
        return raw, ""
    return raw[: match.start()], raw[match.start() :]


def has_markup(text: str) -> bool:
    return bool(MARKUP.search(text))


def proofread(
    texts: list[str], client, *, targets: list[str] | None = None, model: str = MODEL
) -> list[Checked]:
    """Check each sentence. Returns one result per input, in the same order.

    `targets` is the word each sentence exists to practise, when known: naming it
    is what stops a correction from quietly replacing it with a word that reads
    more smoothly and teaches nothing.
    """
    if not texts:
        return []

    targets = targets or [""] * len(texts)
    lines = []
    for index, text in enumerate(texts):
        target = targets[index] if index < len(targets) else ""
        lines.append(
            f"<sentence index=\"{index}\">"
            + (f"\n<practising>{target}</practising>" if target else "")
            + f"\n{text}\n</sentence>"
        )

    response = client.messages.parse(
        model=model,
        max_tokens=16000,
        system=SYSTEM,
        messages=[{"role": "user", "content": "\n".join(lines)}],
        output_format=Report,
    )

    # A refusal returns 200 with no parsed output. One unchecked sentence is not
    # worth failing a card over, so every sentence comes back unchanged instead.
    if getattr(response, "stop_reason", None) == "refusal":
        return [Checked(index=i, corrected=text, issues=[]) for i, text in enumerate(texts)]

    by_index = {result.index: result for result in response.parsed_output.results}
    return [
        by_index.get(index, Checked(index=index, corrected=text, issues=[]))
        for index, text in enumerate(texts)
    ]


def apply_correction(raw: str, corrected: str) -> tuple[str, str]:
    """Put a corrected sentence back into the field, or explain why it was not.

    Returns the new field value and a reason it was left alone, one of which is
    always empty. A sentence carrying markup is reported but never rewritten:
    the correction comes back as plain text, and writing that back would throw
    away whatever formatting was in there.
    """
    head, tail = split_annotation(raw)
    if has_markup(head):
        return raw, "the sentence contains formatting, so it was left as typed"
    if corrected.strip() == head.strip():
        return raw, ""
    return corrected.strip() + tail, ""


# --------------------------------------------------------------------------
# LanguageTool
# --------------------------------------------------------------------------

def _languagetool_matches(text: str, url: str, language: str, timeout: float) -> list[dict]:
    """Ask LanguageTool what is wrong with one sentence.

    Transport failures are raised, not swallowed. The caller already adds the
    card as typed when a check fails; swallowing here would instead report a
    sentence nobody looked at as one that came back clean.
    """
    body = urllib.parse.urlencode({"text": text, "language": language}).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
            "User-Agent": "anki-add-card",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    matches = payload.get("matches")
    return matches if isinstance(matches, list) else []


def _worth_applying(text: str, match: dict, protected: list[tuple[int, int]]) -> tuple | None:
    """Decide whether one finding can be applied without a person looking at it.

    Returns the span, the flagged text and the replacement, or None to leave it
    alone. Everything rejected here is still reported; it is just not applied.
    """
    offset, length = match.get("offset"), match.get("length")
    if not isinstance(offset, int) or not isinstance(length, int) or length <= 0:
        return None
    start, end = offset, offset + length
    if end > len(text):
        return None

    replacements = [r.get("value", "") for r in match.get("replacements", []) if isinstance(r, dict)]
    if not replacements or not replacements[0]:
        return None

    issue = (match.get("rule") or {}).get("issueType", "")
    if issue not in AUTO_FIX:
        return None

    # Never touch a blank the learner fills in themselves.
    if any(a < end and start < b for a, b in protected):
        return None

    flagged = text[start:end]
    # Another language is not a spelling mistake.
    if HANGUL.search(flagged):
        return None
    # A capitalised word mid-sentence is usually a name the checker has not met.
    if issue == "misspelling" and start > 0 and flagged[:1].isupper():
        return None

    return start, end, flagged, replacements[0]


def check_with_languagetool(
    texts: list[str], *, url: str = "", language: str = "en-US", timeout: float = 20.0
) -> list[Checked]:
    """Correct each sentence with LanguageTool. One result per input, in order."""
    endpoint = url or LANGUAGETOOL_URL
    results: list[Checked] = []

    for index, text in enumerate(texts):
        if not text.strip():
            results.append(Checked(index=index, corrected=text, issues=[]))
            continue
        matches = _languagetool_matches(text, endpoint, language, timeout)

        protected = [m.span() for m in GAP.finditer(text)]
        usable = []
        for match in matches:
            if isinstance(match, dict):
                found = _worth_applying(text, match, protected)
                if found:
                    usable.append(found)

        if not usable:
            results.append(Checked(index=index, corrected=text, issues=[]))
            continue
        if len(usable) > MAX_AUTO_FIX:
            results.append(Checked(
                index=index, corrected=text,
                issues=[f"{len(usable)} problems found, too many to apply unsupervised: "
                        + ", ".join(f"{flagged} -> {fix}" for _, _, flagged, fix in usable[:4])
                        + " ..."],
            ))
            continue

        # Right to left, so each edit leaves the offsets of the ones before it intact.
        corrected, issues = text, []
        for start, end, flagged, fix in sorted(usable, reverse=True):
            corrected = corrected[:start] + fix + corrected[end:]
            issues.append(f"{flagged} -> {fix}")
        results.append(Checked(index=index, corrected=corrected, issues=list(reversed(issues))))

    return results
