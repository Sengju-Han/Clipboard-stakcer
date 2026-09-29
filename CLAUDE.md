# Working on this repository

## Only Add to Anki

`docs/index.html` and the `anki/` scripts and workflows behind it. That is the
part that is used every day, and it is the only part to change unless asked by
name.

**Lexis — `docs/app/` — is finished and closed.** Do not refactor it, do not
add to it, do not "improve" it in passing, and do not read through it to
understand something unless a change to Add to Anki genuinely depends on it.
Its tests still have to pass; that is all.

## Be cheap

Sessions here cost tokens and the person paying is one learner with a phone.

- **Do what was asked and stop.** No adjacent improvements, no tidying nearby
  code, no second feature because the first one suggested it.
- **Do not read the whole repository.** Open the file named in the request and
  the one thing it depends on.
- **Run the narrowest test that covers the change.**
  `python3 test/run.py account` for the Add to Anki page,
  `python3 -m pytest test/python -q` for the workflows. The full browser suite
  is fifteen minutes and is CI's job, not the session's.
- **Push once.** Every push starts an eight-minute CI run; batch the work and
  send it in one go rather than a commit at a time.
- **Do not sit watching a pull request.** Open it, say it is open, and finish.
  Hourly check-ins on a green PR spend a session's worth of tokens to learn
  nothing.
- **Answer questions with an answer**, not with a branch.

## Things that must not change

- Never write to `collection.anki2` or `collection.media` directly.
- **A FULL_UPLOAD request from AnkiWeb is always refused**, never obeyed.
- Nothing syncs unless every check passes first, and a backup is taken before
  anything is written. `anki/fold_meaning.py` is the pattern to copy.
- Anything that costs money is off by default and capped by an input the
  person sets. Say what it will cost before spending it.
- No desktop add-ons (AwesomeTTS, HyperTTS) and no Google Cloud TTS. Everything
  runs on GitHub Actions or in a browser, because there is no computer.
- The word a card exists for is the text before the first block tag in `Back`.
  Nothing goes in front of it.
