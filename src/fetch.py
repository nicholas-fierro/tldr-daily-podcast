"""Stage 1: get the day's edition HTML and learn its date.

/api/latest/<edition> 302s to /<edition>/YYYY-MM-DD. Following that redirect
is how we learn the edition date without any date math or "has it published
yet?" guessing.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass

import httpx

from . import config

log = logging.getLogger(__name__)

DATE_IN_URL = re.compile(r"/(\d{4}-\d{2}-\d{2})(?:/|$)")
DATE_IN_TITLE = re.compile(r"TLDR\s+(\d{4}-\d{2}-\d{2})")


class FetchError(RuntimeError):
    """The edition page could not be retrieved. Hard failure, per the matrix."""


class EditionNotPublished(FetchError):
    """No edition exists for this source on this date.

    The only degradable fetch outcome: the source is omitted from the bundle
    and named in the coverage summary. Every other failure stays fatal.
    """


@dataclass(frozen=True)
class Edition:
    date: str  # YYYY-MM-DD
    url: str  # the final, post-redirect URL
    html: str
    edition: str


def _edition_date(final_url: str, html: str) -> str | None:
    """Prefer the redirect URL; fall back to the page title."""
    match = DATE_IN_URL.search(final_url)
    if match:
        return match.group(1)
    match = DATE_IN_TITLE.search(html)
    if match:
        log.warning("no date in URL %s; recovered %s from page title", final_url, match.group(1))
        return match.group(1)
    return None


def page_slug(edition: str) -> str:
    """The dated page path for an edition, which is not always its API slug."""
    return config.EDITION_PAGE_SLUGS.get(edition, edition)


def fetch_edition(edition: str = config.EDITION, date: str | None = None) -> Edition:
    """Fetch the latest edition, or a specific past one when `date` is given.

    Retries 3x with backoff on transport errors and 5xx. A 404 is not retried:
    a named date that does not exist will not start existing.
    """
    if date:
        url = config.EDITION_URL.format(edition=page_slug(edition), date=date)
    else:
        url = config.LATEST_URL.format(edition=edition)

    headers = {"User-Agent": config.USER_AGENT, "Accept": "text/html,*/*"}
    last_error: Exception | None = None

    for attempt in range(1, config.FETCH_RETRIES + 1):
        try:
            with httpx.Client(
                follow_redirects=True, timeout=config.FETCH_TIMEOUT, headers=headers
            ) as client:
                response = client.get(url)

            if response.status_code == 404:
                if date:
                    raise EditionNotPublished(
                        f"no {edition} edition was published for {date}"
                    )
                raise FetchError(f"{url} returned 404 — no such edition")
            response.raise_for_status()

            final_url = str(response.url)
            html = response.text
            resolved = _edition_date(final_url, html)

            # An unpublished dated page does not 404: it 307s to the undated
            # landing page, which carries no date at all. Verified 2026-08-28
            # against tldr.tech/fintech/2026-08-28.
            if date and resolved != date:
                raise EditionNotPublished(
                    f"no {edition} edition was published for {date} "
                    f"({url} resolved to {final_url})"
                )
            if resolved is None:
                raise FetchError(f"could not determine edition date from {final_url!r}")

            log.info("fetched %s (%d bytes)", final_url, len(html))
            return Edition(date=resolved, url=final_url, html=html, edition=edition)

        except FetchError:
            raise
        except (httpx.HTTPStatusError, httpx.TransportError) as exc:
            last_error = exc
            if attempt == config.FETCH_RETRIES:
                break
            delay = 2.0**attempt
            log.warning("fetch attempt %d/%d failed (%s); retrying in %.0fs",
                        attempt, config.FETCH_RETRIES, exc, delay)
            time.sleep(delay)

    raise FetchError(f"could not fetch {url} after {config.FETCH_RETRIES} attempts: {last_error}")
