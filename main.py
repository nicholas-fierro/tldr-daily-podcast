#!/usr/bin/env python3
"""TLDR -> daily podcast.

    python main.py                      # today's edition, full pipeline
    python main.py --date 2026-08-20    # re-run a past edition
    python main.py --stage parse        # stop after a stage and print the result
    python main.py --stage enrich --force        # M2, no R2 credentials

Stages run in order and each one stops after the named step, which is how the
milestones in docs/HANDOFF.md are meant to be driven.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

from src import audio, config, enrich, fetch, parse, publish, script, state, tts

log = logging.getLogger("tldr")

STAGES = ("fetch", "parse", "enrich", "script", "audio", "publish")


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("botocore").setLevel(logging.WARNING)


def _stage_index(name: str) -> int:
    return STAGES.index(name)


def run(args: argparse.Namespace) -> int:
    stop_after = _stage_index(args.stage)
    workdir = Path(args.local)
    workdir.mkdir(parents=True, exist_ok=True)

    # --- fetch ---
    edition = fetch.fetch_edition(edition=args.edition, date=args.date)
    log.info("edition %s -> %s", edition.date, edition.url)
    (workdir / f"{edition.date}.html").write_text(edition.html, encoding="utf-8")

    # The freshness guard: a stale edition means today's has not published yet.
    if not args.date and not args.force and not state.is_fresh(edition.date):
        log.info("latest edition is %s, not today (%s) — nothing to do",
                 edition.date, state.today_in_league_tz())
        return 0

    if stop_after == _stage_index("fetch"):
        print(json.dumps({"date": edition.date, "url": edition.url,
                          "bytes": len(edition.html)}, indent=2))
        return 0

    # --- parse ---
    items = parse.parse_edition(edition.html)
    if stop_after == _stage_index("parse"):
        print(json.dumps([item.to_dict() for item in items], indent=2))
        return 0

    # R2 is part of publishing, not local milestone checkpoints.
    client = cfg = None
    if stop_after == _stage_index("publish") and not args.no_upload:
        cfg = config.r2_config()
        client = publish.r2_client(cfg)

        # Idempotency: the first real action of every run.
        if state.episode_exists(client, cfg.bucket, edition.date) and not args.force:
            log.info("episode for %s already exists — exiting", edition.date)
            return 0

        publish.upload(client, cfg, config.SNAPSHOT_KEY.format(date=edition.date),
                       edition.html.encode("utf-8"), "text/html")

        seen = state.load_seen(client, cfg.bucket)
        today = datetime.strptime(edition.date, "%Y-%m-%d").date()
        items, dropped = state.filter_recent_duplicates(items, seen, today)
        if dropped:
            log.info("dropped %d repeat(s) from the last %d days",
                     len(dropped), config.DEDUP_WINDOW_DAYS)

    # --- enrich ---
    items = enrich.enrich_items(items)
    rate = enrich.enrichment_rate(items)
    if stop_after == _stage_index("enrich"):
        print(json.dumps({
            "enrichment_rate": round(rate, 3),
            "enriched": sum(i.enriched for i in items),
            "total": len(items),
            "items": [item.to_dict() for item in items],
        }, indent=2))
        return 0

    # --- script ---
    episode_script = script.generate_script(items, edition.date)
    script_path = workdir / f"{edition.date}.script.json"
    script_path.write_text(json.dumps(episode_script.to_dict(), indent=2), encoding="utf-8")
    log.info("script saved to %s", script_path)
    if client:
        publish.upload(client, cfg, config.SCRIPT_KEY.format(date=edition.date),
                       script_path.read_bytes(), "application/json")
    if stop_after == _stage_index("script"):
        print(script_path.read_text())
        return 0

    # --- audio ---
    rendered = tts.render_segments(episode_script, tts.GeminiTTS())
    headline = f"{config.PODCAST_TITLE} — {edition.date}"
    mp3, duration = audio.build_episode(rendered, edition.date, headline, workdir)
    if stop_after == _stage_index("audio") or args.no_upload:
        print(json.dumps({"mp3": str(mp3), "duration_s": round(duration, 1),
                          "segments": len(rendered)}, indent=2))
        return 0

    # --- publish ---
    _, size = publish.upload_episode(client, cfg, mp3, edition.date)
    episode = publish.Episode(
        date=edition.date,
        title=headline,
        url=cfg.episode_url(edition.date),
        size_bytes=size,
        duration_s=duration,
        description=publish.build_description(items),
    )
    publish.save_episode_meta(client, cfg, episode)
    publish.prune_old_episodes(client, cfg)
    feed_url = publish.publish_feed(client, cfg, publish.load_all_episode_meta(client, cfg))

    today = datetime.strptime(edition.date, "%Y-%m-%d").date()
    state.save_seen(client, cfg.bucket, state.record_seen(seen, items, today), today)

    log.info("published %s (%.1f min) -> %s", edition.date, duration / 60, feed_url)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--date", help="YYYY-MM-DD; re-run a past edition")
    parser.add_argument("--edition", default=config.EDITION,
                        help="TLDR edition slug (tech, ai, webdev, infosec)")
    parser.add_argument("--stage", default="publish", choices=STAGES,
                        help="stop after this stage and print its output")
    parser.add_argument("--local", default="out",
                        help="working directory for intermediate files")
    parser.add_argument("--no-upload", action="store_true",
                        help="skip all R2 access; implies no idempotency or dedup")
    parser.add_argument("--force", action="store_true",
                        help="ignore the freshness guard and the idempotency check")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    _setup_logging(args.verbose)
    try:
        return run(args)
    except config.MissingCredential as exc:
        log.error("%s", exc)
        return 2
    except (fetch.FetchError, parse.ParseError, script.ScriptError,
            tts.TTSError, audio.AudioError, publish.PublishError) as exc:
        log.error("%s: %s", type(exc).__name__, exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
