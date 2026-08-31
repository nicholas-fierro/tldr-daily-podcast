"""Stage 3: several editions of one date -> one deduplicated running order.

Four newsletters produce 50-70 items for a ten-minute episode, and the same
story is often carried by two of them. This stage merges those duplicates,
keeps every source label so the script can credit coverage honestly, and
selects a balanced subset so no included edition is crowded out.

Pure: it takes already-parsed items and returns items. Fetching and parsing
stay in their own stages, and the coverage record is built by the caller from
what those stages actually returned.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from . import config
from .parse import Item

log = logging.getLogger(__name__)

INCLUDED = "included"
NOT_PUBLISHED = "not_published"

# Words that carry no distinguishing signal in a headline. Two stories sharing
# only these are not the same story.
TITLE_STOPWORDS = frozenset({
    "a", "an", "the", "and", "or", "but", "of", "to", "in", "on", "for", "with",
    "at", "by", "from", "as", "is", "are", "was", "were", "be", "been", "it",
    "its", "that", "this", "these", "those", "new", "how", "why", "what",
    "after", "over", "into", "out", "up", "down", "more", "than", "will",
})

TOKEN = re.compile(r"[a-z0-9]+")


@dataclass
class CombinedItem(Item):
    """An item plus the editions that carried it.

    Subclasses Item so every downstream stage — enrich, script, publish —
    keeps working unchanged.
    """

    sources: list[str] = field(default_factory=list)
    related_urls: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class EditionCoverage:
    """What one source contributed to the bundle, and why."""

    edition: str
    status: str
    item_count: int = 0
    reason: str = ""

    @property
    def name(self) -> str:
        return edition_name(self.edition)

    @property
    def included(self) -> bool:
        return self.status == INCLUDED

    def to_dict(self) -> dict:
        return {
            "edition": self.edition,
            "name": self.name,
            "status": self.status,
            "item_count": self.item_count,
            "reason": self.reason,
        }


@dataclass
class SourceEdition:
    """One included source: its parsed items, already confirmed on-date."""

    edition: str
    date: str
    items: list[Item]


def bundle_editions(bundle: str) -> tuple[str, ...]:
    """The source editions of a bundle. The first is the date anchor."""
    try:
        return config.EDITION_BUNDLES[bundle]
    except KeyError:
        known = ", ".join(sorted(config.EDITION_BUNDLES))
        raise KeyError(f"unknown bundle {bundle!r}; known bundles: {known}") from None


def edition_name(edition: str) -> str:
    return config.EDITION_NAMES.get(edition, edition.upper())


def _title_tokens(title: str) -> frozenset[str]:
    return frozenset(
        token
        for token in TOKEN.findall(title.lower())
        if token not in TITLE_STOPWORDS and len(token) > 2
    )


def title_similarity(left: str, right: str) -> float:
    """Jaccard overlap of meaningful title tokens, 0.0-1.0."""
    a, b = _title_tokens(left), _title_tokens(right)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _merge_into(target: CombinedItem, source_edition: str, other: Item) -> None:
    """Fold a duplicate into the item already holding the story."""
    if source_edition not in target.sources:
        target.sources.append(source_edition)
    if other.url != target.url and other.url not in target.related_urls:
        target.related_urls.append(other.url)
    # The fuller writeup is the more useful fallback if enrichment fails.
    if len(other.blurb) > len(target.blurb):
        target.blurb = other.blurb
    if target.read_time is None:
        target.read_time = other.read_time


def _as_combined(edition: str, item: Item) -> CombinedItem:
    return CombinedItem(
        section=item.section,
        title=item.title,
        url=item.url,
        blurb=item.blurb,
        read_time=item.read_time,
        is_github_repo=item.is_github_repo,
        enriched=item.enriched,
        text=item.text,
        sources=[edition],
    )


def merge_sources(
    sources: list[SourceEdition],
    threshold: float = config.TITLE_SIMILARITY_THRESHOLD,
) -> list[CombinedItem]:
    """Merge editions into one list, collapsing stories carried more than once.

    Exact URL matches merge anywhere. Title similarity merges only *across*
    editions: within one newsletter, two similarly-titled items are two
    deliberate items, and TLDR does not run the same story twice in a day.
    """
    merged: list[CombinedItem] = []
    by_url: dict[str, CombinedItem] = {}
    url_dupes = title_dupes = 0

    for source in sources:
        for item in source.items:
            existing = by_url.get(item.url)
            if existing is not None:
                _merge_into(existing, source.edition, item)
                url_dupes += 1
                continue

            match = _best_title_match(merged, source.edition, item, threshold)
            if match is not None:
                log.info(
                    "merged %s duplicate: %r <- %r",
                    source.edition,
                    match.title,
                    item.title,
                )
                _merge_into(match, source.edition, item)
                by_url[item.url] = match
                title_dupes += 1
                continue

            combined = _as_combined(source.edition, item)
            merged.append(combined)
            by_url[item.url] = combined

    log.info(
        "combined %d items from %d editions (%d url duplicates, %d title duplicates)",
        len(merged),
        len(sources),
        url_dupes,
        title_dupes,
    )
    return merged


def _best_title_match(
    merged: list[CombinedItem],
    edition: str,
    item: Item,
    threshold: float,
) -> CombinedItem | None:
    best: CombinedItem | None = None
    best_score = threshold
    for candidate in merged:
        if edition in candidate.sources:
            continue
        score = title_similarity(candidate.title, item.title)
        if score >= best_score:
            best, best_score = candidate, score
    return best


def select_balanced(
    items: list[CombinedItem],
    editions: list[str],
    cap: int = config.BUNDLE_ITEM_CAP,
) -> list[CombinedItem]:
    """Round-robin across editions up to `cap`, preserving each one's order.

    Items carried by more than one edition are drawn from whichever source
    ran them first, so a widely-covered story is never postponed behind a
    single-source one.
    """
    if len(items) <= cap:
        return list(items)

    queues: dict[str, list[CombinedItem]] = {edition: [] for edition in editions}
    for item in items:
        primary = item.sources[0] if item.sources else editions[0]
        queues.setdefault(primary, []).append(item)

    chosen: list[CombinedItem] = []
    cursors = {edition: 0 for edition in queues}
    while len(chosen) < cap:
        progressed = False
        for edition in editions:
            queue = queues.get(edition, [])
            cursor = cursors[edition]
            if cursor >= len(queue):
                continue
            chosen.append(queue[cursor])
            cursors[edition] = cursor + 1
            progressed = True
            if len(chosen) == cap:
                break
        if not progressed:
            break

    # Restore the merged running order rather than the round-robin order.
    picked = {id(item) for item in chosen}
    selected = [item for item in items if id(item) in picked]
    log.info("selected %d of %d combined items", len(selected), len(items))
    return selected


def combine_editions(
    sources: list[SourceEdition],
    cap: int = config.BUNDLE_ITEM_CAP,
    threshold: float = config.TITLE_SIMILARITY_THRESHOLD,
) -> list[CombinedItem]:
    """Merge, deduplicate, and balance the day's sources into one running order."""
    merged = merge_sources(sources, threshold)
    return select_balanced(merged, [source.edition for source in sources], cap)


def coverage_lines(coverage: list[EditionCoverage]) -> list[str]:
    """One line per source, included or not. A missing source is never silent."""
    lines = []
    for record in coverage:
        if record.included:
            lines.append(f"- {record.name}: included — {record.item_count} items")
        else:
            reason = record.reason or "no edition was published"
            lines.append(f"- {record.name}: not included — {reason}")
    return lines


def coverage_summary(coverage: list[EditionCoverage]) -> str:
    return "Edition coverage:\n" + "\n".join(coverage_lines(coverage))
