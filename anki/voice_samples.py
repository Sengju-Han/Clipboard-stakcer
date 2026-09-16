#!/usr/bin/env python3
"""Record the same sentences in several voices, as a page you can play on a phone.

Choosing a voice from a list of names is guesswork, and re-voicing a thousand
cards to find out is an expensive way to guess. This reads a handful of
sentences in each voice and writes them next to a page that plays them, so the
choice is made by ear before anything is changed.

Nothing here touches a collection. It needs no account and no key: the voices
are Microsoft Edge's, the same ones build_tts_apkg.py uses, so what you hear on
the page is what the cards would say.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from build_tts_apkg import MIN_BYTES, edge_audio  # noqa: E402
from export_deck import fail, log, plural  # noqa: E402

# The voices worth comparing for American English, with the British pair at the
# end. The Multilingual ones are the newer models; they are usually the reason
# somebody thinks the audio got better.
VOICES = [
    "en-US-AvaNeural",
    "en-US-AvaMultilingualNeural",
    "en-US-AndrewNeural",
    "en-US-AndrewMultilingualNeural",
    "en-US-EmmaNeural",
    "en-US-EmmaMultilingualNeural",
    "en-US-BrianNeural",
    "en-US-BrianMultilingualNeural",
    "en-US-JennyNeural",
    "en-US-GuyNeural",
    "en-GB-SoniaNeural",
    "en-GB-RyanNeural",
]

# Chosen to expose what actually differs between these voices, rather than to
# sound nice: a contraction and a reduced vowel, a number and a date read out,
# and a consonant cluster that several of them fumble. Deliberately not taken
# from anybody's collection — this page is published, and study notes are not.
SENTENCES = [
    "I'd have told you about it, but you weren't there.",
    "She asked for twenty-three copies by the fourth of March.",
    "The sixth sixth-form student insisted the texts were his.",
]


async def record(voice: str, sentences: list[str], out_dir: Path) -> dict | None:
    """One voice, every sentence. None if the service will not speak it."""
    files = []
    for index, text in enumerate(sentences):
        try:
            audio = await edge_audio(text, voice)
        except Exception as exc:
            log(f"::warning::{voice} could not be recorded ({type(exc).__name__}: {exc}); skipping it.")
            return None
        if len(audio) < MIN_BYTES:
            log(f"::warning::{voice} returned {len(audio)} bytes for sentence {index + 1}; skipping it.")
            return None
        name = f"{voice}-{index + 1}.mp3"
        (out_dir / name).write_bytes(audio)
        files.append(name)
    return {"voice": voice, "files": files}


async def record_all(voices: list[str], sentences: list[str], out_dir: Path) -> list[dict]:
    done = []
    for voice in voices:
        entry = await record(voice, sentences, out_dir)
        if entry:
            done.append(entry)
            log(f"  {voice}")
    return done


def main() -> int:
    parser = argparse.ArgumentParser(description="Record sample sentences in several voices.")
    parser.add_argument("--voices", default=",".join(VOICES))
    parser.add_argument("--out-dir", default="docs/voices")
    args = parser.parse_args()

    voices = [v.strip() for v in args.voices.split(",") if v.strip()]
    if not voices:
        fail("No voices given.")

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    # A voice dropped from the list should lose its files too, or the page
    # keeps offering something it no longer lists.
    for stale in out_dir.glob("*.mp3"):
        stale.unlink()

    log(f"Recording {plural(len(voices), 'voice')}...")
    entries = asyncio.run(record_all(voices, SENTENCES, out_dir))
    if not entries:
        fail("No voice could be recorded.",
             "The speech service refused every request. Nothing was written.")

    (out_dir / "index.json").write_text(
        json.dumps({"sentences": SENTENCES, "voices": entries}, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    total = sum(f.stat().st_size for f in out_dir.glob("*.mp3"))
    log(f"{plural(len(entries), 'voice')} recorded, {total // 1024}KB.")
    if len(entries) < len(voices):
        log(f"::warning::{len(voices) - len(entries)} of {len(voices)} voices were refused "
            "and are not on the page.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
