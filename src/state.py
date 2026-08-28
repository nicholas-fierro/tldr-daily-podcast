"""Idempotency and the dedup window.

Both are backed by R2 so they survive the ephemeral runner. Both fail open:
if state cannot be read we generate the episode rather than skip a day.
"""

from __future__ import annotations

import json
import logging
from datetime import date as Date, datetime, timedelta

from . import config
from .parse import Item

log = logging.getLogger(__name__)


def episode_exists(client, bucket: str, edition: str, date: str) -> bool:
    """Return whether this edition/date episode already exists."""
    key = config.EPISODE_KEY.format(edition=edition, date=date)
    try:
        client.head_object(Bucket=bucket, Key=key)
        return True
    except Exception as exc:  # ClientError 404, or anything transient
        if "404" in str(exc) or "Not Found" in str(exc):
            return False
        log.warning("idempotency check inconclusive (%s); proceeding", exc)
        return False


def load_seen(client, bucket: str, edition: str) -> dict[str, str]:
    """Load this edition's {normalized_url: YYYY-MM-DD first seen} state."""
    key = config.DEDUP_STATE_KEY.format(edition=edition)
    try:
        body = client.get_object(Bucket=bucket, Key=key)["Body"].read()
        return json.loads(body)
    except Exception as exc:  # noqa: BLE001 - a missing/corrupt window is not fatal
        log.info("no usable dedup state for %s (%s); starting a fresh window", edition, exc)
        return {}


def save_seen(
    client, bucket: str, edition: str, seen: dict[str, str], today: Date
) -> None:
    cutoff = (today - timedelta(days=config.DEDUP_RETAIN_DAYS)).isoformat()
    pruned = {url: seen_on for url, seen_on in seen.items() if seen_on >= cutoff}
    client.put_object(
        Bucket=bucket,
        Key=config.DEDUP_STATE_KEY.format(edition=edition),
        Body=json.dumps(pruned, indent=0, sort_keys=True).encode(),
        ContentType="application/json",
    )
    log.info("dedup window for %s: %d urls retained", edition, len(pruned))


def filter_recent_duplicates(
    items: list[Item], seen: dict[str, str], today: Date
) -> tuple[list[Item], list[Item]]:
    """Drop items whose URL appeared within the dedup window. Returns (kept, dropped)."""
    cutoff = (today - timedelta(days=config.DEDUP_WINDOW_DAYS)).isoformat()
    kept, dropped = [], []
    for item in items:
        first_seen = seen.get(item.url)
        if first_seen and first_seen >= cutoff and first_seen != today.isoformat():
            log.info("dropping repeat: %s (first seen %s)", item.title, first_seen)
            dropped.append(item)
        else:
            kept.append(item)
    return kept, dropped


def record_seen(seen: dict[str, str], items: list[Item], today: Date) -> dict[str, str]:
    updated = dict(seen)
    for item in items:
        updated.setdefault(item.url, today.isoformat())
    return updated


def today_in_league_tz() -> Date:
    """'Today' for recency purposes is Eastern, not the runner's UTC."""
    try:
        from zoneinfo import ZoneInfo

        return datetime.now(ZoneInfo(config.TIMEZONE)).date()
    except Exception:  # noqa: BLE001 - tzdata missing on a bare runner
        log.warning("no tzdata for %s; falling back to UTC", config.TIMEZONE)
        return datetime.utcnow().date()


def is_recent(
    edition_date: str,
    today: Date | None = None,
    max_age_days: int = config.EDITION_MAX_AGE_DAYS,
) -> bool:
    """Accept undelivered editions from today or the configured lookback window."""
    today = today or today_in_league_tz()
    published = datetime.strptime(edition_date, "%Y-%m-%d").date()
    age = (today - published).days
    return 0 <= age <= max_age_days
