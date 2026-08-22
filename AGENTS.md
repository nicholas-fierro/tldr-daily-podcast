# Canonical reference for agents working in this repo

**Read [`docs/HANDOFF.md`](docs/HANDOFF.md) first.** It is the spec, the decision
log, and the build plan. The decisions in section 1 are settled — don't
re-litigate them unless you hit a genuine wall, and say so if you do.

## This repository is public, but treat secrets with that in mind

Never commit API keys, R2 credentials, the feed token, or the unguessable feed
path. They live in GitHub repository secrets and a gitignored local `.env`.
Generated episodes and `feed*.xml` are gitignored too.

## Working rules

- **Build in milestone order (M1 → M6, section 7).** Each milestone ends at a
  checkpoint that a human reviews. Stop there; don't run ahead.
- **Re-verify before trusting this doc on anything external.** TLDR's page
  structure, Gemini TTS model IDs, and API pricing all drift. The doc says so at
  each point.
- **Never publish a broken episode.** Section 5 is the failure matrix; a missing
  episode always beats silence, a truncation, or a read-out ad.
- Keep the TTS layer behind a clean interface — provider swap should be one file.
- The parser takes the edition slug (`tech`, `ai`, …) as a parameter from day one.

## Commands

```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt   # ffmpeg must be on PATH
pytest                            # tests/test_parse.py is the highest-value test
python main.py --date YYYY-MM-DD  # re-run a past edition
```
