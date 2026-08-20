"""Stage 2: edition HTML -> items[].

Parses structurally rather than by CSS class. TLDR's class names are
Tailwind-ish and churn; the heading -> paragraph -> link shape has been
stable for years. Sponsor filtering lives here and nowhere else.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, asdict
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

from bs4 import BeautifulSoup, Tag

log = logging.getLogger(__name__)

HEADING_TAGS = ("h1", "h2", "h3", "h4", "h5", "h6")

# "Title (5 minute read)" / "(GitHub Repo)" / "(Sponsor)"
ANNOTATION = re.compile(r"\s*\(([^()]*)\)\s*$")
READ_TIME = re.compile(r"(\d+)\s*minute\s*read", re.I)

SPONSOR_HOSTS = frozenset({"advertise.tldr.tech", "jobs.ashbyhq.com"})

# Matched against the parenthetical annotation, where "(Sponsor)" lives. Kept
# separate from the title check: "sponsor" is an ordinary word in a headline
# ("Sponsors Pull Out Of..."), but in the annotation slot it only ever means an ad.
SPONSOR_ANNOTATIONS = ("sponsor", "advertisement")

# Matched against the raw title, for the case where the annotation was not split off.
SPONSOR_TITLE_MARKERS = ("(sponsor)", "sponsored by", "(advertisement)")

# Headings that introduce a section rather than an item. Matched loosely:
# TLDR renames sections occasionally and adds emoji.
KNOWN_SECTIONS = (
    "big tech & startups",
    "science & futuristic technology",
    "programming, design & data science",
    "miscellaneous",
    "quick links",
)


class ParseError(RuntimeError):
    """Structure changed out from under us. Hard failure, per the matrix."""


@dataclass
class Item:
    section: str
    title: str
    url: str
    blurb: str
    read_time: int | None = None
    is_github_repo: bool = False
    enriched: bool = False
    text: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def normalize_url(url: str) -> str:
    """Strip tracking params. Applied before fetching and before dedup."""
    parts = urlsplit(url)
    kept = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if not key.lower().startswith("utm_") and key.lower() not in {"sp", "ref", "source"}
    ]
    return urlunsplit(
        (parts.scheme, parts.netloc, parts.path.rstrip("/") or "/", urlencode(kept), "")
    )


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _looks_like_section(heading: Tag) -> bool:
    """A section header introduces items; it never links to one."""
    if heading.find("a", href=True):
        return False
    text = _clean(heading.get_text()).lower()
    if not text:
        return False
    return any(known in text for known in KNOWN_SECTIONS) or len(text) < 60


def _split_annotation(text: str) -> tuple[str, str | None]:
    match = ANNOTATION.search(text)
    if not match:
        return _clean(text), None
    return _clean(text[: match.start()]), _clean(match.group(1))


def _blurb_after(heading: Tag) -> str:
    """Collect sibling paragraphs until the next heading."""
    chunks: list[str] = []
    for sibling in heading.next_siblings:
        if not isinstance(sibling, Tag):
            continue
        if sibling.name in HEADING_TAGS:
            break
        if sibling.name in ("p", "div", "span"):
            text = _clean(sibling.get_text(" "))
            if text:
                chunks.append(text)
        if len(" ".join(chunks)) > 1_200:
            break
    return _clean(" ".join(chunks))


def is_sponsored(title: str, url: str, section: str, annotation: str | None) -> bool:
    """Sponsor detection. Deliberately aggressive — a false positive costs one
    item, a false negative reads an ad into the episode."""
    if annotation and any(
        marker in annotation.lower() for marker in SPONSOR_ANNOTATIONS
    ):
        return True
    if any(marker in title.lower() for marker in SPONSOR_TITLE_MARKERS):
        return True
    if "sponsor" in section.lower():
        return True
    host = (urlsplit(url).hostname or "").lower()
    if host in SPONSOR_HOSTS:
        return True
    if host.endswith(".ashbyhq.com"):
        return True
    return False


def parse_edition(html: str) -> list[Item]:
    """HTML -> items, sponsors removed, URLs normalized, order preserved."""
    soup = BeautifulSoup(html, "html.parser")
    root = soup.find("main") or soup.body or soup

    items: list[Item] = []
    seen: set[str] = set()
    section = "Uncategorized"
    dropped_sponsors = 0

    for heading in root.find_all(HEADING_TAGS):
        anchor = heading.find("a", href=True)

        if anchor is None:
            if _looks_like_section(heading):
                section = _clean(heading.get_text())
            continue

        raw_title = _clean(anchor.get_text(" "))
        if not raw_title:
            continue

        title, annotation = _split_annotation(raw_title)
        url = normalize_url(anchor["href"])
        if not url.startswith(("http://", "https://")):
            continue

        if is_sponsored(title, url, section, annotation):
            dropped_sponsors += 1
            log.debug("dropped sponsored item: %s (%s)", title, url)
            continue

        if url in seen:  # same story linked twice within one edition
            continue
        seen.add(url)

        read_time_match = READ_TIME.search(annotation or "")
        items.append(
            Item(
                section=section,
                title=title,
                url=url,
                blurb=_blurb_after(heading),
                read_time=int(read_time_match.group(1)) if read_time_match else None,
                is_github_repo="github repo" in (annotation or "").lower(),
            )
        )

    log.info("parsed %d items (%d sponsored dropped)", len(items), dropped_sponsors)

    if not items:
        raise ParseError(
            "0 items parsed — the page structure has almost certainly changed. "
            "Check the snapshot in R2 against src/parse.py."
        )
    if len(items) < 5:
        log.warning("only %d items parsed; expected 10-15. Structure may have shifted.", len(items))

    return items
