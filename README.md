# tldr-daily-podcast

Every weekday morning, a ~10-minute two-host audio briefing of that day's
[TLDR tech newsletter](https://tldr.tech/tech) lands in a private podcast feed.
Content is built from the **linked articles**, not just TLDR's blurbs.

**Status:** greenfield. See [`docs/HANDOFF.md`](docs/HANDOFF.md) for the full spec,
key decisions, failure-handling matrix, and the M1–M6 build order.

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

## Stack

| Piece | Choice |
|---|---|
| Source | `https://tldr.tech/api/latest/tech` (302s to the day's edition) |
| Extraction | Trafilatura |
| Script | Claude API (`claude-sonnet-5`) |
| Voicing | Gemini multi-speaker TTS (2 speakers, chunked per segment) |
| Orchestration | GitHub Actions, three morning triggers + idempotency guard |
| Delivery | Cloudflare R2 + private RSS feed at an unguessable path |

## Local setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
# ffmpeg required on PATH
python main.py --date 2026-08-20
```

## Secrets

Set as GitHub repository secrets (and in a local, gitignored `.env` for development):

`ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`,
`R2_SECRET_ACCESS_KEY`, `R2_BUCKET`, `FEED_TOKEN`, `ALERT_WEBHOOK_URL`

Never commit values, `.env`, generated episodes, or the feed path.
