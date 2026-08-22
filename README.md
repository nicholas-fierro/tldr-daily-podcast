# tldr-daily-podcast

Every weekday morning, a ~10-minute two-host audio briefing of that day's
[TLDR tech newsletter](https://tldr.tech/tech) lands in a private podcast feed.
Content is built from the **linked articles**, not just TLDR's blurbs.

Full spec, decisions, and build order: [`docs/HANDOFF.md`](docs/HANDOFF.md).

## Status

The whole pipeline is written. M1 and M2 were verified against a live TLDR
edition; stages needing API keys or publication remain written-but-unrun.

| Milestone | Code | Verified |
|---|---|---|
| M1 Parser | done | **verified live** — 14 items parsed, 3 sponsors dropped, real fixture committed |
| M2 Enrichment | done | **verified live** — 12/14 enriched (85.7%); 2 clean blurb fallbacks, 0 items lost |
| M3 Script | done | **verified by owner** — grounded script approved in voiced episode |
| M4 Audio | done | **verified live and approved** — 7/7 segments rendered, episode heard end-to-end |
| M5 Publish | done | feed generation and retention tested; **never uploaded to R2** |
| M6 Automate | done | workflows parse; **never run** |

**M1 checkpoint passed on 2026-08-21.** The live page used anchors wrapping
headings rather than headings containing anchors, so the parser was fixed to
support both shapes. `tests/fixtures/tldr-2026-08-21.html` is the captured real
edition; all 14 editorial items have blurbs, and all 3 `(Sponsor)` slots plus
the `mailto:` TLDR hiring item are excluded.

**M2 checkpoint passed on 2026-08-22.** Article extraction succeeded for 12 of
14 items (85.7%). WSJ returned HTTP 401, and a Register page extracted a related-
links listing instead of article text; both fell back to their TLDR blurbs with
`enriched: false`. No item was dropped.

**M3 checkpoint passed on 2026-08-22.** DeepSeek V3.2 produced a grounded
1,142-word script across 7 segments for $0.007748. The owner approved the script
after hearing the voiced episode end-to-end.

**M4 checkpoint passed on 2026-08-22.** Gemini rendered all 7 segments and
ffmpeg produced a 3.44 MB mono 64 kbps MP3. The 7:10 runtime is below the nominal
8-12 minute target, but the owner approved the pacing and final audio. Future
scripts should still aim for 1,350-1,450 words rather than speeding up playback.

## Running it

Python 3.11+ is required.

```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt          # ffmpeg must also be on PATH

python main.py --stage parse             # M1: items as JSON, no credentials needed
python main.py --stage enrich            # M2: adds article text + success rate
python main.py --stage script            # M3: needs OPENROUTER_API_KEY
python main.py --stage audio --no-upload # M4: needs GEMINI_API_KEY + ffmpeg
python main.py                           # M5/M6: full run, needs R2
```

Useful flags: `--date YYYY-MM-DD` re-runs a past edition, `--force` bypasses the
freshness and idempotency guards, `--no-upload` skips R2 entirely, `--edition ai`
switches newsletters.

```bash
pytest -q                                # 119 passing
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
development. The app loads `.env` automatically; exported environment variables
win.

| Secret | Needed for | Source |
|---|---|---|
| `OPENROUTER_API_KEY` | M3 | openrouter.ai/keys |
| `GEMINI_API_KEY` | M4 | aistudio.google.com |
| `R2_ACCOUNT_ID` | M5 | Cloudflare dashboard |
| `R2_ACCESS_KEY_ID` | M5 | Cloudflare R2 API token |
| `R2_SECRET_ACCESS_KEY` | M5 | Cloudflare R2 API token |
| `R2_BUCKET` | M5 | your bucket name |
| `R2_PUBLIC_BASE_URL` | M5 | the bucket's public hostname |
| `FEED_TOKEN` | M5 | `openssl rand -hex 16` — self-generated |
| `ALERT_WEBHOOK_URL` | M6 | Slack / Discord / ntfy |

Script generation defaults to `deepseek/deepseek-v3.2` through
OpenRouter. Override it with `SCRIPT_MODEL`; structured JSON output is required.

`R2_PUBLIC_BASE_URL` is not in the handoff's list. The RSS `<enclosure>` needs an
absolute, publicly reachable URL for each MP3, and that hostname cannot be
derived from the S3 API endpoint — so it has to be supplied.

`FEED_TOKEN` is the random hex in the feed's unguessable path
(`feed-<token>.xml`). Changing it changes the feed URL and silently breaks any
podcast app already subscribed. Set it once and leave it.

M1 and M2 need no credentials at all.

## Known gaps

- `src/config.py` pins a Gemini TTS model ID that was not verifiable at build
  time. Model IDs churn; check current docs and override with `TTS_MODEL`
  rather than editing, so the check stays cheap.
- Voice names (`Kore`, `Puck`) are likewise unverified — override with
  `TTS_VOICE_A` / `TTS_VOICE_B`.
- The M3 script has not been voiced or heard. Its word-count gate is deliberately
  soft because ffprobe duration at M4 is the real 8-12 minute requirement.
