"""Feed generation. Pure, so it is fully testable without R2."""

from datetime import datetime, timezone
from xml.etree import ElementTree

import pytest

from src import config, publish
from src.config import R2Config
from src.parse import Item

ITUNES = "{http://www.itunes.com/dtds/podcast-1.0.dtd}"

CFG = R2Config(
    account_id="acct", access_key_id="k", secret_access_key="s", bucket="bkt",
    public_base_url="https://media.example.com", feed_token="0123456789abcdef0123456789abcdef",
)


def episode(date="2026-08-20", edition="tech", **kwargs) -> publish.Episode:
    defaults = dict(
        title=f"TLDR Daily {edition.upper()} — {date}",
        url=f"https://media.example.com/episodes/{edition}/{date}.mp3",
        size_bytes=5_012_345,
        duration_s=612.4,
        description="Today's stories:\n\nA Story\nhttps://e.com/a",
    )
    return publish.Episode(edition=edition, date=date, **{**defaults, **kwargs})


@pytest.fixture
def feed() -> ElementTree.Element:
    xml = publish.build_feed([episode("2026-08-18"), episode("2026-08-20")], CFG)
    return ElementTree.fromstring(xml)


# --- validity -------------------------------------------------------------

def test_feed_is_well_formed_rss_2(feed):
    assert feed.tag == "rss"
    assert feed.get("version") == "2.0"
    assert feed.find("channel") is not None


def test_channel_carries_the_itunes_basics(feed):
    channel = feed.find("channel")
    assert channel.find(f"{ITUNES}author").text == config.PODCAST_AUTHOR
    assert channel.find(f"{ITUNES}category").get("text") == "Technology"
    assert channel.find(f"{ITUNES}explicit").text == "false"


def test_personal_feed_is_blocked_from_directories(feed):
    assert feed.find(f"channel/{ITUNES}block").text == "Yes"


# --- enclosures -----------------------------------------------------------

def test_enclosure_has_url_length_and_type(feed):
    enclosure = feed.find("channel/item/enclosure")
    assert enclosure.get("url").endswith(".mp3")
    assert enclosure.get("type") == "audio/mpeg"
    assert int(enclosure.get("length")) == 5_012_345


def test_enclosure_url_is_absolute(feed):
    for enclosure in feed.findall("channel/item/enclosure"):
        assert enclosure.get("url").startswith("https://")


# --- ordering and identity ------------------------------------------------

def test_newest_episode_is_first(feed):
    titles = [item.find("title").text for item in feed.findall("channel/item")]
    assert titles[0].endswith("2026-08-20")


def test_guids_are_stable_and_unique(feed):
    guids = [item.find("guid").text for item in feed.findall("channel/item")]
    assert guids == [
        "tldr-daily-tech-2026-08-20",
        "tldr-daily-tech-2026-08-18",
    ]
    assert len(set(guids)) == 2


def test_guids_are_unique_across_editions_on_the_same_date():
    episodes = [episode(edition="tech"), episode(edition="ai")]
    assert {item.guid for item in episodes} == {
        "tldr-daily-tech-2026-08-20",
        "tldr-daily-ai-2026-08-20",
    }


def test_guid_is_not_a_permalink(feed):
    assert feed.find("channel/item/guid").get("isPermaLink") == "false"


def test_pubdate_is_rfc822(feed):
    from email.utils import parsedate_to_datetime

    text = feed.find("channel/item/pubDate").text
    assert parsedate_to_datetime(text).date().isoformat() == "2026-08-20"


def test_duration_is_hhmmss(feed):
    assert feed.find(f"channel/item/{ITUNES}duration").text == "00:10:12"


@pytest.mark.parametrize(
    "seconds,expected",
    [(0, "00:00:00"), (59.6, "00:01:00"), (612.4, "00:10:12"), (3_661, "01:01:01")],
)
def test_format_duration(seconds, expected):
    assert publish.format_duration(seconds) == expected


# --- show notes -----------------------------------------------------------

def test_description_lists_titles_and_urls():
    items = [
        Item(section="S", title="First Story", url="https://e.com/1", blurb=""),
        Item(section="S", title="Second Story", url="https://e.com/2", blurb=""),
    ]
    description = publish.build_description(items)
    assert "First Story" in description
    assert "https://e.com/2" in description


def test_description_survives_xml_escaping():
    items = [Item(section="S", title="AT&T <buys> Something", url="https://e.com/1", blurb="")]
    xml = publish.build_feed([episode(description=publish.build_description(items))], CFG)
    parsed = ElementTree.fromstring(xml)  # would raise if escaping were wrong
    assert "AT&T <buys> Something" in parsed.find("channel/item/description").text


def test_ampersand_in_title_is_escaped():
    xml = publish.build_feed([episode(title="Big Tech & Startups Recap")], CFG)
    assert ElementTree.fromstring(xml).find("channel/item/title").text == (
        "Big Tech & Startups Recap"
    )


# --- feed location --------------------------------------------------------

def test_feed_key_is_unguessable():
    assert CFG.feed_key == "feed-0123456789abcdef0123456789abcdef.xml"


def test_feed_url_joins_cleanly():
    cfg = R2Config("a", "k", "s", "b", "https://media.example.com/", "tok")
    assert cfg.feed_url == "https://media.example.com/feed-tok.xml"


def test_episode_url_contains_edition_and_date():
    assert CFG.episode_url("ai", "2026-08-20") == (
        "https://media.example.com/episodes/ai/2026-08-20.mp3"
    )


def test_persistent_object_keys_are_edition_scoped():
    assert config.EPISODE_KEY.format(edition="ai", date="2026-08-20") == (
        "episodes/ai/2026-08-20.mp3"
    )
    assert config.SCRIPT_KEY.format(edition="ai", date="2026-08-20") == (
        "scripts/ai/2026-08-20.json"
    )
    assert config.SNAPSHOT_KEY.format(edition="ai", date="2026-08-20") == (
        "snapshots/ai/2026-08-20.html"
    )
    assert publish.META_KEY.format(edition="ai", date="2026-08-20") == (
        "meta/ai/2026-08-20.json"
    )


def test_combined_episodes_use_the_bundle_identity():
    """A bundle is one persistent identity like any edition, so episode,
    script, and metadata keys all hang off `daily/<date>`."""
    assert config.EPISODE_KEY.format(edition="daily", date="2026-08-28") == (
        "episodes/daily/2026-08-28.mp3"
    )
    assert config.SCRIPT_KEY.format(edition="daily", date="2026-08-28") == (
        "scripts/daily/2026-08-28.json"
    )
    assert publish.META_KEY.format(edition="daily", date="2026-08-28") == (
        "meta/daily/2026-08-28.json"
    )
    assert config.DEDUP_STATE_KEY.format(edition="daily") == (
        "state/daily/seen-urls.json"
    )
    assert CFG.episode_url("daily", "2026-08-28") == (
        "https://media.example.com/episodes/daily/2026-08-28.mp3"
    )
    assert episode(date="2026-08-28", edition="daily").guid == "tldr-daily-daily-2026-08-28"


def test_snapshots_stay_source_qualified_within_a_bundle():
    """When parsing breaks, the input that broke it belongs to one source."""
    for source in ("tech", "ai", "webdev", "fintech"):
        assert config.SNAPSHOT_KEY.format(edition=source, date="2026-08-28") == (
            f"snapshots/{source}/2026-08-28.html"
        )


def test_empty_feed_is_still_valid():
    parsed = ElementTree.fromstring(publish.build_feed([], CFG))
    assert parsed.findall("channel/item") == []


# --- retention ------------------------------------------------------------

class FakeClient:
    def __init__(self, keys):
        self.keys = list(keys)
        self.deleted = []

    def list_objects_v2(self, Bucket, Prefix, ContinuationToken=None):
        return {
            "Contents": [{"Key": key} for key in self.keys if key.startswith(Prefix)],
            "IsTruncated": False,
        }

    def delete_object(self, Bucket, Key):
        self.deleted.append(Key)


def test_prune_keeps_the_newest_n_per_edition():
    keys = [f"episodes/tech/2026-07-{day:02d}.mp3" for day in range(1, 21)]
    keys += ["episodes/ai/2026-07-01.mp3"]
    client = FakeClient(keys)
    publish.prune_old_episodes(client, CFG, "tech", keep=5)
    assert len(client.deleted) == 15
    assert "episodes/tech/2026-07-20.mp3" not in client.deleted
    assert "episodes/tech/2026-07-01.mp3" in client.deleted
    assert "episodes/ai/2026-07-01.mp3" not in client.deleted


def test_prune_is_a_noop_under_the_limit():
    client = FakeClient(["episodes/tech/2026-07-01.mp3"])
    publish.prune_old_episodes(client, CFG, "tech", keep=30)
    assert client.deleted == []
