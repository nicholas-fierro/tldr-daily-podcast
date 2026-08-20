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


def episode_exists(client, bucket: str, date: str) -> bool:
    """First real action of every run. Exists -> the run exits 0."""
    key = config.EPISODE_KEY.format(date=date)
    try:
        client.head_object(Bucket=bucket, Key=key)
        return True
    except Exception as exc:  # ClientError 404, or anything transient
        if "404" in str(exc) or "Not Found" in str(exc):
            return False
        log.warning("idempotency check inconclusive (%s); proceeding", exc)
        return False


def load_seen(client, bucket: str) -> dict[str, str]:
    """{normalized_url: YYYY-MM-DD first seen}."""
    try:
        body = client.get_object(Bucket=bucket, Key=config.DEDUP_STATE_KEY)["Body"].read()
        return json.loads(body)
    except Exception as exc:  # noqa: BLE001 - a missing/corrupt window is not fatal
        log.info("no usable dedup state (%s); starting a fresh window", exc)
        return {}


def save_seen(client, bucket: str, seen: dict[str, str], today: Date) -> None:
    cutoff = (today - timedelta(days=config.DEDUP_RETAIN_DAYS)).isoformat()
    pruned = {url: seen_on for url, seen_on in seen.items() if seen_on >= cutoff}
    client.put_object(
        Bucket=bucket,
        Key=config.DEDUP_STATE_KEY,
        Body=json.dumps(pruned, indent=0, sort_keys=True).encode(),
        ContentType="application/json",
    )
    log.info("dedup window: %d urls retained", len(pruned))


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
    """'Today' for freshness purposes is Eastern, not the runner's UTC."""
    try:
        from zoneinfo import ZoneInfo

        return datetime.now(ZoneInfo(config.TIMEZONE)).date()
    except Exception:  # noqa: BLE001 - tzdata missing on a bare runner
        log.warning("no tzdata for %s; falling back to UTC", config.TIMEZONE)
        return datetime.utcnow().date()


def is_fresh(edition_date: str, today: Date | None = None) -> bool:
    """The freshness guard: a stale edition means today's has not published yet."""
    today = today or today_in_league_tz()
    return edition_date == today.isoformat()
