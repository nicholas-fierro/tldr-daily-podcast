"""Stage 7: upload to R2, rebuild the feed, prune old episodes.

The feed is rebuilt from the bucket listing on every run rather than appended
to. Rebuilding is idempotent and self-healing: a run that dies mid-publish
leaves a feed that the next run simply corrects.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from email.utils import format_datetime
from datetime import datetime, timezone
from pathlib import Path
from xml.sax.saxutils import escape

from . import config
from .config import R2Config

log = logging.getLogger(__name__)


class PublishError(RuntimeError):
    """Upload or feed rebuild failed. Hard failure, per the matrix."""


@dataclass
class Episode:
    date: str  # YYYY-MM-DD
    title: str
    url: str
    size_bytes: int
    duration_s: float
    description: str

    @property
    def guid(self) -> str:
        return f"tldr-daily-{self.date}"


def r2_client(cfg: R2Config):
    import boto3
    from botocore.config import Config as BotoConfig

    return boto3.client(
        "s3",
        endpoint_url=cfg.endpoint_url,
        aws_access_key_id=cfg.access_key_id,
        aws_secret_access_key=cfg.secret_access_key,
        region_name="auto",
        config=BotoConfig(retries={"max_attempts": 4, "mode": "standard"}),
    )


def format_duration(seconds: float) -> str:
    """HH:MM:SS, as iTunes expects."""
    total = int(round(seconds))
    return f"{total // 3600:02d}:{(total % 3600) // 60:02d}:{total % 60:02d}"


def build_description(items) -> str:
    """Show notes: the day's stories with URLs, for tapping through in the app."""
    lines = [f"{item.title}\n{item.url}" for item in items]
    return "Today's stories:\n\n" + "\n\n".join(lines)


def build_feed(episodes: list[Episode], cfg: R2Config, now: datetime | None = None) -> str:
    """Pure: episodes -> RSS 2.0 + iTunes XML. Newest first."""
    now = now or datetime.now(timezone.utc)
    ordered = sorted(episodes, key=lambda e: e.date, reverse=True)

    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<rss version="2.0" '
        'xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd" '
        'xmlns:content="http://purl.org/rss/1.0/modules/content/">',
        "<channel>",
        f"<title>{escape(config.PODCAST_TITLE)}</title>",
        f"<description>{escape(config.PODCAST_DESCRIPTION)}</description>",
        f"<link>{escape(cfg.public_base_url)}</link>",
        f"<language>{config.PODCAST_LANGUAGE}</language>",
        f"<lastBuildDate>{format_datetime(now)}</lastBuildDate>",
        f"<itunes:author>{escape(config.PODCAST_AUTHOR)}</itunes:author>",
        f"<itunes:summary>{escape(config.PODCAST_DESCRIPTION)}</itunes:summary>",
        "<itunes:explicit>false</itunes:explicit>",
        '<itunes:category text="Technology"/>',
        "<itunes:block>Yes</itunes:block>",  # personal feed: keep it out of directories
    ]

    for episode in ordered:
        published = datetime.strptime(episode.date, "%Y-%m-%d").replace(
            hour=12, tzinfo=timezone.utc
        )
        parts += [
            "<item>",
            f"<title>{escape(episode.title)}</title>",
            f'<guid isPermaLink="false">{escape(episode.guid)}</guid>',
            f"<pubDate>{format_datetime(published)}</pubDate>",
            f"<description>{escape(episode.description)}</description>",
            f"<itunes:duration>{format_duration(episode.duration_s)}</itunes:duration>",
            f'<enclosure url="{escape(episode.url)}" '
            f'length="{episode.size_bytes}" type="audio/mpeg"/>',
            "</item>",
        ]

    parts += ["</channel>", "</rss>"]
    return "\n".join(parts)


def upload(client, cfg: R2Config, key: str, body: bytes, content_type: str) -> None:
    try:
        client.put_object(Bucket=cfg.bucket, Key=key, Body=body, ContentType=content_type)
        log.info("uploaded %s (%d bytes)", key, len(body))
    except Exception as exc:  # noqa: BLE001
        raise PublishError(f"upload of {key} failed: {exc}") from exc


def upload_episode(client, cfg: R2Config, mp3: Path, date: str) -> tuple[str, int]:
    key = config.EPISODE_KEY.format(date=date)
    body = mp3.read_bytes()
    upload(client, cfg, key, body, "audio/mpeg")
    return key, len(body)


def list_episode_keys(client, cfg: R2Config) -> list[str]:
    keys: list[str] = []
    token = None
    while True:
        kwargs = {"Bucket": cfg.bucket, "Prefix": "episodes/"}
        if token:
            kwargs["ContinuationToken"] = token
        page = client.list_objects_v2(**kwargs)
        keys += [obj["Key"] for obj in page.get("Contents", []) if obj["Key"].endswith(".mp3")]
        if not page.get("IsTruncated"):
            return sorted(keys)
        token = page.get("NextContinuationToken")


def prune_old_episodes(client, cfg: R2Config, keep: int = config.RETAIN_EPISODES) -> list[str]:
    keys = list_episode_keys(client, cfg)
    stale = keys[:-keep] if len(keys) > keep else []
    for key in stale:
        client.delete_object(Bucket=cfg.bucket, Key=key)
        log.info("pruned %s", key)
    return stale


def publish_feed(client, cfg: R2Config, episodes: list[Episode]) -> str:
    xml = build_feed(episodes, cfg)
    upload(client, cfg, cfg.feed_key, xml.encode("utf-8"), "application/rss+xml")
    return cfg.feed_url


META_KEY = "meta/{date}.json"


def save_episode_meta(client, cfg: R2Config, episode: Episode) -> None:
    """Per-episode metadata, so the feed can be rebuilt from scratch later.

    The MP3 alone cannot tell us the headline or the show notes, and we rebuild
    rather than append — so the facts have to live somewhere durable.
    """
    import json

    upload(
        client, cfg, META_KEY.format(date=episode.date),
        json.dumps({
            "date": episode.date,
            "title": episode.title,
            "url": episode.url,
            "size_bytes": episode.size_bytes,
            "duration_s": episode.duration_s,
            "description": episode.description,
        }, indent=2).encode(),
        "application/json",
    )


def load_all_episode_meta(client, cfg: R2Config) -> list[Episode]:
    """Every episode still present in the bucket, for the feed rebuild."""
    import json

    live_dates = {
        Path(key).stem for key in list_episode_keys(client, cfg)
    }
    episodes: list[Episode] = []
    for date in sorted(live_dates):
        try:
            body = client.get_object(
                Bucket=cfg.bucket, Key=META_KEY.format(date=date)
            )["Body"].read()
            episodes.append(Episode(**json.loads(body)))
        except Exception as exc:  # noqa: BLE001 - one bad record must not drop the feed
            log.warning("no usable metadata for %s (%s); omitting from feed", date, exc)
    return episodes
