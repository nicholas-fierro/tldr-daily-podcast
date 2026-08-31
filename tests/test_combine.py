"""Merging several editions of one date into one running order."""

import pytest

from src import combine, config
from src.parse import Item


def item(title, url, section="Big Tech & Startups", blurb="Summary", read_time=5):
    return Item(
        section=section, title=title, url=url, blurb=blurb, read_time=read_time
    )


def source(edition, items, date="2026-08-28"):
    return combine.SourceEdition(edition=edition, date=date, items=items)


def test_bundle_editions_names_its_sources():
    assert combine.bundle_editions("daily") == ("tech", "ai", "webdev", "fintech")


def test_unknown_bundle_is_rejected():
    with pytest.raises(KeyError, match="unknown bundle"):
        combine.bundle_editions("nope")


def test_exact_url_duplicates_merge_across_editions():
    combined = combine.merge_sources([
        source("tech", [item("Anthropic ships a thing", "https://example.com/a")]),
        source("ai", [item("Anthropic ships a thing", "https://example.com/a")]),
    ])

    assert len(combined) == 1
    assert combined[0].sources == ["tech", "ai"]


def test_tracking_parameters_do_not_defeat_url_dedup():
    """normalize_url runs in the parser, so both editions arrive already clean."""
    from src.parse import normalize_url

    left = normalize_url("https://example.com/a?utm_source=tldrnewsletter")
    right = normalize_url("https://example.com/a?utm_source=tldrai&utm_medium=email")
    assert left == right

    combined = combine.merge_sources([
        source("tech", [item("A story", left)]),
        source("ai", [item("A story", right)]),
    ])
    assert len(combined) == 1
    assert combined[0].sources == ["tech", "ai"]


def test_strong_title_similarity_merges_across_editions():
    combined = combine.merge_sources([
        source("tech", [item("OpenAI acquires Statsig for 1.1 billion",
                             "https://tech.example.com/openai")]),
        source("ai", [item("OpenAI acquires Statsig for $1.1B",
                           "https://ai.example.com/openai")]),
    ])

    assert len(combined) == 1
    merged = combined[0]
    assert merged.sources == ["tech", "ai"]
    assert merged.url == "https://tech.example.com/openai"
    assert merged.related_urls == ["https://ai.example.com/openai"]


def test_distinct_stories_stay_separate():
    combined = combine.merge_sources([
        source("tech", [item("Apple ships a new modem chip",
                             "https://example.com/apple")]),
        source("ai", [item("Meta reorganizes its superintelligence lab",
                           "https://example.com/meta")]),
    ])

    assert len(combined) == 2


def test_similar_titles_within_one_edition_are_not_merged():
    """TLDR does not run the same story twice in one newsletter; two similar
    titles there are two deliberate items."""
    combined = combine.merge_sources([
        source("ai", [
            item("Gemini 3 benchmark results are out", "https://example.com/one"),
            item("Gemini 3 benchmark results analyzed", "https://example.com/two"),
        ]),
    ])

    assert len(combined) == 2


def test_merge_keeps_the_fuller_blurb_and_a_read_time():
    combined = combine.merge_sources([
        source("tech", [
            Item(section="s", title="A shared story", url="https://example.com/a",
                 blurb="Short.", read_time=None),
        ]),
        source("ai", [
            Item(section="s", title="A shared story", url="https://example.com/a",
                 blurb="A considerably longer writeup of the same story.", read_time=7),
        ]),
    ])

    assert combined[0].blurb == "A considerably longer writeup of the same story."
    assert combined[0].read_time == 7


def test_combined_items_carry_source_labels_downstream():
    combined = combine.merge_sources([source("webdev", [item("A", "https://e.com/a")])])
    assert combined[0].sources == ["webdev"]
    assert combined[0].to_dict()["sources"] == ["webdev"]


def test_title_similarity_ignores_stopwords():
    assert combine.title_similarity("The state of the art", "A state of an art") == 1.0
    assert combine.title_similarity("Apple ships silicon", "Meta hires staff") == 0.0


def test_selection_is_balanced_across_editions():
    sources = [
        source("tech", [item(f"Tech story {n}", f"https://tech.example.com/{n}")
                        for n in range(10)]),
        source("ai", [item(f"AI story {n}", f"https://ai.example.com/{n}")
                      for n in range(10)]),
        source("webdev", [item(f"Dev story {n}", f"https://dev.example.com/{n}")
                          for n in range(10)]),
    ]
    combined = combine.combine_editions(sources, cap=9)

    assert len(combined) == 9
    per_edition = {"tech": 0, "ai": 0, "webdev": 0}
    for chosen in combined:
        per_edition[chosen.sources[0]] += 1
    assert per_edition == {"tech": 3, "ai": 3, "webdev": 3}


def test_selection_under_the_cap_keeps_everything():
    sources = [source("tech", [item(f"S{n}", f"https://e.com/{n}") for n in range(4)])]
    assert len(combine.combine_editions(sources, cap=28)) == 4


def test_a_short_edition_does_not_waste_its_share():
    """One source running dry gives its slots to the others rather than
    shrinking the episode."""
    sources = [
        source("tech", [item(f"T{n}", f"https://t.example.com/{n}") for n in range(8)]),
        source("ai", [item("Only one", "https://ai.example.com/1")]),
    ]
    combined = combine.combine_editions(sources, cap=6)
    assert len(combined) == 6


def test_selection_preserves_merged_running_order():
    sources = [
        source("tech", [item(f"T{n}", f"https://t.example.com/{n}") for n in range(4)]),
        source("ai", [item(f"A{n}", f"https://a.example.com/{n}") for n in range(4)]),
    ]
    combined = combine.combine_editions(sources, cap=4)
    titles = [c.title for c in combined]
    assert titles == sorted(titles, key=lambda t: (t[0] != "T", t))


def test_coverage_summary_reports_included_and_missing_sources():
    coverage = [
        combine.EditionCoverage("tech", combine.INCLUDED, 14),
        combine.EditionCoverage("ai", combine.INCLUDED, 18),
        combine.EditionCoverage("webdev", combine.INCLUDED, 15),
        combine.EditionCoverage(
            "fintech",
            combine.NOT_PUBLISHED,
            0,
            "no 2026-08-28 edition was published",
        ),
    ]
    summary = combine.coverage_summary(coverage)

    assert "- Tech: included — 14 items" in summary
    assert "- AI: included — 18 items" in summary
    assert "- Web Dev: included — 15 items" in summary
    assert (
        "- Fintech: not included — no 2026-08-28 edition was published" in summary
    )


def test_every_bundle_source_has_a_display_name():
    for edition in combine.bundle_editions("daily"):
        assert edition in config.EDITION_NAMES
