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
OPENROUTER_TITLE = os.environ.get("OPENROUTER_TITLE", "Daily Standup Podcast").strip()

HOST_A = "Ava"  # frames, asks, drives the running order
HOST_B = "Ben"  # explains, contextualizes, supplies the numbers

WORD_TARGET_MIN = 1_350
WORD_TARGET_MAX = 1_450
WORD_ACCEPT_MIN = 1_200
WORD_ACCEPT_MAX = 1_600
WORD_HARD_MIN = 1_100
WORD_HARD_MAX = 1_700

# --- tts ------------------------------------------------------------------

TTS_PROVIDER = os.environ.get("TTS_PROVIDER", "gemini").strip().lower()

# Gemini model IDs churn. Override these rather than editing pinned defaults.
TTS_MODEL = os.environ.get("TTS_MODEL", "gemini-2.5-flash-preview-tts").strip()
TTS_VOICE_A = os.environ.get("TTS_VOICE_A", "Kore").strip()
TTS_VOICE_B = os.environ.get("TTS_VOICE_B", "Algenib").strip()

# Kokoro runs locally and renders one voice at a time.
KOKORO_LANG_CODE = os.environ.get("KOKORO_LANG_CODE", "a").strip()
KOKORO_VOICE_A = os.environ.get("KOKORO_VOICE_A", "af_heart").strip()
KOKORO_VOICE_B = os.environ.get("KOKORO_VOICE_B", "am_michael").strip()
KOKORO_SPEED = float(os.environ.get("KOKORO_SPEED", "1.0"))
TTS_LINE_GAP_MS = 180

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

# --- listening evaluation (eval/score_script.py, offline, stdlib only) --------

# Turn openers the script prompt forbids ("Avoid repetitive starts such as
# ..."). Any turn whose first word is one of these counts as a repetition hit.
EVAL_OPENER_TOKENS = ("and", "right", "exactly", "so", "well", "okay", "yeah")

# Full marks when the opener-hit rate is at or under this; linear fall to zero
# at EVAL_OPENER_CAP. A rare discourse "Right" is conversation, a habit is not.
EVAL_OPENER_TOLERANCE = 0.05
EVAL_OPENER_CAP = 0.30

# Tokens that make a non-opening turn count as contingent on the prior turn:
# explicit acknowledgement / agreement / contrast at the start of the turn.
EVAL_CONTINGENCY_MARKERS = (
    "yes", "yeah", "yep", "right", "exactly", "true", "fair", "sure",
    "agreed", "absolutely", "definitely", "hmm", "oh", "wow", "huh",
    "really", "no", "but", "however", "although", "though", "still",
    "yet", "except", "actually", "wait", "hold",
)

# A turn this short with no other signal is a backchannel ("Got it",
# "Makes sense") — contingent by form.
EVAL_BACKCHANNEL_MAX_WORDS = 6

# Audience-directed closing lines in the final segment. A real sign-off spans
# more than the single last turn ("Thanks for listening." / "See you tomorrow."
# / "Take care."), and those lines address the listener, not the prior turn, so
# scoring them for contingency false-flags them. Any final-segment line whose
# lower-cased text contains one of these phrases is exempted, as is the very
# last line regardless of wording.
EVAL_SIGNOFF_MARKERS = (
    "thanks for listening", "thanks for tuning in", "thanks for joining",
    "see you", "catch you", "until next time", "until tomorrow",
    "that's all", "that is all", "that's it for", "that is it for",
    "have a great", "have a good", "take care", "stay curious",
    "we'll be back", "we will be back", "back tomorrow",
)

# Lower-cased words ignored when matching turns against each other: function
# words and generic podcast filler that would otherwise fake lexical overlap
# or read as named entities.
EVAL_STOPWORDS = frozenset(
    """
    a about above after again against all almost also always among amount
    analysis and another any anyone anything around because become been
    before being below between big both brief bring but can company companys
    could daily data day doesnt doing dont down during each even every few
    first for from further get going good had has have having here however
    into its itself just know large last launch later least like look made
    major make manner many matter means might model more most much new news
    next number over part place quite rather read really round same second
    seems seen several should since small some something startup still such
    take than that the their them then there these they thing think this
    those through today under until using very want well went were what
    when where which while with within without would year years youre your
    across announced between chief corp during exec former half late major
    monday tuesday wednesday thursday friday saturday sunday morning evening
    today tonight week announced says reportedly giant unit announced
    january february march april may june july august september october
    november december
    """.split()
)

# Titlecase tokens shorter than this are never checkable entities.
EVAL_ENTITY_MIN_LEN = 4

# Coefficient-of-variation target for sentence lengths. Real two-person talk
# mixes short reactions with long explanations; monotone equal lengths read
# as one voice. Full marks at or above the target.
EVAL_SENTENCE_CV_TARGET = 0.55

# --- audio ----------------------------------------------------------------

SEGMENT_GAP_MS = 350
LOUDNORM_TARGET_LUFS = -16.0
MP3_BITRATE = "64k"

DURATION_MIN_S = 8 * 60
DURATION_MAX_S = 12 * 60

PODCAST_TITLE = "Daily Standup"
PODCAST_AUTHOR = "Nicholas Fierro"
PODCAST_DESCRIPTION = (
    "A two-host tech briefing, every weekday morning. Stories are selected from "
    "the TLDR newsletters and reported from the linked articles rather than the "
    "summaries. Episodes are generated automatically. Not affiliated with or "
    "endorsed by TLDR."
)
PODCAST_LANGUAGE = "en-us"
PODCAST_OWNER_NAME = "Nicholas Fierro"
# Apple wants an owner address only for directory submission, which this feed
# opts out of via <itunes:block>. Left unset so a personal address does not ship
# in a file every subscriber can read; set it if the show is ever submitted.
PODCAST_OWNER_EMAIL = os.environ.get("PODCAST_OWNER_EMAIL", "").strip()
PODCAST_COPYRIGHT = "(c) Nicholas Fierro"

# Credit and disclaimer, carried in the channel description and in every
# episode's show notes. The selection is TLDR's work; say so where a listener
# will actually see it.
PODCAST_ATTRIBUTION = (
    "Story selection derives from the TLDR newsletters (https://tldr.tech). "
    "This show is not affiliated with or endorsed by TLDR. Episodes are "
    "generated automatically — check the linked source before relying on a detail."
)

# Cover art. Square, 1400x1400 minimum and 3000x3000 recommended, RGB JPEG or
# PNG. Uploaded once by hand; the feed only ever references it.
ARTWORK_KEY = "artwork/cover.jpg"

# Prefix for feed GUIDs. Persistent and published, though never displayed:
# changing it makes every subscribed client re-download the back catalogue once.
GUID_PREFIX = "daily-standup"

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

    def redact(self, text: str) -> str:
        """Strip the feed token out of anything bound for a log or an error
        message. CI runs in a public repository, so its logs are public, and the
        unguessable path is the only thing guarding the feed. Every string that
        can carry the feed key — object keys included — goes through here rather
        than relying on the platform to redact the secret for us."""
        return text.replace(self.feed_token, "REDACTED")

    @property
    def masked_feed_url(self) -> str:
        """`feed_url` with the token elided. Derived, so it cannot drift from
        the real key format."""
        return self.redact(self.feed_url)

    @property
    def artwork_url(self) -> str:
        return f"{self.public_base_url.rstrip('/')}/{ARTWORK_KEY}"

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
