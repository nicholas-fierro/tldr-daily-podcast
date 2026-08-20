"""The highest-value test in the project.

Sponsor filtering and item extraction, against a fixture. When TLDR changes
their markup, this is what tells you — before a silent episode does.

NOTE: the committed fixture is SYNTHETIC, written to the structure documented
in docs/HANDOFF.md. It proves the parser does what we intend; it does not
prove the intent matches the real page. Run scripts/capture_fixture.py to
capture a real edition and re-run these tests against it — until then, treat
a green run here as necessary but not sufficient.
"""

from pathlib import Path

import pytest

from src.parse import Item, ParseError, is_sponsored, normalize_url, parse_edition

FIXTURES = Path(__file__).parent / "fixtures"
SYNTHETIC = FIXTURES / "synthetic-edition.html"

# Point this at a captured edition once you have one; the tests below that use
# it will start running instead of skipping.
REAL_FIXTURE = next(iter(sorted(FIXTURES.glob("tldr-*.html"))), None)


@pytest.fixture
def items() -> list[Item]:
    return parse_edition(SYNTHETIC.read_text(encoding="utf-8"))


# --- sponsor filtering ----------------------------------------------------

def test_no_sponsored_items_survive(items):
    urls = " ".join(item.url for item in items)
    titles = " ".join(item.title.lower() for item in items)

    assert "advertise.tldr.tech" not in urls
    assert "ashbyhq.com" not in urls
    assert "(sponsor)" not in titles
    assert "sponsor" not in titles


def test_sponsor_slots_are_actually_present_in_the_fixture():
    """Guards the guard: a fixture with no ads would make the test above vacuous."""
    raw = SYNTHETIC.read_text(encoding="utf-8")
    assert "advertise.tldr.tech" in raw
    assert "jobs.ashbyhq.com" in raw
    assert "(Sponsor)" in raw


@pytest.mark.parametrize(
    "title,url,section,annotation",
    [
        ("Reach 5 Million Readers", "https://advertise.tldr.tech/x", "Big Tech", "Sponsor"),
        ("Some Product", "https://example.com/x", "Big Tech", "Sponsor"),
        ("Example Co Is Hiring", "https://jobs.ashbyhq.com/co/roles", "Quick Links", "1 minute read"),
        ("Anything", "https://example.com/x", "Sponsored Content", None),
        ("Deep Dive", "https://example.com/x", "Big Tech", "Sponsored by Acme"),
    ],
)
def test_is_sponsored_catches_each_shape(title, url, section, annotation):
    assert is_sponsored(title, url, section, annotation)


def test_is_sponsored_does_not_eat_real_items():
    assert not is_sponsored(
        "Chipmaker Acquires Startup", "https://www.theverge.com/x",
        "Big Tech & Startups", "4 minute read",
    )


# --- item extraction ------------------------------------------------------

def test_extracts_the_expected_items(items):
    titles = [item.title for item in items]
    assert "Chipmaker Acquires Inference Startup for $4B" in titles
    assert "Three Async Rust Pitfalls" in titles
    # 10 anchors in the fixture, minus 2 sponsors, minus 1 in-edition repeat
    assert len(items) == 7


def test_sections_are_preserved(items):
    by_title = {item.title: item.section for item in items}
    assert by_title["Chipmaker Acquires Inference Startup for $4B"] == "Big Tech & Startups"
    assert by_title["Solid-State Battery Hits 800 Cycles"] == "Science & Futuristic Technology"
    assert by_title["Discussion: The State of Package Managers"] == "Quick Links"


def test_read_time_is_parsed(items):
    by_title = {item.title: item.read_time for item in items}
    assert by_title["Chipmaker Acquires Inference Startup for $4B"] == 4
    assert by_title["Three Async Rust Pitfalls"] == 8


def test_github_repos_are_flagged_and_carry_no_read_time(items):
    repo = next(item for item in items if item.title == "example-tool")
    assert repo.is_github_repo
    assert repo.read_time is None


def test_blurbs_are_captured(items):
    battery = next(item for item in items if "Solid-State" in item.title)
    assert "eighty percent" in battery.blurb


def test_duplicate_url_within_one_edition_is_dropped(items):
    urls = [item.url for item in items]
    assert len(urls) == len(set(urls))


def test_annotation_is_stripped_from_titles(items):
    assert all("minute read" not in item.title for item in items)
    assert all("GitHub Repo" not in item.title for item in items)


# --- url normalization ----------------------------------------------------

@pytest.mark.parametrize(
    "raw,expected",
    [
        ("https://e.com/a?utm_source=tldrnewsletter", "https://e.com/a"),
        ("https://e.com/a?utm_source=x&utm_medium=y", "https://e.com/a"),
        ("https://e.com/a?sp=abc", "https://e.com/a"),
        ("https://e.com/a/", "https://e.com/a"),
        ("https://e.com/a?id=7&utm_source=x", "https://e.com/a?id=7"),
    ],
)
def test_normalize_url(raw, expected):
    assert normalize_url(raw) == expected


def test_normalization_makes_the_quick_links_repeat_collide():
    a = normalize_url("https://www.theverge.com/example-acquisition?utm_source=tldrnewsletter")
    b = normalize_url(
        "https://www.theverge.com/example-acquisition?utm_source=tldrnewsletter&utm_medium=quicklink"
    )
    assert a == b


# --- failure behavior -----------------------------------------------------

def test_empty_page_is_a_hard_failure():
    with pytest.raises(ParseError):
        parse_edition("<html><body><main><p>nothing here</p></main></body></html>")


def test_structure_change_surfaces_as_zero_items():
    """If TLDR moves to <div>-based items, we must fail loudly, not quietly."""
    html = '<html><body><main><div><a href="https://e.com/x">A Story (4 minute read)</a></div></main></body></html>'
    with pytest.raises(ParseError):
        parse_edition(html)


# --- real fixture, once one exists ----------------------------------------

@pytest.mark.skipif(REAL_FIXTURE is None, reason="no real edition captured yet")
def test_real_edition_parses_into_a_plausible_shape():
    items = parse_edition(REAL_FIXTURE.read_text(encoding="utf-8"))
    assert 8 <= len(items) <= 25, f"got {len(items)} items — structure may have shifted"
    assert all(item.url.startswith("https://") for item in items)
    assert all(item.title for item in items)
    assert not any("advertise.tldr.tech" in item.url for item in items)
    assert not any("ashbyhq" in item.url for item in items)
    assert sum(1 for item in items if item.blurb) >= len(items) * 0.8


def test_sponsor_on_an_ordinary_host_is_still_caught():
    """The disguised slot: a normal-looking URL with a (Sponsor) annotation.

    Regression test — an earlier version matched '(sponsor)' against a title
    whose parentheses had already been stripped, so this case slipped through
    whenever the host was not one of the known ad domains.
    """
    assert is_sponsored("Ship Faster With Acme", "https://acme.com/landing",
                        "Quick Links", "Sponsor")


def test_sponsor_wording_in_a_real_headline_is_not_a_false_positive():
    assert not is_sponsored("Sponsors Pull Out of Esports League",
                            "https://www.theverge.com/x", "Miscellaneous", "5 minute read")
