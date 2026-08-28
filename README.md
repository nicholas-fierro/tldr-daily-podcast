<a id="readme-top"></a>

<!-- PROJECT SHIELDS -->
[![Daily Episode Workflow][workflow-shield]][workflow-url]
[![Python 3.11+][python-shield]][python-url]
[![Tests: 145 passing][tests-shield]][tests-url]

<!-- PROJECT LOGO -->
<br />
<div align="center">
  <h1 align="center">TLDR Daily Podcast</h1>

  <p align="center">
    Two-host audio briefings for new TLDR Tech, AI, Web Dev, and InfoSec editions —
    built from the <em>linked articles</em>, not the blurbs.
    <br />
    <a href="#how-it-works"><strong>How it works »</strong></a>
    <br />
    <br />
    <a href="#getting-started">Getting Started</a>
    &middot;
    <a href="#usage">Usage</a>
    &middot;
    <a href="#for-coding-agents">For Coding Agents</a>
  </p>
</div>

<!-- TABLE OF CONTENTS -->
<details>
  <summary>Table of Contents</summary>
  <ol>
    <li>
      <a href="#about-the-project">About The Project</a>
      <ul>
        <li><a href="#built-with">Built With</a></li>
        <li><a href="#how-it-works">How It Works</a></li>
      </ul>
    </li>
    <li>
      <a href="#getting-started">Getting Started</a>
      <ul>
        <li><a href="#prerequisites">Prerequisites</a></li>
        <li><a href="#installation">Installation</a></li>
        <li><a href="#secrets">Secrets</a></li>
      </ul>
    </li>
    <li>
      <a href="#usage">Usage</a>
      <ul>
        <li><a href="#stage-by-stage">Stage by Stage</a></li>
        <li><a href="#delivery">Delivery</a></li>
        <li><a href="#flags">Flags</a></li>
        <li><a href="#scheduling">Scheduling</a></li>
      </ul>
    </li>
    <li><a href="#design-decisions">Design Decisions</a></li>
    <li><a href="#failure-handling">Failure Handling</a></li>
    <li><a href="#status">Status</a></li>
    <li><a href="#roadmap">Roadmap</a></li>
    <li><a href="#for-coding-agents">For Coding Agents</a></li>
    <li><a href="#license">License</a></li>
    <li><a href="#contact">Contact</a></li>
    <li><a href="#acknowledgments">Acknowledgments</a></li>
  </ol>
</details>

<!-- ABOUT THE PROJECT -->
## About The Project

[TLDR](https://tldr.tech/tech) publishes Tech, AI, Web Dev, and InfoSec newsletters
on their own schedules. Reading them takes time you don't always have; listening
does not.

This repository is the automation that turns each new edition into a podcast episode.
It scrapes the day's edition, follows every link and reads the **actual articles**,
has an LLM write a two-host dialogue grounded in that text, voices it with
multi-speaker TTS, and delivers the finished MP3.

Two properties matter more than anything else here:

* **Grounded, not confabulated.** The script is written from extracted article
  text. Where extraction failed, the item is marked `enriched: false` and the
  hosts are instructed to hedge audibly rather than invent detail.
* **Never ship a broken episode.** A missing episode is a far better outcome
  than silence, a truncated file, or a read-out ad. See
  [Failure Handling](#failure-handling).

<p align="right">(<a href="#readme-top">back to top</a>)</p>

### Built With

* [![Python][python-badge]][python-url] 3.11+
* [httpx](https://www.python-httpx.org/) + [BeautifulSoup](https://www.crummy.com/software/BeautifulSoup/) — fetch and structural parse
* [Trafilatura](https://trafilatura.readthedocs.io/) — readable-text extraction from linked articles
* [OpenRouter](https://openrouter.ai/) (`deepseek/deepseek-v3.2`) — script generation with JSON Schema output
* [Google Gemini TTS](https://ai.google.dev/gemini-api/docs/speech-generation) — multi-speaker voicing
* [ffmpeg](https://ffmpeg.org/) — concat, loudness normalization, MP3 encode, ID3 tags
* [Cloudflare R2](https://developers.cloudflare.com/r2/) + RSS 2.0 — storage and private feed delivery
* [GitHub Actions](https://docs.github.com/actions) — scheduled orchestration

<p align="right">(<a href="#readme-top">back to top</a>)</p>

### How It Works

```
cron (3× weekday mornings, UTC × 4 editions)
 └─ resolve    /api/latest/<edition> → actual edition date → delivery-marker guard
     └─ fetch      raw HTML + edition date from the redirect
         └─ guard  outside lookback? → exit 0. already delivered? → exit 0.
         └─ parse   → [{section, title, url, blurb, read_time}], sponsors dropped
             └─ dedup    → suppress URLs seen in the last 3 days
                 └─ enrich   → concurrent article fetch + Trafilatura text (best-effort)
                     └─ script   → one LLM call → 5-8 segments of two-host dialogue
                         └─ tts      → one request per segment → 24kHz PCM
                             └─ audio    → 350ms gaps, loudnorm, mono 64kbps MP3, ID3
                                 └─ deliver  → R2 upload + RSS rebuild
                                     ── or ─→ email the MP3 as an attachment (--email)
```

**Delivery forks after the MP3 exists.** Everything above `deliver` is shared by
both paths. `--email` sends the finished file as an attachment over SMTP and
skips R2 entirely; the default path uploads to R2 and rebuilds the feed. The two
are mutually exclusive, as is `--no-upload`, which does neither.

One module per stage in `src/`, orchestrated by `main.py`:

| Module | Responsibility |
|---|---|
| `src/fetch.py` | Hit `/api/latest/<edition>`, follow the redirect, return HTML + date |
| `src/parse.py` | HTML → items. **Sponsor filtering lives here.** |
| `src/enrich.py` | Concurrent article fetch, readable-text extraction, paywall detection |
| `src/script.py` | Items → OpenRouter → validated segmented dialogue JSON |
| `src/tts.py` | Segments → per-segment audio, with retry, behind a `TTSProvider` protocol |
| `src/audio.py` | ffmpeg concat, silence padding, loudnorm, MP3, ID3 tags |
| `src/publish.py` | R2 upload, feed rebuild, retention pruning |
| `src/email_delivery.py` | The alternative delivery path: MP3 as an SMTP attachment |
| `src/state.py` | Freshness guard, idempotency check, dedup window |
| `src/config.py` | Every tunable: voices, word targets, model IDs, thresholds |

**All tunables live in `src/config.py`.** Voices, word targets, model IDs, and
thresholds are there, not scattered through the modules. Secrets come from the
environment and are never defaulted to a literal.

<p align="right">(<a href="#readme-top">back to top</a>)</p>

<!-- GETTING STARTED -->
## Getting Started

### Prerequisites

* Python 3.11 or newer
* `ffmpeg` and `ffprobe` on `PATH`

  ```sh
  brew install ffmpeg          # macOS
  sudo apt-get install ffmpeg  # Debian/Ubuntu
  ```

### Installation

1. Clone the repo
   ```sh
   git clone git@github.com:nicholas-fierro/tldr-daily-podcast.git
   cd tldr-daily-podcast
   ```
2. Create a virtualenv and install dependencies
   ```sh
   python3.11 -m venv .venv && source .venv/bin/activate
   pip install -r requirements-dev.txt
   ```
3. Copy the environment template and fill in what the stage you're running needs
   ```sh
   cp .env.example .env
   ```
4. Confirm the suite passes
   ```sh
   pytest -q
   ```
5. Run the first stage — it needs no credentials at all
   ```sh
   python main.py --stage parse
   ```

### Secrets

`.env` is gitignored and loaded automatically; exported environment variables
win over it. In CI these are GitHub repository secrets.

| Variable | Needed by | Where it comes from |
|---|---|---|
| `OPENROUTER_API_KEY` | script | [openrouter.ai/keys](https://openrouter.ai/keys) |
| `GEMINI_API_KEY` | tts | [aistudio.google.com](https://aistudio.google.com/) |
| `R2_ACCOUNT_ID` | publish | Cloudflare dashboard |
| `R2_ACCESS_KEY_ID` | publish | Cloudflare R2 API token |
| `R2_SECRET_ACCESS_KEY` | publish | Cloudflare R2 API token |
| `R2_BUCKET` | publish | Your bucket name |
| `R2_PUBLIC_BASE_URL` | publish | The bucket's public hostname |
| `FEED_TOKEN` | publish | `openssl rand -hex 16` — self-generated |
| `SMTP_HOST` | `--email` | Your mail provider |
| `SMTP_USERNAME` | `--email` | Your mail provider |
| `SMTP_PASSWORD` | `--email` | An app password, not your account password |
| `EMAIL_TO` | `--email` | Recipient. Comma-separated for several. |
| `ALERT_WEBHOOK_URL` | CI alerting | Slack / Discord / ntfy |

Optional overrides: `SCRIPT_MODEL`, `SCRIPT_PROVIDER`, `TTS_MODEL`,
`TTS_VOICE_A`, `TTS_VOICE_B`, `OPENROUTER_BASE_URL`, `SMTP_PORT` (default
`465`), `SMTP_USE_SSL` (default `true`; `false` uses STARTTLS), `EMAIL_FROM`
(defaults to `SMTP_USERNAME`).

R2 and SMTP are alternatives, not both — you only need the credentials for the
delivery path you actually use.

> [!IMPORTANT]
> **This repository is public.** Never commit API keys, R2 credentials, the feed
> token, or the unguessable feed path. Generated episodes and `feed*.xml` are
> gitignored; keep it that way.

> [!WARNING]
> `R2_PUBLIC_BASE_URL` cannot be derived from the S3 API endpoint — the RSS
> `<enclosure>` needs an absolute, publicly reachable URL per MP3, so it must be
> supplied separately.
>
> `FEED_TOKEN` is the random hex in the feed's unguessable path
> (`feed-<token>.xml`). Changing it changes the feed URL and **silently breaks
> any podcast app already subscribed.** Set it once and leave it.

<p align="right">(<a href="#readme-top">back to top</a>)</p>

<!-- USAGE -->
## Usage

```sh
python main.py                      # today's edition, full pipeline
python main.py --date 2026-08-20    # re-run a past edition
```

### Stage by Stage

`--stage` stops after the named step and prints its output as JSON. Each stage
is cumulative — later stages need everything before them.

```sh
python main.py --stage parse              # no credentials needed
python main.py --stage enrich             # no credentials; adds article text + success rate
python main.py --stage script             # needs OPENROUTER_API_KEY
python main.py --stage audio --no-upload  # needs GEMINI_API_KEY + ffmpeg; MP3 to ./out
python main.py --stage publish            # full run; needs R2
python main.py --email                    # full run, emailed instead; needs SMTP
```

### Delivery

`--no-upload`, `--email`, and the default R2 path are mutually exclusive.

| Mode | Result |
|---|---|
| *(default)* | Upload to R2, rebuild the RSS feed, prune to 30 episodes |
| `--email` | Send the MP3 as an SMTP attachment. No R2 access at all. |
| `--no-upload` | Write the MP3 to `--local` and stop. Neither R2 nor email. |

`--email-marker <path>` writes the edition slug and actual edition date, and only
after the send succeeds. That marker is how the email path gets its idempotency:
CI resolves the newsletter's actual date before setup, then caches the marker as
`emailed-<edition>-<actual-date>`. This supports newsletters with different
publication schedules and skips delivered editions before dependency setup.
See [Scheduling](#scheduling).

### Flags

| Flag | Effect |
|---|---|
| `--date YYYY-MM-DD` | Re-run a past edition. Also bypasses the recency guard. |
| `--resolved-date YYYY-MM-DD` | Pin CI's resolved latest edition while retaining the recency guard. |
| `--edition <slug>` | `tech` (default), `ai`, `webdev`, `infosec`. |
| `--stage <name>` | Stop after `fetch`/`parse`/`enrich`/`script`/`audio`/`publish`. |
| `--local <dir>` | Working directory for intermediate files. Defaults to `out`. |
| `--no-upload` | Skip all R2 access. Implies no idempotency check and no dedup. |
| `--email` | Email the MP3 instead of publishing to R2. Also skips dedup and idempotency. |
| `--email-marker <path>` | Write `<edition>:<actual-date>` only after a successful send. |
| `--force` | Ignore both the recency guard and the idempotency check. |
| `-v`, `--verbose` | Debug logging. |

Exit codes: `0` success or a deliberate no-op, `1` a stage failed, `2` a required
credential was missing.

### Scheduling

`.github/workflows/daily.yml` fires at 11:15, 12:15, and 13:15 UTC on weekdays.
Each scheduled trigger fans out across `tech`, `ai`, `webdev`, and `infosec`;
`workflow_dispatch` runs only its selected edition and accepts optional date and
stage inputs.

Three triggers rather than one because **GitHub Actions cron is UTC-only, does
not follow DST, and is not punctual**. GitHub can still drop all scheduled runs;
the independent fallback design is tracked in [`EXTERNAL_SCHEDULER.md`](EXTERNAL_SCHEDULER.md).

Guards make delayed and repeated runs safe:

* **Actual-edition resolver** — before checkout or dependency setup, CI follows
  `/api/latest/<edition>` and extracts the newsletter's real publication date.
* **Recency guard** — an undelivered latest edition may be up to three days old,
  which recovers delayed runs and supports newsletters with different cadences.
* **Idempotency, R2 path** — the first publishing action is a HEAD against
  `episodes/<edition>/<actual-date>.mp3`. Existing object means exit 0.
* **Idempotency, email path** — CI restores
  `emailed-<edition>-<actual-date>` before checkout. A hit skips setup and
  generation entirely.

Concurrency is scoped by edition. Duplicate runs for one newsletter serialize;
different newsletters may generate in parallel.

**Which path CI takes is controlled by the `ENABLE_R2_PUBLISH` repository
variable.** Set it to `true` and the workflow publishes to R2; anything else,
including unset, and it emails. Note the split: SMTP *connection* settings are
repository **variables** (`SMTP_HOST`, `SMTP_PORT`, `SMTP_USE_SSL`), while
credentials and addresses are **secrets** (`SMTP_USERNAME`, `SMTP_PASSWORD`,
`EMAIL_FROM`, `EMAIL_TO`).

Run artifacts — the HTML snapshot and the script JSON, never the MP3 — upload on
`always()`. Especially on failure, since the snapshot is what you read when
parsing breaks.

<p align="right">(<a href="#readme-top">back to top</a>)</p>

<!-- DESIGN DECISIONS -->
## Design Decisions

These are settled. Don't re-litigate them without hitting a genuine wall — and
say so if you do.

| Decision | Choice | Why |
|---|---|---|
| Content source | `tldr.tech/api/latest/<edition>` (public web) | No Gmail OAuth, no IMAP, no email parsing. Cleaner HTML than the email, and the redirect removes all date math. |
| Audio generation | Gemini multi-speaker TTS | NotebookLM has no public consumer API; its programmatic path is enterprise-only. Browser-automation wrappers are fragile and were explicitly rejected. |
| Script generation | OpenRouter, `deepseek/deepseek-v3.2` | Cheap open-weight inference that runs from CI while preserving control over length, structure, tone, and structured output. |
| Orchestration | GitHub Actions | Free, built-in cron, secrets, logs, artifacts. No server to patch. |
| Storage + delivery | Cloudflare R2 + private RSS | Effectively free at this volume, S3-compatible, no egress fees, any podcast app can subscribe. |
| Workflow tools (n8n etc.) | **Not** for the core pipeline | Article fetching, retry logic, chunked TTS, and audio concat are code-shaped, not node-shaped. |
| Paywall handling | Blurb fallback, never headless Chrome | Not worth the maintenance or the ToS exposure. |

Consequences worth knowing before you change something:

* **Parse structurally, never by CSS class.** TLDR's Tailwind-ish class names
  change. Both observed heading/link nestings (`<a><h3>…</h3></a>` and
  `<h3><a>…</a></h3>`) are supported, because the live page has used both.
* **Sponsor filtering is aggressive and non-negotiable.** Items are dropped on
  `(Sponsor)` in the link text, an `advertise.tldr.tech` or `jobs.ashbyhq.com`
  host, or a section heading containing "Sponsor". Typically 2-3 slots per
  edition, one of them disguised in Quick Links. Missing them means the podcast
  reads out ads.
* **Expect 20-35% enrichment failure.** Bloomberg, WSJ, and JS-only sites fail
  or return paywall stubs. Extracted text under ~400 chars or containing a
  paywall marker phrase counts as a failure. Failures fall back to the TLDR
  blurb with `enriched: false`. **No item is ever dropped, and a failed fetch
  never fails the run.**
* **One TTS request per segment, never one per episode.** Gemini's own guidance
  is that quality drifts past a few minutes. Segments are 60-120s, retried up to
  3× with backoff, then dropped individually if they still fail.
* **Word count is a proxy, duration is the gate.** Target 1,350-1,450 words;
  accept 1,200-1,600 with a warning; hard-fail below 1,100 or above 1,700. The
  real requirement is the 8-12 minute ffprobe duration. Never fix duration by
  speeding up playback.
* **The feed is rebuilt, not appended.** Regenerating from the full object
  listing is idempotent and self-healing.
* **The TTS layer sits behind a `TTSProvider` protocol** so swapping providers
  is a one-file change. ElevenLabs is the documented fallback if Gemini voice
  quality disappoints.
* **`--edition` is part of every persistent identity.** Scheduled runs cover
  `tech`, `ai`, `webdev`, and `infosec`; object keys, markers, filenames, feed
  GUIDs, dedup state, titles, and prompts include the edition.

<p align="right">(<a href="#readme-top">back to top</a>)</p>

<!-- FAILURE HANDLING -->
## Failure Handling

| Failure | Behavior |
|---|---|
| TLDR page 404/500 | Retry 3× with backoff, then exit non-zero (alerts) |
| Parser returns 0 items | **Hard fail + alert** — the page structure changed |
| Parser returns <5 items | Warn + alert, continue |
| Individual article fetch fails | Fall back to blurb, mark `enriched: false`, continue |
| OpenRouter fails | Retry 2×, then hard fail + alert |
| A TTS segment fails 3× | Drop that segment, continue, log a warning |
| >30% of TTS segments fail | **Hard fail** — don't ship a mangled episode |
| Upload fails | Retry, then hard fail + alert |
| SMTP send fails | `EmailDeliveryError`, exit 1. The marker file is not written. |

**Never publish silence, a truncated file, or an ad read.**

Every run snapshots the raw HTML. When parsing breaks, you want the input that
broke it — it's in the workflow artifacts and at
`snapshots/<edition>/YYYY-MM-DD.html` in R2.

<p align="right">(<a href="#readme-top">back to top</a>)</p>

<!-- STATUS -->
## Status

The full pipeline is written and `pytest` is green at 145 tests. What has and
hasn't been exercised against live services:

| Stage | Live verification |
|---|---|
| Parse | **Verified live** (2026-08-21) — 14 items parsed, 3 sponsors dropped, real edition committed as a fixture |
| Enrich | **Verified live** (2026-08-22) — 12/14 enriched (85.7%); 2 clean blurb fallbacks, 0 items lost |
| Script | **Verified and owner-approved** (2026-08-22) — grounded 1,142-word script across 7 segments for $0.0077 |
| Audio | **Verified and owner-approved** (2026-08-22) — 7/7 segments rendered, 3.44 MB mono 64kbps MP3, heard end-to-end |
| Publish (R2) | Feed generation and retention tested in isolation. **Never uploaded to R2.** |
| Deliver (email) | **Verified live** (2026-08-23) — episode delivered end-to-end as an SMTP attachment |
| Automate | **Verified live** (2026-08-27) — manual and scheduled runs exercised; GitHub later delayed/dropped cron events, motivating the external fallback design. |

Notes from those runs, worth carrying forward:

* The live page used anchors wrapping headings rather than headings containing
  anchors. The parser was fixed to support both shapes;
  `tests/fixtures/tldr-2026-08-21.html` is the captured real edition and
  `tests/test_parse.py` is the highest-value test in the project.
* Enrichment failures were a WSJ HTTP 401 and a Register page that extracted a
  related-links listing. Both degraded cleanly to blurbs.
* The approved episode ran 7:10 — below the nominal 8-12 minute target. The
  owner approved the pacing, but **future scripts should hit 1,350-1,450 words
  rather than compensating with playback speed.**

<p align="right">(<a href="#readme-top">back to top</a>)</p>

<!-- ROADMAP -->
## Roadmap

- [x] **Email delivery branch** — `--email` sends the finished MP3 as an SMTP
      attachment instead of publishing to R2
- [x] **Gemini TTS model ID and voice names verified** against a live
      `models.list` call and a full 7/7 segment render
- [ ] First live R2 upload and a feed that validates in a real podcast app
- [ ] A full unattended week on the GitHub Actions schedule

Explicitly **not** in scope: chapter markers, transcripts in the feed, per-item
deep links beyond the description list, any web UI.

### Known gaps

* `src/config.py` pins `gemini-2.5-flash-preview-tts` and voices `Kore` / `Algenib`.
  `Kore` and prior default `Puck` were rendered successfully on 2026-08-22.
  `Algenib` is listed in Gemini's current voice library but has not yet been rendered
  by this project. **Model IDs still churn,
  and this one is a `-preview-` ID**, so re-check before assuming it works, and
  override with `TTS_MODEL` / `TTS_VOICE_A` / `TTS_VOICE_B` rather than editing
  the file.
* API pricing drifts. Verify it rather than assuming; per-run token and character
  counts are logged so actual spend is visible.
* There is no `LICENSE` file in the repository. See [License](#license).

<p align="right">(<a href="#readme-top">back to top</a>)</p>

<!-- FOR CODING AGENTS -->
## For Coding Agents

Read [`AGENTS.md`](AGENTS.md). It is the working contract for agents in this
repo — this README is the reference for *what the system does*, `AGENTS.md` is
the reference for *how to change it*.

<p align="right">(<a href="#readme-top">back to top</a>)</p>

<!-- LICENSE -->
## License

No license has been declared. This is a personal project; absent a `LICENSE`
file, default copyright applies and no usage rights are granted. If you want it
to be reusable, add one.

<p align="right">(<a href="#readme-top">back to top</a>)</p>

<!-- CONTACT -->
## Contact

Nicholas Fierro — [@nicholas-fierro](https://github.com/nicholas-fierro)

Project Link: [https://github.com/nicholas-fierro/tldr-daily-podcast](https://github.com/nicholas-fierro/tldr-daily-podcast)

<p align="right">(<a href="#readme-top">back to top</a>)</p>

<!-- ACKNOWLEDGMENTS -->
## Acknowledgments

* [TLDR](https://tldr.tech/) — the newsletter this is built on
* [Trafilatura](https://trafilatura.readthedocs.io/) — the reason enrichment
  works as well as it does on news sites
* [Best-README-Template](https://github.com/othneildrew/Best-README-Template) —
  the structure of this document

<p align="right">(<a href="#readme-top">back to top</a>)</p>

<!-- MARKDOWN LINKS & IMAGES -->
[workflow-shield]: https://img.shields.io/github/actions/workflow/status/nicholas-fierro/tldr-daily-podcast/daily.yml?style=for-the-badge&label=daily%20episode
[workflow-url]: https://github.com/nicholas-fierro/tldr-daily-podcast/actions/workflows/daily.yml
[python-shield]: https://img.shields.io/badge/python-3.11%2B-blue?style=for-the-badge
[python-url]: https://www.python.org/
[tests-shield]: https://img.shields.io/badge/tests-145%20passing-brightgreen?style=for-the-badge
[tests-url]: https://github.com/nicholas-fierro/tldr-daily-podcast/tree/main/tests
[python-badge]: https://img.shields.io/badge/Python-3776AB?style=for-the-badge&logo=python&logoColor=white
