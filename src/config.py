"""Tunables for the pipeline.

Everything a human might want to adjust lives here: voices, word targets,
model IDs, thresholds. Secrets come from the environment and are never
defaulted to a literal.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv(override=False)

# --- source ---------------------------------------------------------------

EDITION = "tech"  # slug; /api/latest/<edition>. Others: ai, webdev, infosec.
LATEST_URL = "https://tldr.tech/api/latest/{edition}"
EDITION_URL = "https://tldr.tech/{edition}/{date}"
EDITION_MAX_AGE_DAYS = 3

# The dated page path does not always match the API slug: /api/latest/webdev
# redirects to /dev/YYYY-MM-DD. Verified 2026-08-28.
EDITION_PAGE_SLUGS = {"webdev": "dev"}

# Human-facing names, used in the script prompt and the email coverage summary.
EDITION_NAMES = {
    "tech": "Tech",
    "ai": "AI",
    "webdev": "Web Dev",
    "fintech": "Fintech",
    "infosec": "InfoSec",
}

# One episode per bundle. The first entry is the anchor: when no date is given,
# its latest edition decides the target date every other source must match.
EDITION_BUNDLES = {
    "daily": ("tech", "ai", "webdev", "fintech"),
}

# Four editions yield 50-70 items — far more than a ten-minute episode can hold
# and more context than the script model needs. Selection is balanced across
# included sources so no edition is crowded out.
BUNDLE_ITEM_CAP = 28

# Two titles this similar (token Jaccard) are the same story told twice.
# Deliberately high: merging two distinct stories loses one entirely.
TITLE_SIMILARITY_THRESHOLD = 0.6

# Honest identification. Points at the repo, not a person.
USER_AGENT = (
    "tldr-daily-podcast/0.1 (personal podcast generator; "
    "+https://github.com/nicholas-fierro/tldr-daily-podcast)"
)

FETCH_RETRIES = 3
FETCH_TIMEOUT = 20.0

# --- enrichment -----------------------------------------------------------

ENRICH_CONCURRENCY = 5
ENRICH_TIMEOUT = 15.0
ENRICH_RETRIES = 1  # one retry, on 5xx/timeout only
ARTICLE_CHAR_LIMIT = 6_000

# Below this many characters, an "extraction" is a paywall stub or a nav bar.
PAYWALL_MIN_CHARS = 400

# Lowercased substrings that mark a paywall interstitial rather than an article.
PAYWALL_MARKERS = (
    "subscribe to continue",
    "subscribe to read",
    "already a subscriber",
    "become a subscriber",
    "create a free account",
    "sign in to read",
    "for full access",
    "this article is for subscribers",
    "enable javascript",
    "javascript is disabled",
    "are you a robot",
    "verify you are human",
    "access denied",
)

# Expected enrichment floor. Informational: we warn, we never fail on it.
ENRICH_TARGET_RATE = 0.65

# --- dedup ----------------------------------------------------------------

DEDUP_STATE_KEY = "state/{edition}/seen-urls.json"
DEDUP_RETAIN_DAYS = 7  # how much history we keep
DEDUP_WINDOW_DAYS = 3  # how far back a repeat suppresses an item

# --- script ---------------------------------------------------------------

SCRIPT_PROVIDER = os.environ.get("SCRIPT_PROVIDER", "openrouter").strip().lower()
SCRIPT_MODEL = os.environ.get(
    "SCRIPT_MODEL", "deepseek/deepseek-v3.2"
).strip()
SCRIPT_MAX_TOKENS = 3_600
SCRIPT_RETRIES = 2
SCRIPT_TIMEOUT = 180.0
SCRIPT_SEGMENT_MIN = 5
SCRIPT_SEGMENT_MAX = 8

OPENROUTER_BASE_URL = os.environ.get(
    "OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"
).rstrip("/")
OPENROUTER_REFERER = os.environ.get(
    "OPENROUTER_REFERER", "https://github.com/nicholas-fierro/tldr-daily-podcast"
).strip()
OPENROUTER_TITLE = os.environ.get("OPENROUTER_TITLE", "TLDR Daily Podcast").strip()

HOST_A = "Ava"  # frames, asks, drives the running order
HOST_B = "Ben"  # explains, contextualizes, supplies the numbers

WORD_TARGET_MIN = 1_350
WORD_TARGET_MAX = 1_450
WORD_ACCEPT_MIN = 1_200
WORD_ACCEPT_MAX = 1_600
WORD_HARD_MIN = 1_100
WORD_HARD_MAX = 1_700

# --- tts ------------------------------------------------------------------

# Model IDs churn. Verify against current docs before trusting this default;
# override with TTS_MODEL rather than editing, so the check stays cheap.
TTS_MODEL = os.environ.get("TTS_MODEL", "gemini-2.5-flash-preview-tts")

TTS_VOICE_A = os.environ.get("TTS_VOICE_A", "Kore")
TTS_VOICE_B = os.environ.get("TTS_VOICE_B", "Algenib")

# Prefixed to every request so tone does not wander between segments.
TTS_STYLE_DIRECTION = (
    "Read the following two-host podcast dialogue in a warm, conversational, "
    "tech-news podcast pace. Natural and engaged, not breathless, not newsreader-formal."
)

TTS_RETRIES = 3
TTS_BACKOFF_BASE = 2.0
# Above this share of failed segments the episode is not worth shipping.
TTS_MAX_SEGMENT_FAILURE_RATE = 0.30

TTS_SAMPLE_RATE = 24_000  # Gemini TTS returns 24kHz signed 16-bit mono PCM
TTS_SAMPLE_WIDTH = 2
TTS_CHANNELS = 1

# --- audio ----------------------------------------------------------------

SEGMENT_GAP_MS = 350
LOUDNORM_TARGET_LUFS = -16.0
MP3_BITRATE = "64k"

DURATION_MIN_S = 8 * 60
DURATION_MAX_S = 12 * 60

PODCAST_TITLE = "TLDR Daily"
PODCAST_AUTHOR = "TLDR Daily (generated)"
PODCAST_DESCRIPTION = (
    "Two-host briefings from TLDR newsletters, built from the linked articles "
    "rather than the summaries."
)
PODCAST_LANGUAGE = "en-us"

# --- publish --------------------------------------------------------------

EPISODE_KEY = "episodes/{edition}/{date}.mp3"
SCRIPT_KEY = "scripts/{edition}/{date}.json"
# Snapshots stay source-qualified even in a bundle: when parsing breaks, the
# input that broke it belongs to one source page, not to the combined episode.
SNAPSHOT_KEY = "snapshots/{edition}/{date}.html"
RETAIN_EPISODES = 30


@dataclass(frozen=True)
class R2Config:
    """Resolved from the environment at call time, never at import time."""

    account_id: str
    access_key_id: str
    secret_access_key: str
    bucket: str
    public_base_url: str
    feed_token: str

    @property
    def endpoint_url(self) -> str:
        return f"https://{self.account_id}.r2.cloudflarestorage.com"

    @property
    def feed_key(self) -> str:
        return f"feed-{self.feed_token}.xml"

    @property
    def feed_url(self) -> str:
        return f"{self.public_base_url.rstrip('/')}/{self.feed_key}"

    def episode_url(self, edition: str, date: str) -> str:
        key = EPISODE_KEY.format(edition=edition, date=date)
        return f"{self.public_base_url.rstrip('/')}/{key}"


@dataclass(frozen=True)
class SMTPConfig:
    host: str
    port: int
    username: str
    password: str
    sender: str
    recipient: str
    use_ssl: bool


class MissingCredential(RuntimeError):
    """Raised when a stage is reached without the secrets it needs."""


def _require(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise MissingCredential(
            f"{name} is not set. See the secrets table in README.md for where it comes from."
        )
    return value


def r2_config() -> R2Config:
    return R2Config(
        account_id=_require("R2_ACCOUNT_ID"),
        access_key_id=_require("R2_ACCESS_KEY_ID"),
        secret_access_key=_require("R2_SECRET_ACCESS_KEY"),
        bucket=_require("R2_BUCKET"),
        public_base_url=_require("R2_PUBLIC_BASE_URL"),
        feed_token=_require("FEED_TOKEN"),
    )


def smtp_config() -> SMTPConfig:
    raw_port = os.environ.get("SMTP_PORT", "465").strip()
    try:
        port = int(raw_port)
    except ValueError as exc:
        raise MissingCredential("SMTP_PORT must be an integer") from exc
    if not 1 <= port <= 65535:
        raise MissingCredential("SMTP_PORT must be between 1 and 65535")

    raw_ssl = os.environ.get("SMTP_USE_SSL", "true").strip().lower()
    if raw_ssl not in {"true", "false"}:
        raise MissingCredential("SMTP_USE_SSL must be true or false")

    username = _require("SMTP_USERNAME")
    return SMTPConfig(
        host=_require("SMTP_HOST"),
        port=port,
        username=username,
        password=_require("SMTP_PASSWORD"),
        sender=os.environ.get("EMAIL_FROM", "").strip() or username,
        recipient=_require("EMAIL_TO"),
        use_ssl=raw_ssl == "true",
    )


def openrouter_key() -> str:
    return _require("OPENROUTER_API_KEY")


def gemini_key() -> str:
    return _require("GEMINI_API_KEY")


TIMEZONE = "America/New_York"
