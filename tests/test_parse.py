"""Sponsor filtering and item extraction against a captured TLDR edition.

When TLDR changes its markup, this fixture should fail before a silent episode
or ad read reaches the feed.
"""

from pathlib import Path

import pytest

from src.parse import Item, ParseError, is_sponsored, normalize_url, parse_edition

FIXTURES = Path(__file__).parent / "fixtures"
REAL_FIXTURE = FIXTURES / "tldr-2026-08-21.html"


@pytest.fixture
def items() -> list[Item]:
    return parse_edition(REAL_FIXTURE.read_text(encoding="utf-8"))


# --- sponsor filtering ----------------------------------------------------

def test_no_sponsored_items_survive(items):
    urls = " ".join(item.url for item in items)
    titles = {item.title for item in items}

    assert "advertise.tldr.tech" not in urls
    assert "ashbyhq.com" not in urls
    assert "Reach 8 million tech professionals on TLDR" not in titles
    assert "Glean costs 75% less per task" not in titles
    assert "Reduce your AI defect rates and lower your token consumption up to 36%." not in titles
    assert "TLDR is hiring a curator for TLDR Product!" not in titles


def test_sponsor_slots_are_actually_present_in_the_fixture():
    """Guards the guard: a fixture with no ads would make the test above vacuous."""
    raw = REAL_FIXTURE.read_text(encoding="utf-8")
    assert "Reach 8 million tech professionals on TLDR (Sponsor)" in raw
    assert "Glean costs 75% less per task (Sponsor)" in raw
    assert "Reduce your AI defect rates and lower your token consumption up to 36%. (Sponsor)" in raw
    assert "advertise.tldr.tech" in raw


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
    assert "Anthropic Expects to Match or Top SpaceX's Record IPO Size" in titles
    assert "Waymo has designed a robocar chip to stay ahead of Tesla" in titles
    assert len(items) == 14


def test_sections_are_preserved(items):
    by_title = {item.title: item.section for item in items}
    assert by_title["Anthropic Expects to Match or Top SpaceX's Record IPO Size"] == "Big Tech & Startups"
    assert by_title["Tesla's Austin robotaxis are now fully driverless, tracking shows"] == "Science & Futuristic Technology"
    assert by_title["Better Batteries"] == "Programming, Design & Data Science"
    assert by_title["The End Of Open Source"] == "Miscellaneous"
    assert by_title["Waymo has designed a robocar chip to stay ahead of Tesla"] == "Quick Links"


def test_read_time_is_parsed(items):
    by_title = {item.title: item.read_time for item in items}
    assert by_title["Anthropic Expects to Match or Top SpaceX's Record IPO Size"] == 4
    assert by_title["The End Of Open Source"] == 20


def test_github_repos_are_flagged_and_carry_no_read_time():
    html = """
    <main>
      <h3>Programming, Design &amp; Data Science</h3>
      <a href="https://github.com/example/tool?utm_source=tldrnewsletter">
        <h3>example-tool (GitHub Repo)</h3>
      </a>
      <div>Repository summary.</div>
    </main>
    """
    [repo] = parse_edition(html)
    assert repo.is_github_repo
    assert repo.read_time is None
    assert repo.blurb == "Repository summary."


def test_blurbs_are_captured(items):
    batteries = next(item for item in items if item.title == "Better Batteries")
    assert "Python's stdlib" in batteries.blurb


def test_duplicate_url_within_one_edition_is_dropped():
    html = """
    <main>
      <h3>Quick Links</h3>
      <h4><a href="https://example.com/story?utm_source=tldr">Story (4 minute read)</a></h4>
      <p>First summary.</p>
      <h4><a href="https://example.com/story?utm_medium=quicklink">Story again (1 minute read)</a></h4>
      <p>Repeated summary.</p>
    </main>
    """
    items = parse_edition(html)
    assert len(items) == 1


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


# --- real fixture integrity ------------------------------------------------
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
