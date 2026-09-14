#!/usr/bin/env python3
"""Explain one English word for a Korean learner, and cache the answer.

A dictionary says what a word means. It does not say whether you would use it in
a job application or to a friend, which words it habitually travels with, or
which near-identical word you are about to confuse it with. Those are the things
that decide whether a word is actually usable, and they are what this asks for.

Answers are written to a cache in the repository, keyed by the word. Looking the
same word up again is then free and instant, which over a year of study is most
lookups.

    ANTHROPIC_API_KEY, ANTHROPIC_MODEL (optional)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

from pydantic import BaseModel

from proofread import MODEL, api_key, client, key_shape  # noqa: E402

SYSTEM = """You explain English words to a Korean adult who is building \
vocabulary flashcards. They are past beginner: they know the common words and \
are working on the ones that make writing sound native.

Explain the word as given. If it is a phrase or a phrasal verb, explain the \
phrase, not its parts.

- meaning: the plain English definition, one line. Say what it means before \
saying anything clever about it.
- pronunciation: IPA, in slashes, as a British or American speaker would say it.
- korean: the most accurate Korean gloss. If no single word fits, give the \
short phrase that does. Do not list five near-synonyms.
- tone: the register, as one of formal, neutral, casual, slang, literary, \
technical. Say which, and nothing else.
- nuance: two sentences at most. What this word carries that a plainer synonym \
does not, and when a native speaker would reach for it. Concrete, not abstract.
- collocations: the words it habitually travels with, written as real \
fragments — "a resilient economy", "bounce back from". Three to five. These \
matter more than the definition for sounding natural.
- confusables: words this learner is likely to reach for by mistake, each with \
the one distinction that separates them. Two at most. Skip it if nothing \
genuinely competes.
- examples: two sentences a real person would say, at this learner's level, \
showing different senses or registers if the word has them.
- memory_hook: one line. An etymology, a cognate, or an image that makes it \
stick. Skip it rather than force something weak.

Be specific and brief. This goes on a flashcard that will be read hundreds of \
times, so every word has to earn its place."""


class Confusable(BaseModel):
    word: str
    difference: str


class Explanation(BaseModel):
    word: str
    meaning: str
    pronunciation: str
    korean: str
    tone: str
    nuance: str
    collocations: list[str]
    confusables: list[Confusable]
    examples: list[str]
    memory_hook: str


def slug(term: str) -> str:
    """Cache key. Stable, lowercase, and safe as a filename."""
    return re.sub(r"[^a-z0-9]+", "-", term.strip().lower()).strip("-")


def explain(term: str, client, *, model: str = "") -> Explanation:
    response = client.messages.parse(
        model=model or MODEL,
        max_tokens=4000,
        system=SYSTEM,
        messages=[{"role": "user", "content": term.strip()}],
        output_format=Explanation,
    )
    if getattr(response, "stop_reason", None) == "refusal":
        raise RuntimeError("the model declined to explain this word")
    return response.parsed_output


def main() -> int:
    parser = argparse.ArgumentParser(description="Explain a word and cache the answer.")
    parser.add_argument("--word", required=True)
    parser.add_argument("--cache-dir", default="docs/lookups")
    parser.add_argument("--model", default="")
    parser.add_argument("--force", action="store_true", help="Re-ask even if it is cached.")
    args = parser.parse_args()

    term = args.word.strip()
    if not term:
        print("::error::No word was given.", flush=True)
        return 1
    if len(term) > 80:
        print("::error::That is too long to be a word or a phrase.", flush=True)
        return 1

    key = slug(term)
    if not key:
        print(f"::error::{term!r} has nothing to look up in it.", flush=True)
        return 1

    target = Path(args.cache_dir) / f"{key}.json"
    if target.exists() and not args.force:
        print(f"Already cached at {target}; nothing to do.", flush=True)
        return 0

    import anthropic

    # Without this the SDK raises a TypeError about header resolution, which
    # says nothing about what to actually do. The fix is one setting, so name it.
    if not api_key():
        print(
            "::error::ANTHROPIC_API_KEY is not set for this repository. "
            "Add it under Settings -> Secrets and variables -> Actions -> "
            "New repository secret, named exactly ANTHROPIC_API_KEY. A secret on "
            "your account or on an environment is not the same thing and this "
            "workflow cannot see it.",
            flush=True,
        )
        return 1

    print(f"Asking about {term!r}...", flush=True)
    try:
        answer = explain(term, client(), model=args.model)
    except anthropic.AuthenticationError:
        print(
            f"::error::The API key was rejected: {key_shape()}. Replace the "
            "ANTHROPIC_API_KEY secret with a key copied whole from "
            "console.anthropic.com. The console shows a key in full only at the "
            "moment you create it; afterwards it is masked, and the masked form "
            "is not a usable key.",
            flush=True,
        )
        return 1
    except anthropic.BadRequestError as exc:
        note = str(exc).lower()
        if "workspace" in note:
            print(
                "::error::The key works, but it belongs to the organization rather "
                "than to any one workspace, and the API will not pick a workspace "
                "for it. Two ways out, either is fine: make a new key from inside a "
                "workspace at console.anthropic.com (it carries the workspace with "
                "it, and nothing else needs changing), or add the workspace's ID as "
                "a repository variable named ANTHROPIC_WORKSPACE_ID and this will "
                "send it.",
                flush=True,
            )
            return 1
        if "credit balance" in note:
            print(
                "::error::The key works, but the account has no credit. Buy credit "
                "at console.anthropic.com -> Billing. This is separate from a "
                "Claude subscription; a Pro or Max plan does not pay for API calls.",
                flush=True,
            )
            return 1
        raise

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(answer.model_dump(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Wrote {target}", flush=True)

    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        lines = [
            f"## {answer.word}", "",
            f"**{answer.korean}** · _{answer.tone}_ · `{answer.pronunciation}`", "",
            answer.meaning, "",
            answer.nuance, "",
            "**Goes with:** " + ", ".join(f"`{c}`" for c in answer.collocations),
        ]
        if answer.confusables:
            lines += ["", "**Not to be confused with:**"] + [
                f"- **{c.word}** — {c.difference}" for c in answer.confusables
            ]
        lines += ["", "**Examples:**"] + [f"- {e}" for e in answer.examples]
        if answer.memory_hook:
            lines += ["", f"**Remember it:** {answer.memory_hook}"]
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
