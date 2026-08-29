#!/usr/bin/env python3
"""TLDR -> daily podcast.

    python main.py                      # today's edition, full pipeline
    python main.py --bundle daily       # one episode from all four newsletters
    python main.py --date 2026-08-20    # re-run a past edition
    python main.py --stage parse        # stop after a stage and print the result
    python main.py --stage enrich --force        # M2, no R2 credentials
    python main.py --email                       # generate and email the MP3

Stages run in order and each one stops after the named step. See README.md for
the full flag reference and the delivery modes.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from src import (
    audio,
    combine,
    config,
    email_delivery,
    enrich,
    fetch,
    parse,
    publish,
    script,
    state,
    tts,
)

log = logging.getLogger("tldr")

STAGES = ("fetch", "parse", "combine", "enrich", "script", "audio", "publish")


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


def _write_email_marker(path: str | None, edition: str, date: str) -> None:
    if not path:
        return
    marker = Path(path)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(f"{edition}:{date}\n", encoding="utf-8")


def _gather_remaining_sources(
    editions: tuple[str, ...],
    anchor: fetch.Edition,
) -> tuple[list[fetch.Edition], list[combine.EditionCoverage]]:
    """Fetch the rest of a bundle at the anchor's date, exactly.

    A source with no edition that day is omitted and recorded; any other fetch
    failure propagates and fails the episode. Called only once the anchor has
    cleared the recency and idempotency guards, so a no-op run costs one fetch.
    """
    target = anchor.date
    sources = [anchor]
    coverage = [
        combine.EditionCoverage(
            edition=anchor.edition,
            status=combine.INCLUDED,
        )
    ]

    for edition in editions[1:]:
        try:
            fetched = fetch.fetch_edition(edition=edition, date=target)
        except fetch.EditionNotPublished as exc:
            log.warning("%s: %s", edition, exc)
            coverage.append(
                combine.EditionCoverage(
                    edition=edition,
                    status=combine.NOT_PUBLISHED,
                    reason=f"no {target} edition was published",
                )
            )
            continue
        sources.append(fetched)
        coverage.append(
            combine.EditionCoverage(edition=edition, status=combine.INCLUDED)
        )

    return sources, coverage


def run(args: argparse.Namespace) -> int:
    stop_after = _stage_index(args.stage)
    workdir = Path(args.local)
    workdir.mkdir(parents=True, exist_ok=True)

    # --- fetch ---
    resolved_date = getattr(args, "resolved_date", None)
    bundle = getattr(args, "bundle", None)
    source_editions = (
        combine.bundle_editions(bundle) if bundle else (args.edition,)
    )
    # The delivered identity: a bundle name for combined runs, the edition slug
    # otherwise. Object keys, markers, filenames, and GUIDs all hang off this.
    name = bundle or args.edition

    # The anchor alone fixes the target date and clears the guards; the rest of
    # the bundle is fetched only once there is work to do.
    anchor = fetch.fetch_edition(
        edition=source_editions[0], date=args.date or resolved_date
    )
    date = anchor.date
    identity = f"{name}-{date}"
    log.info("target date %s, anchored on %s", date, anchor.edition)

    if not args.date and not args.force and not state.is_recent(date):
        log.info(
            "latest %s edition is %s, outside the %d-day lookback ending %s — nothing to do",
            name,
            date,
            config.EDITION_MAX_AGE_DAYS,
            state.today_in_league_tz(),
        )
        return 0

    client = cfg = None
    seen: dict[str, str] = {}
    if stop_after == _stage_index("publish") and not args.no_upload and not args.email:
        cfg = config.r2_config()
        client = publish.r2_client(cfg)
        if state.episode_exists(client, cfg.bucket, name, date) and not args.force:
            log.info("episode for %s already exists — exiting", identity)
            return 0

    sources, coverage = _gather_remaining_sources(source_editions, anchor)
    for source in sources:
        log.info("edition %s/%s -> %s", source.edition, source.date, source.url)
        (workdir / f"{source.edition}-{source.date}.html").write_text(
            source.html, encoding="utf-8"
        )

    if stop_after == _stage_index("fetch"):
        print(json.dumps({
            "edition": name,
            "date": date,
            "sources": [
                {"edition": s.edition, "url": s.url, "bytes": len(s.html)}
                for s in sources
            ],
            "coverage": [record.to_dict() for record in coverage],
        }, indent=2))
        return 0

    # --- parse ---
    parsed: list[combine.SourceEdition] = []
    for source in sources:
        items = parse.parse_edition(source.html)
        parsed.append(
            combine.SourceEdition(
                edition=source.edition, date=source.date, items=items
            )
        )
    counts = {source.edition: len(source.items) for source in parsed}
    coverage = [
        replace(record, item_count=counts.get(record.edition, 0))
        for record in coverage
    ]

    if stop_after == _stage_index("parse"):
        print(json.dumps(
            {
                "date": date,
                "coverage": [record.to_dict() for record in coverage],
                "sources": {
                    p.edition: [item.to_dict() for item in p.items] for p in parsed
                },
            }
            if bundle
            else [item.to_dict() for item in parsed[0].items],
            indent=2,
        ))
        return 0

    # --- combine ---
    items = combine.combine_editions(parsed)
    if stop_after == _stage_index("combine"):
        print(json.dumps({
            "edition": name,
            "date": date,
            "coverage": [record.to_dict() for record in coverage],
            "item_count": len(items),
            "items": [item.to_dict() for item in items],
        }, indent=2))
        return 0

    if client and cfg:
        for source in sources:
            publish.upload(
                client,
                cfg,
                config.SNAPSHOT_KEY.format(edition=source.edition, date=source.date),
                source.html.encode("utf-8"),
                "text/html",
            )
        seen = state.load_seen(client, cfg.bucket, name)
        today = datetime.strptime(date, "%Y-%m-%d").date()
        items, dropped = state.filter_recent_duplicates(items, seen, today)
        if dropped:
            log.info(
                "dropped %d repeat(s) from the last %d days",
                len(dropped),
                config.DEDUP_WINDOW_DAYS,
            )

    # --- enrich ---
    items = enrich.enrich_items(items)
    rate = enrich.enrichment_rate(items)
    if stop_after == _stage_index("enrich"):
        print(json.dumps({
            "edition": name,
            "date": date,
            "coverage": [record.to_dict() for record in coverage],
            "enrichment_rate": round(rate, 3),
            "enriched": sum(i.enriched for i in items),
            "total": len(items),
            "items": [item.to_dict() for item in items],
        }, indent=2))
        return 0

    # --- script ---
    episode_script = script.generate_script(
        items,
        date,
        edition=name,
        sources=[record.edition for record in coverage if record.included]
        if bundle
        else None,
    )
    script_path = workdir / f"{identity}.script.json"
    script_path.write_text(json.dumps(episode_script.to_dict(), indent=2), encoding="utf-8")
    log.info("script saved to %s", script_path)
    if client and cfg:
        publish.upload(
            client,
            cfg,
            config.SCRIPT_KEY.format(edition=name, date=date),
            script_path.read_bytes(),
            "application/json",
        )
    if stop_after == _stage_index("script"):
        print(script_path.read_text())
        return 0

    # --- audio ---
    rendered = tts.render_segments(episode_script, tts.GeminiTTS())
    headline = (
        f"{config.PODCAST_TITLE} — {date}"
        if bundle
        else f"{config.PODCAST_TITLE} {name.upper()} — {date}"
    )
    mp3, duration = audio.build_episode(
        rendered,
        date,
        headline,
        workdir,
        edition=name,
    )
    if stop_after == _stage_index("audio") or args.no_upload:
        print(json.dumps({
            "edition": name,
            "date": date,
            "coverage": [record.to_dict() for record in coverage],
            "mp3": str(mp3),
            "duration_s": round(duration, 1),
            "segments": len(rendered),
        }, indent=2))
        return 0

    if args.email:
        email_delivery.send_episode(
            mp3,
            date,
            headline,
            config.smtp_config(),
            edition=name,
            coverage=coverage if bundle else None,
        )
        _write_email_marker(args.email_marker, name, date)
        print(json.dumps({
            "edition": name,
            "date": date,
            "coverage": [record.to_dict() for record in coverage],
            "mp3": str(mp3),
            "duration_s": round(duration, 1),
            "segments": len(rendered),
            "delivery": "email",
        }, indent=2))
        return 0

    # --- publish ---
    if client is None or cfg is None:
        raise publish.PublishError("R2 publishing was not initialized")
    _, size = publish.upload_episode(client, cfg, mp3, name, date)
    episode = publish.Episode(
        edition=name,
        date=date,
        title=headline,
        url=cfg.episode_url(name, date),
        size_bytes=size,
        duration_s=duration,
        description=publish.build_description(items),
    )
    publish.save_episode_meta(client, cfg, episode)
    publish.prune_old_episodes(client, cfg, name)
    feed_url = publish.publish_feed(
        client,
        cfg,
        publish.load_all_episode_meta(client, cfg),
    )

    today = datetime.strptime(date, "%Y-%m-%d").date()
    state.save_seen(
        client,
        cfg.bucket,
        name,
        state.record_seen(seen, items, today),
        today,
    )

    log.info("published %s (%.1f min) -> %s", identity, duration / 60, feed_url)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--date", help="YYYY-MM-DD; re-run a past edition")
    parser.add_argument(
        "--resolved-date",
        help="CI-resolved latest date; unlike --date, recency still applies",
    )
    # No default, so that an explicit --edition can be told from an unset one
    # and rejected alongside --bundle. run() falls back to config.EDITION.
    parser.add_argument("--edition",
                        help=f"TLDR edition slug (default {config.EDITION}; "
                             "ai, webdev, fintech, infosec)")
    parser.add_argument("--bundle", choices=sorted(config.EDITION_BUNDLES),
                        help="combine several editions of one date into one episode")
    parser.add_argument("--stage", default="publish", choices=STAGES,
                        help="stop after this stage and print its output")
    parser.add_argument("--local", default="out",
                        help="working directory for intermediate files")
    delivery = parser.add_mutually_exclusive_group()
    delivery.add_argument("--no-upload", action="store_true",
                          help="generate locally without R2 or email delivery")
    delivery.add_argument("--email", action="store_true",
                          help="email the MP3 instead of publishing to R2")
    parser.add_argument("--email-marker",
                        help="write this file only after successful email delivery")
    parser.add_argument("--force", action="store_true",
                        help="ignore the recency guard and the idempotency check")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    if args.date and args.resolved_date:
        parser.error("--date and --resolved-date are mutually exclusive")
    if args.bundle and args.edition:
        parser.error("--bundle and --edition are mutually exclusive")
    args.edition = args.edition or config.EDITION

    _setup_logging(args.verbose)
    try:
        return run(args)
    except config.MissingCredential as exc:
        log.error("%s", exc)
        return 2
    except (fetch.FetchError, parse.ParseError, script.ScriptError,
            tts.TTSError, audio.AudioError, publish.PublishError,
            email_delivery.EmailDeliveryError) as exc:
        log.error("%s: %s", type(exc).__name__, exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
