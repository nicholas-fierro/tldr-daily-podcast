"""Stage 3: fetch each linked article and extract readable text.

Best-effort by design. 20-35% of items will fail — paywalls, JS-only sites,
bot walls — and that is a normal run, not a broken one. Every failure falls
back to TLDR's blurb with enriched=False so the script writer knows to hedge.
Nothing in here is allowed to fail the run.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import re

import httpx
import trafilatura

from . import config
from .parse import Item

log = logging.getLogger(__name__)

GITHUB_REPO_URL = re.compile(r"^https?://(?:www\.)?github\.com/([^/]+)/([^/#?]+)")


def _is_paywall_stub(text: str) -> bool:
    if len(text) < config.PAYWALL_MIN_CHARS:
        return True

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if sum(line in {"-", "•"} for line in lines) >= 8:
        return True

    head = text[:1_500].lower()
    return any(marker in head for marker in config.PAYWALL_MARKERS)


def _extract(html: str, url: str) -> str:
    """Trafilatura, tuned for news: drop comments and boilerplate."""
    text = trafilatura.extract(
        html,
        url=url,
        include_comments=False,
        include_tables=False,
        favor_precision=True,
    )
    return (text or "").strip()


def _truncate(text: str) -> str:
    """Keep the lede and the first few sections; the tail is boilerplate."""
    if len(text) <= config.ARTICLE_CHAR_LIMIT:
        return text
    cut = text[: config.ARTICLE_CHAR_LIMIT]
    boundary = cut.rfind("\n\n")
    if boundary < config.ARTICLE_CHAR_LIMIT // 2:
        boundary = cut.rfind(". ")
    return (cut[: boundary + 1] if boundary > 0 else cut).strip()


async def _github_readme(client: httpx.AsyncClient, url: str) -> str:
    """Read the README through the API rather than scraping the repo page."""
    match = GITHUB_REPO_URL.match(url)
    if not match:
        return ""
    owner, repo = match.group(1), match.group(2).removesuffix(".git")

    headers = {"Accept": "application/vnd.github+json"}
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if token:  # optional: lifts the 60/hr unauthenticated ceiling
        headers["Authorization"] = f"Bearer {token}"

    response = await client.get(
        f"https://api.github.com/repos/{owner}/{repo}/readme",
        headers=headers,
        timeout=config.ENRICH_TIMEOUT,
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("encoding") != "base64":
        return ""
    readme = base64.b64decode(payload["content"]).decode("utf-8", errors="replace")
    # Badge soup and HTML comments carry no meaning when read aloud.
    readme = re.sub(r"<!--.*?-->", " ", readme, flags=re.S)
    readme = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", readme)
    return readme.strip()


async def _enrich_one(client: httpx.AsyncClient, item: Item, sem: asyncio.Semaphore) -> Item:
    async with sem:
        for attempt in range(config.ENRICH_RETRIES + 1):
            try:
                if item.is_github_repo or GITHUB_REPO_URL.match(item.url):
                    text = await _github_readme(client, item.url)
                else:
                    response = await client.get(item.url, timeout=config.ENRICH_TIMEOUT)
                    response.raise_for_status()
                    text = _extract(response.text, item.url)

                if not text or _is_paywall_stub(text):
                    log.info("no usable text for %s — falling back to blurb", item.url)
                    break

                item.text = _truncate(text)
                item.enriched = True
                log.info("enriched %s (%d chars)", item.url, len(item.text))
                return item

            except httpx.HTTPStatusError as exc:
                retryable = exc.response.status_code >= 500
                log.info("enrich %s -> HTTP %d", item.url, exc.response.status_code)
                if not retryable or attempt == config.ENRICH_RETRIES:
                    break
            except (httpx.TransportError, asyncio.TimeoutError) as exc:
                log.info("enrich %s -> %s", item.url, type(exc).__name__)
                if attempt == config.ENRICH_RETRIES:
                    break
            except Exception as exc:  # noqa: BLE001 - enrichment must never fail the run
                log.warning("enrich %s -> unexpected %s: %s", item.url, type(exc).__name__, exc)
                break
            await asyncio.sleep(2.0 * (attempt + 1))

    item.text = item.blurb
    item.enriched = False
    return item


async def enrich_items_async(items: list[Item]) -> list[Item]:
    sem = asyncio.Semaphore(config.ENRICH_CONCURRENCY)
    headers = {"User-Agent": config.USER_AGENT, "Accept": "text/html,*/*"}
    async with httpx.AsyncClient(follow_redirects=True, headers=headers) as client:
        return await asyncio.gather(*(_enrich_one(client, item, sem) for item in items))


def enrich_items(items: list[Item]) -> list[Item]:
    """Populate `.text` and `.enriched` on every item. Never raises."""
    enriched = asyncio.run(enrich_items_async(items))
    rate = enrichment_rate(enriched)
    log.info("enrichment rate: %.0f%% (%d/%d)",
             rate * 100, sum(i.enriched for i in enriched), len(enriched))
    if rate < config.ENRICH_TARGET_RATE:
        log.warning("enrichment below the %.0f%% target — more hedged items than usual",
                    config.ENRICH_TARGET_RATE * 100)
    return enriched


def enrichment_rate(items: list[Item]) -> float:
    if not items:
        return 0.0
    return sum(1 for item in items if item.enriched) / len(items)
