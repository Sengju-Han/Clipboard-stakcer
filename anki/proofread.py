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

import os
import re

from pydantic import BaseModel

# Claude Sonnet 5: $2 per million input tokens, $10 per million output, which is
# roughly a fifth of Opus for a job that is mostly spotting typos. Override it
# with an ANTHROPIC_MODEL repository variable rather than editing this line.
MODEL = os.environ.get("ANTHROPIC_MODEL", "").strip() or "claude-sonnet-5"

# Everything after the first block break is the learner's own note - a gloss, a
# definition, often in another language. It is not part of the sentence and is
# left exactly as found.
BLOCK_BOUNDARY = re.compile(r"<\s*(?:div|br|p|li|tr|h[1-6])\b[^>]*>", re.IGNORECASE)

MARKUP = re.compile(r"<[^>]+>|&[a-zA-Z]+;|&#\d+;")

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
