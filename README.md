# tldr-daily-podcast

Every weekday morning, a ~10-minute two-host audio briefing of that day's
[TLDR tech newsletter](https://tldr.tech/tech) lands in a private podcast feed.
Content is built from the **linked articles**, not just TLDR's blurbs.

Full spec, decisions, and build order: [`docs/HANDOFF.md`](docs/HANDOFF.md).

## Status

The whole pipeline is written. What has actually been *proven* is narrower —
the build environment had no route to `tldr.tech` and no ffmpeg, so the stages
that need a live page, a key, or an encoder are written-but-unrun.

| Milestone | Code | Verified |
|---|---|---|
| M1 Parser | done | against a **synthetic** fixture only — see below |
| M2 Enrichment | done | fallback path proven (0% network, 0 items lost); no real success rate measured |
| M3 Script | done | prompt + response parsing tested; **no episode has been read** |
| M4 Audio | done | PCM math and ffmpeg argv tested; **ffmpeg never invoked, nothing listened to** |
| M5 Publish | done | feed generation and retention tested; **never uploaded to R2** |
| M6 Automate | done | workflows parse; **never run** |

**The committed fixture is synthetic.** It is written to the structure
documented in the handoff, not captured from tldr.tech. It proves the parser
does what we intended; it does not prove our intent matches the real page.
Before trusting M1, run this from a machine with normal network access:

```bash
python scripts/capture_fixture.py
```

It writes `tests/fixtures/tldr-<date>.html` and prints the parsed items for you
to eyeball for ads. A real fixture also activates the currently-skipped test in
`tests/test_parse.py`. This is the single highest-value thing left to do.

## Running it

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt          # ffmpeg must also be on PATH

python main.py --stage parse             # M1: items as JSON, no credentials needed
python main.py --stage enrich            # M2: adds article text + success rate
python main.py --stage script            # M3: needs ANTHROPIC_API_KEY
python main.py --stage audio --no-upload # M4: needs GEMINI_API_KEY + ffmpeg
python main.py                           # M5/M6: full run, needs R2
```

Useful flags: `--date YYYY-MM-DD` re-runs a past edition, `--force` bypasses the
freshness and idempotency guards, `--no-upload` skips R2 entirely, `--edition ai`
switches newsletters.

```bash
pytest -q                                # 111 passing, 1 skipped
```

## Pipeline

```
cron
 └─> fetch /api/latest/tech          → raw HTML + edition date
      └─> parse                       → [{section, title, url, blurb, read_time}]
           └─> guard: already built?  → exit 0 if episode for this date exists in R2
                └─> enrich (parallel) → full article text per item (best-effort)
                     └─> write script → 2-host dialogue, ~1,400 words
                          └─> TTS     → per-segment WAV
                               └─> mux → single MP3 w/ ID3 tags
                                    └─> upload + regenerate RSS
```

One module per stage in `src/`, orchestrated by `main.py`. Tunables — voices,
word targets, model IDs, thresholds — are all in `src/config.py`.

## Secrets

Set as GitHub repository secrets, and in a gitignored local `.env` for
development (`set -a; source .env; set +a`).

| Secret | Needed for | Source |
|---|---|---|
| `ANTHROPIC_API_KEY` | M3 | console.anthropic.com |
| `GEMINI_API_KEY` | M4 | aistudio.google.com |
| `R2_ACCOUNT_ID` | M5 | Cloudflare dashboard |
| `R2_ACCESS_KEY_ID` | M5 | Cloudflare R2 API token |
| `R2_SECRET_ACCESS_KEY` | M5 | Cloudflare R2 API token |
| `R2_BUCKET` | M5 | your bucket name |
| `R2_PUBLIC_BASE_URL` | M5 | the bucket's public hostname |
| `FEED_TOKEN` | M5 | `openssl rand -hex 16` — self-generated |
| `ALERT_WEBHOOK_URL` | M6 | Slack / Discord / ntfy |

`R2_PUBLIC_BASE_URL` is not in the handoff's list. The RSS `<enclosure>` needs an
absolute, publicly reachable URL for each MP3, and that hostname cannot be
derived from the S3 API endpoint — so it has to be supplied.

`FEED_TOKEN` is the random hex in the feed's unguessable path
(`feed-<token>.xml`). Changing it changes the feed URL and silently breaks any
podcast app already subscribed. Set it once and leave it.

M1 and M2 need no credentials at all.

## Known gaps

- No real edition has ever been parsed. See the fixture note above.
- `src/config.py` pins a Gemini TTS model ID that was not verifiable at build
  time. Model IDs churn; check current docs and override with `TTS_MODEL`
  rather than editing, so the check stays cheap.
- Voice names (`Kore`, `Puck`) are likewise unverified — override with
  `TTS_VOICE_A` / `TTS_VOICE_B`.
- Nobody has listened to an episode, so the prompt has never been iterated on.
  Per the handoff, M3 is the step that decides whether this is worth listening
  to; the prompt as committed is a first draft, not a tuned one.
