"""Dedup window, recency guard, idempotency."""

from datetime import date
import json

from src import state
from src.parse import Item


def item(url: str, title: str = "T") -> Item:
    return Item(section="S", title=title, url=url, blurb="b")


TODAY = date(2026, 8, 20)


# --- dedup ----------------------------------------------------------------

def test_repeat_inside_the_window_is_dropped():
    seen = {"https://e.com/a": "2026-08-18"}
    kept, dropped = state.filter_recent_duplicates([item("https://e.com/a")], seen, TODAY)
    assert kept == []
    assert len(dropped) == 1


def test_repeat_outside_the_window_is_kept():
    seen = {"https://e.com/a": "2026-08-10"}
    kept, dropped = state.filter_recent_duplicates([item("https://e.com/a")], seen, TODAY)
    assert len(kept) == 1
    assert dropped == []


def test_unseen_url_is_kept():
    kept, dropped = state.filter_recent_duplicates([item("https://e.com/new")], {}, TODAY)
    assert len(kept) == 1
    assert dropped == []


def test_item_first_seen_today_is_kept():
    seen = {"https://e.com/a": TODAY.isoformat()}
    kept, _ = state.filter_recent_duplicates([item("https://e.com/a")], seen, TODAY)
    assert len(kept) == 1


def test_record_seen_does_not_overwrite_first_seen_date():
    seen = {"https://e.com/a": "2026-08-01"}
    updated = state.record_seen(seen, [item("https://e.com/a")], TODAY)
    assert updated["https://e.com/a"] == "2026-08-01"


def test_record_seen_adds_new_urls():
    updated = state.record_seen({}, [item("https://e.com/a")], TODAY)
    assert updated == {"https://e.com/a": TODAY.isoformat()}


# --- recency --------------------------------------------------------------

def test_todays_edition_is_recent():
    assert state.is_recent("2026-08-20", TODAY)


def test_previous_edition_inside_lookback_is_recent():
    assert state.is_recent("2026-08-17", TODAY)


def test_edition_outside_lookback_is_stale():
    assert not state.is_recent("2026-08-16", TODAY)


def test_future_edition_is_not_recent():
    assert not state.is_recent("2026-08-21", TODAY)


# --- idempotency ----------------------------------------------------------

class FakeClient:
    def __init__(self, exists: bool = True, error: Exception | None = None):
        self.exists, self.error = exists, error
        self.saved: dict[str, bytes] = {}
        self.head_keys: list[str] = []

    def head_object(self, Bucket, Key):
        self.head_keys.append(Key)
        if self.error:
            raise self.error
        if not self.exists:
            raise Exception("An error occurred (404) when calling HeadObject: Not Found")
        return {"ContentLength": 1}

    def put_object(self, Bucket, Key, Body, ContentType=None):
        self.saved[Key] = Body

    def get_object(self, Bucket, Key):
        raise Exception("NoSuchKey")


def test_existing_episode_is_detected_by_edition_and_date():
    client = FakeClient(exists=True)
    assert state.episode_exists(client, "b", "ai", "2026-08-20")
    assert client.head_keys == ["episodes/ai/2026-08-20.mp3"]


def test_missing_episode_is_detected():
    assert not state.episode_exists(
        FakeClient(exists=False), "b", "tech", "2026-08-20"
    )


def test_inconclusive_check_fails_open():
    client = FakeClient(error=Exception("connection reset"))
    assert not state.episode_exists(client, "b", "tech", "2026-08-20")


# --- state persistence ----------------------------------------------------

def test_missing_state_starts_a_fresh_window():
    assert state.load_seen(FakeClient(), "b", "ai") == {}


def test_save_prunes_beyond_the_retention_horizon():
    client = FakeClient()
    seen = {"https://e.com/old": "2026-07-01", "https://e.com/new": "2026-08-19"}
    state.save_seen(client, "b", "ai", seen, TODAY)
    written = json.loads(client.saved["state/ai/seen-urls.json"])
    assert "https://e.com/new" in written
    assert "https://e.com/old" not in written


def test_dedup_state_is_isolated_by_edition():
    client = FakeClient()
    state.save_seen(client, "b", "tech", {"https://e.com/tech": "2026-08-20"}, TODAY)
    state.save_seen(client, "b", "ai", {"https://e.com/ai": "2026-08-20"}, TODAY)
    assert set(client.saved) == {
        "state/tech/seen-urls.json",
        "state/ai/seen-urls.json",
    }
