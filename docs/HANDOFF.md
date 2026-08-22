# TLDR → Daily Podcast: Engineering Handoff

**Goal:** Every weekday morning, a ~10-minute two-host audio briefing of that day's TLDR tech newsletter appears in my podcast app. Content must be based on the *linked articles*, not just TLDR's one-paragraph blurbs.

**Status:** Greenfield. Nothing built. This doc is the spec + build plan.

---

## 1. Key decisions (already made — don't re-litigate unless you hit a wall)

| Decision | Choice | Why |
|---|---|---|
| Source of content | `https://tldr.tech/api/latest/tech` (public web) | No Gmail OAuth, no IMAP, no email parsing. Cleaner HTML than the email. Verified working. |
| Audio generation | Gemini multi-speaker TTS via API | NotebookLM has **no public consumer API**. Its programmatic audio-overview endpoint is enterprise-only (Gemini Enterprise / Discovery Engine). Browser-automation wrappers around the NotebookLM UI exist but are fragile and break on every UI change — explicitly rejected. |
| Script generation | OpenRouter (`deepseek/deepseek-v3.2`) | Cheap open-weight inference works from GitHub Actions while preserving control over length, structure, tone, and structured output. |
| Orchestration | GitHub Actions (scheduled workflow) | Free, built-in cron, built-in secrets, built-in logs and artifact retention, no server to patch. |
| Storage + delivery | Cloudflare R2 + private RSS feed | Effectively free at this volume, S3-compatible, no egress fees. Any podcast app can subscribe. |
| n8n | **Not** for the core pipeline | Article fetching, retry logic, chunked TTS, and audio concatenation are code-shaped, not node-shaped. Revisit only if a non-technical person needs to edit the flow. |

---

## 2. Source format (verified 2026-08-21)

`https://tldr.tech/api/latest/tech` **302-redirects** to `https://tldr.tech/tech/YYYY-MM-DD` for the most recent edition. Use the `/api/latest/tech` URL as the entry point — it removes all date math and the "has today's edition published yet?" guesswork. Capture the final redirect URL to learn the edition date.

Page structure (as of the verification date — **re-verify before writing selectors**):

- A page title of the form `TLDR YYYY-MM-DD`
- Section headers: `Big Tech & Startups`, `Science & Futuristic Technology`, `Programming, Design & Data Science`, `Miscellaneous`, `Quick Links`
- Each item is an `<article>` whose direct child anchor wraps an `<h3>` with text `Title (N minute read)` or `Title (GitHub Repo)`; a sibling `<div>` contains TLDR's summary
- Outbound links carry `?utm_source=tldrnewsletter`
- The captured 2026-08-21 edition had 14 editorial items, 3 `(Sponsor)` slots, and one `mailto:` TLDR hiring item

### Parsing requirements

1. **Parse structurally, not by CSS class.** Support both observed heading/link nestings (`<a><h3>…</h3></a>` and `<h3><a>…</a></h3>`) and collect the following summary sibling. TLDR's Tailwind-ish class names will change.
2. **Filter sponsored content aggressively.** Drop any item where:
   - the link text contains `(Sponsor)`
   - the URL host is `advertise.tldr.tech`
   - the URL host is `jobs.ashbyhq.com` (TLDR's own job ads appear inline as regular items)
   - the item appears under a heading containing "Sponsor"
   There are typically 2–3 sponsor slots per edition, including one disguised in Quick Links. Missing them means the podcast reads out ads.
3. **Normalize URLs.** Strip `utm_*` and `sp=` params before fetching and before dedup.
4. **Preserve section labels.** The script writer uses these to group topics.
5. **Snapshot the raw HTML** to R2 (or a workflow artifact) on every run. When parsing breaks, you want the input that broke it.
6. **Commit a fixture.** Save one real edition's HTML to `tests/fixtures/` and write a parser test against it. This is the single highest-value test in the project.

---

## 3. Pipeline

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

### 3.1 Enrichment (article fetching)

- Concurrency cap: 5. Per-request timeout: 15s. One retry on 5xx/timeout.
- Send a real, honest User-Agent identifying the project and a contact URL.
- Extract readable text with **Trafilatura** (Python) or **Readability + jsdom** (Node). Trafilatura is meaningfully better on news sites.
- Truncate each extracted article to ~6,000 characters. The lede and first few sections carry the substance; the tail is boilerplate.
- **Expect 20–35% failure.** Bloomberg, WSJ, and JS-only sites will fail or return paywall stubs. This is fine and expected.
- **Detect paywall stubs:** extracted text under ~400 chars, or containing subscribe/paywall marker phrases → treat as a failure.
- **On any failure, fall back to the TLDR blurb** and mark the item `enriched: false`. Never drop an item, and never let a failed fetch fail the run.
- GitHub repo links: fetch the README via the GitHub API rather than scraping the repo page.
- **Do not** add headless Chrome to beat paywalls. Not worth the maintenance or the ToS exposure. The blurb fallback is good enough.

### 3.2 Dedup

Persist the last 7 days of item URLs in R2 as a small JSON file. If a normalized URL appeared in the last 3 days, drop it — TLDR occasionally repeats a story across editions.

### 3.3 Script generation

One OpenRouter call. Pass every item as structured input: section, title, URL, blurb, full text (or blurb + `enriched: false`), read-time estimate. Require JSON Schema output and route only to providers supporting it.

**Prompt requirements:**

- Two named hosts with distinct, consistent roles — suggest one who frames and asks, one who explains and contextualizes. Give them fixed names and keep them constant across episodes (listeners anchor on this).
- **Target 1,350–1,450 words.** Accept 1,200–1,600 with a warning, and hard-fail only below 1,100 or above 1,700. Word count is a proxy; the real gate is the 8–12 minute ffprobe duration at M4.
- **Group by theme, not by TLDR's ordering.** "Three things happened in AI infrastructure today" beats reading a list in order.
- **Weight by substance, not by section.** A major acquisition gets 90 seconds; a Quick Link gets one sentence or gets cut. Explicitly authorize the model to omit weak items.
- **Mark low-confidence items.** Where `enriched: false`, the hosts should hedge naturally ("the summary suggests…") rather than confabulate detail. This is important — unenriched items are where hallucination will show up.
- Cold open with the day's single biggest story. No "welcome back to the show" throat-clearing.
- Output as structured segments, each tagged with a topic, so TTS can chunk cleanly:
  ```json
  {"segments": [{"topic": "ai-infra", "lines": [{"speaker": "Ava", "text": "..."}]}]}
  ```
- No sound-effect cues, no music cues, no `[laughs]` stage directions unless you've confirmed the TTS model honors the tag syntax.

Save the script JSON to R2 alongside the audio. You will want to read it when an episode sounds wrong, and it's the cheapest thing to inspect.

### 3.4 TTS

Gemini TTS supports native multi-speaker dialogue with **up to 2 speakers** in a single request — which is exactly the two-host format.

Model IDs have churned (`gemini-2.5-flash-tts`, `gemini-2.5-pro-tts`, `gemini-3.1-flash-tts-preview`, plus `-preview-` variants on the Gemini API vs. Cloud TTS paths). **Check the current docs at build time rather than trusting any ID in this document.** Start with a Flash-tier TTS model; escalate to Pro-tier if quality is short.

Two documented constraints that shape the design:

1. **Quality drifts on outputs longer than a few minutes.** Google's own guidance is to split transcripts into smaller chunks. So: **generate one request per topic segment (~60–120s each), then concatenate.** Do not attempt a single 10-minute request.
2. **The model occasionally returns text tokens instead of audio and 500s.** Retry with backoff, up to 3 attempts per segment. If a segment still fails, drop that segment and continue — a 9-minute episode is a fine outcome; a failed run is not.

Pin voice selections in config so the hosts sound the same every day. Prefix each request with the same style direction ("warm, conversational, tech-news podcast pace") so tone doesn't wander between segments.

**Concatenation:** stitch with ffmpeg, inserting ~350ms of silence between segments so topic transitions don't sound clipped. Normalize loudness to broadcast standard (`loudnorm` filter, target -16 LUFS mono) — otherwise volume varies noticeably between days.

**Verify duration** with ffprobe and log it. If episodes consistently land outside 8–12 minutes, adjust the word-count target in the prompt. Don't try to fix duration by speeding up audio.

### 3.5 Output and delivery

- Encode to MP3, mono, 64kbps — plenty for speech, keeps files ~5MB.
- Write ID3 tags: title = TLDR's own headline for the day, date, artist, album.
- Upload to R2 at `episodes/YYYY-MM-DD.mp3`.
- Regenerate `feed.xml` from the full object listing (don't append — rebuild, it's idempotent and self-healing).

**RSS requirements:**
- Valid RSS 2.0 with the iTunes namespace
- `<enclosure>` with correct `url`, `length` (byte size — apps misbehave if this is wrong), and `type="audio/mpeg"`
- Stable `<guid>` per episode
- Correct RFC-822 `<pubDate>`
- `<itunes:duration>` from ffprobe
- Episode description = the day's item titles as a plain list (this shows in the app's show notes — genuinely useful for tapping through to the source articles, so include the URLs)
- **Privacy:** serve the feed at an unguessable path (`feed-<32-random-hex>.xml`). It's a personal feed; don't publish an obviously-named one at the bucket root.

Keep the last 30 episodes; prune older ones in the same job.

---

## 4. Scheduling

TLDR lands roughly 6:00–7:00am ET on weekdays.

- GitHub Actions cron is **UTC only and does not follow DST.** Schedule at a fixed UTC time that's safely after publication year-round, and let the freshness guard handle the rest.
- **GitHub Actions scheduled runs are not punctual** — delays of 5 to 30+ minutes under load are normal and documented. Don't build anything that assumes exact firing.
- **Freshness guard:** after fetching, compare the edition date from the redirect URL against today's date in `America/New_York`. If it's stale, exit 0 without generating. Schedule the workflow to run **three times** across the morning (e.g. 11:15, 12:15, 13:15 UTC); the idempotency check means only the first run that sees a fresh edition does any work. This is more robust than one retry loop inside a single job.
- **Idempotency:** first real action of every run is a HEAD against `episodes/YYYY-MM-DD.mp3`. Exists → exit 0.
- Also add `workflow_dispatch` with an optional date input so you can re-run any past edition by hand. You will use this constantly during development.

---

## 5. Failure handling

| Failure | Behavior |
|---|---|
| TLDR page 404/500 | Retry 3× w/ backoff, then exit non-zero (alerts) |
| Parser returns 0 items | **Hard fail + alert.** This means the page structure changed. |
| Parser returns <5 items | Warn + alert, but continue |
| Individual article fetch fails | Fall back to blurb, log, continue |
| OpenRouter API fails | Retry 2×, then hard fail + alert |
| A TTS segment fails 3× | Drop the segment, continue, log a warning |
| >30% of TTS segments fail | Hard fail — don't ship a mangled episode |
| Upload fails | Retry, then hard fail + alert |

**Never publish silence, a truncated file, or an ad read.** A missing episode is a far better outcome than a broken one.

**Alerting:** simplest option is a GitHub Actions step on `failure()` that posts to a webhook (Slack/Discord/ntfy). Don't rely on GitHub's failure emails — they're easy to tune out.

---

## 6. Repo layout

```
.github/workflows/daily.yml
src/
  fetch.py        # get /api/latest/tech, follow redirect, return html + date
  parse.py        # html -> items[]; sponsor filtering lives here
  enrich.py       # concurrent article fetch + readable-text extraction
  script.py       # items -> OpenRouter -> segmented dialogue JSON
  tts.py          # segments -> per-segment audio, with retry
  audio.py        # ffmpeg concat, silence padding, loudnorm, mp3, tags
  publish.py      # R2 upload, feed rebuild, retention pruning
  state.py        # dedup window, idempotency checks
  config.py       # voices, word targets, model IDs, thresholds
tests/
  fixtures/tldr-2026-08-21.html
  test_parse.py   # sponsor filtering + item extraction
main.py
```

Python is the recommended stack (Trafilatura, `httpx`, `google-genai`, and `boto3` all clean). Node is acceptable if preferred; extraction quality is the only real tradeoff.

**Secrets** (GitHub repository secrets): `OPENROUTER_API_KEY`, `GEMINI_API_KEY` (or GCP service account JSON if using the Vertex/Cloud TTS path), `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET`, `FEED_TOKEN`, `ALERT_WEBHOOK_URL`.

Runner needs ffmpeg: `sudo apt-get install -y ffmpeg` or the `FedericoCarboni/setup-ffmpeg` action.

---

## 7. Build order (checkpoints — stop and show me at each)

**M1 — Parser.** Fetch and parse today's edition, print items as JSON. Sponsors filtered. Fixture test passing. *Checkpoint: the JSON is correct and contains zero ads.*

**M2 — Enrichment.** Add concurrent article fetching. Report the enrichment success rate. *Checkpoint: ≥65% of items enriched, failures degrade cleanly to blurbs.*

**M3 — Script.** Add OpenRouter script generation, output segmented JSON. *Checkpoint: I read the script and it's genuinely good — well-grouped, right weighting, no confabulation on unenriched items. Iterate on the prompt here until it is. This is the step that determines whether the whole thing is worth listening to; don't rush past it.*

**M4 — Audio.** Add chunked TTS, concat, normalize, MP3 out to local disk. *Checkpoint: I listen end-to-end. Duration in range, no clipping at seams, voices consistent.*

**M5 — Publish.** R2 upload + RSS feed. *Checkpoint: feed validates and the episode plays in a real podcast app.*

**M6 — Automate.** GitHub Actions workflow, three morning triggers, idempotency, alerting, retention. *Checkpoint: it runs unattended for a full week without intervention.*

---

## 8. Notes / open questions

- Cost should be small — pennies per month for OpenRouter script generation plus TTS — but **verify current API pricing during M3/M4 rather than assuming**, and log per-run token and character counts so we can see actual spend.
- If TTS voice quality disappoints at M4, ElevenLabs is the fallback. It's meaningfully more expensive but the voices are better. Keep the TTS module behind a clean interface so swapping providers is a one-file change.
- If I later want other TLDR editions (AI, Web Dev, InfoSec), the path is `/api/latest/<edition>` — the parser should take the edition slug as a parameter from day one, even though we only use `tech` now.
- Not in scope for v1: chapter markers, transcripts in the feed, per-item "read more" deep links beyond the description list, any web UI.
