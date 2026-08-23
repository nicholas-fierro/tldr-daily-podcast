# AGENTS.md

The working contract for coding agents in this repository.

[`README.md`](README.md) is the canonical reference for **what this automation
does** — pipeline, stages, decisions, failure matrix, secrets. Read it first.
This file covers **how to change it**.

There is no separate spec or handoff document. The README and this file are it.
If you learn something that contradicts either, fix the document in the same
change.

---

## The one rule

**Never ship a broken episode.** A missing episode is a far better outcome than
silence, a truncated file, or an ad read out loud. Every design choice in the
failure matrix follows from this. When you're unsure whether to degrade or fail,
degrade for individual items and fail for the episode as a whole.

---

## Secrets

This repository is **public**.

- Never commit API keys, R2 credentials, the feed token, or the unguessable feed
  path. They live in GitHub repository secrets and a gitignored local `.env`.
- Never default a secret to a literal in code. `src/config.py` resolves them from
  the environment at call time via `_require()`, which raises
  `MissingCredential`. Keep it that way.
- Generated episodes, `out/`, and `feed*.xml` are gitignored. Don't add them.
- When adding a new secret, update three places: `.env.example` (with an empty
  value), the secrets table in `README.md`, and the workflow `env:` block.

---

## Architecture rules

These are load-bearing. Breaking one is a design change, not a refactor — flag it.

- **One module per stage in `src/`, orchestrated by `main.py`.** A stage does its
  own job and returns data; it doesn't reach into another stage's concerns.
- **All tunables live in `src/config.py`** — voices, word targets, model IDs,
  thresholds. If you find yourself typing a magic number into a stage module,
  it belongs in config instead.
- **Keep TTS behind the `TTSProvider` protocol** in `src/tts.py`. Swapping
  providers must remain a one-file change. ElevenLabs is the documented fallback.
- **`--edition` is a parameter everywhere.** `tech` is the only slug in
  production, but `ai`, `webdev`, and `infosec` must keep working without a code
  change.
- **Parse structurally, never by CSS class.** TLDR's class names change; its
  document structure is stabler. Both heading/link nestings
  (`<a><h3>…</h3></a>` and `<h3><a>…</a></h3>`) are supported because the live
  page has used both.
- **The feed is rebuilt from the full object listing, never appended to.**
  Rebuilding is idempotent and self-healing.
- **Delivery forks after the MP3 exists, and only there.** R2 publishing
  (`src/publish.py`) and email (`src/email_delivery.py`) are siblings. Everything
  upstream of them is shared and must stay delivery-agnostic — no `if args.email`
  reaching back into fetch, parse, enrich, script, or audio.
- **Guards run before work, not after.** The freshness check and the idempotency
  HEAD are what make three overlapping cron triggers safe.

---

## Testing

```sh
pytest -q          # 132 passing as of the last commit
```

- `tests/test_parse.py` is the highest-value test in the project. It runs against
  `tests/fixtures/tldr-2026-08-21.html`, a real captured edition. If you touch
  the parser, this is the test that has to convince you.
- Network, R2, OpenRouter, Gemini, and SMTP are all faked in tests. **Don't add a
  test that makes a live API call or sends a real message.** If you need new
  behavior from a provider, extend the fake.
- When a live run teaches you something about the real world — a new page shape,
  a new paywall marker, a provider quirk — capture it as a fixture or a config
  constant, not as a comment.

---

## Working style

- **Verify anything external before trusting a document — including this one.**
  TLDR's page structure, Gemini TTS model IDs and voice names, and API pricing
  all drift. The README marks each place this matters.
- **Prefer an environment override to editing a pinned value.** `TTS_MODEL`,
  `TTS_VOICE_A`, `TTS_VOICE_B`, and `SCRIPT_MODEL` exist so that re-checking a
  churning external ID stays cheap.
- **Run the cheap stages first.** `--stage parse` and `--stage enrich` need no
  credentials and cost nothing. Get those right before spending tokens.
- **Read the snapshot when parsing breaks.** Every run writes the raw HTML to
  `out/` and to `snapshots/YYYY-MM-DD.html` in R2, and the workflow uploads it on
  `always()`. It's the input that broke the parser.
- **Prompt changes in `src/script.py` are product changes.** The script is what
  determines whether the podcast is worth listening to. Don't tune it casually,
  and don't weaken the grounding and hedging instructions — unenriched items are
  exactly where hallucination shows up.
- Stop at a natural checkpoint and show your work rather than chaining
  speculative changes across stages.

---

## Commands

```sh
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt           # ffmpeg must also be on PATH
pytest -q

python main.py --stage parse              # free, no credentials
python main.py --stage enrich             # free, no credentials
python main.py --stage script             # OPENROUTER_API_KEY
python main.py --stage audio --no-upload  # GEMINI_API_KEY + ffmpeg, MP3 to ./out
python main.py --email                    # full run, emailed instead of published
python main.py --date YYYY-MM-DD          # re-run a past edition
```

Full flag reference is in the [Usage](README.md#usage) section of the README.

---

## Open work

Recently landed and not yet finished — check the current state of these before
building on top of them.

- **The email path has been delivered live; the R2 path has not.** R2 has never
  received an upload and is tested against fakes only.
- **The email path's idempotency is CI-side, not in the code.** `--email` skips
  R2, so the HEAD-against-the-bucket check isn't available. The guard is the
  `--email-marker` file cached under `emailed-<edition>-<date>` in
  `.github/workflows/daily.yml`. Run the pipeline with `--email` outside that
  workflow and nothing stops a repeat send.
- **`ENABLE_R2_PUBLISH` selects the CI path** — `true` publishes to R2, anything
  else emails. SMTP connection settings are repository *variables*; credentials
  and addresses are *secrets*. Keep that split when touching the workflow.
