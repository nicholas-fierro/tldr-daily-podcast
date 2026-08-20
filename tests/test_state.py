"""Dedup window, freshness guard, idempotency."""

from datetime import date

import pytest

from src import state
from src.parse import Item


def item(url: str, title: str = "T") -> Item:
    return Item(section="S", title=title, url=url, blurb="b")


TODAY = date(2026, 8, 20)


# --- dedup ----------------------------------------------------------------

def test_repeat_inside_the_window_is_dropped():
    seen = {"https://e.com/a": "2026-08-18"}  # 2 days ago
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


def test_item_first_seen_today_is_kept():
    """A re-run of today's own edition must not delete its own episode's items."""
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


# --- freshness ------------------------------------------------------------

def test_todays_edition_is_fresh():
    assert state.is_fresh("2026-08-20", TODAY)


def test_yesterdays_edition_is_stale():
    assert not state.is_fresh("2026-08-19", TODAY)


# --- idempotency ----------------------------------------------------------

class FakeClient:
    def __init__(self, exists: bool = True, error: Exception | None = None):
        self.exists, self.error = exists, error
        self.saved: dict[str, bytes] = {}

    def head_object(self, Bucket, Key):
        if self.error:
            raise self.error
        if not self.exists:
            raise Exception("An error occurred (404) when calling HeadObject: Not Found")
        return {"ContentLength": 1}

    def put_object(self, Bucket, Key, Body, ContentType=None):
        self.saved[Key] = Body

    def get_object(self, Bucket, Key):
        raise Exception("NoSuchKey")


def test_existing_episode_is_detected():
    assert state.episode_exists(FakeClient(exists=True), "b", "2026-08-20")


def test_missing_episode_is_detected():
    assert not state.episode_exists(FakeClient(exists=False), "b", "2026-08-20")


def test_inconclusive_check_fails_open():
    """A flaky HEAD must not silently skip a day's episode."""
    client = FakeClient(error=Exception("connection reset"))
    assert not state.episode_exists(client, "b", "2026-08-20")


# --- state persistence ----------------------------------------------------

def test_missing_state_starts_a_fresh_window():
    assert state.load_seen(FakeClient(), "b") == {}


def test_save_prunes_beyond_the_retention_horizon():
    import json

    client = FakeClient()
    seen = {"https://e.com/old": "2026-07-01", "https://e.com/new": "2026-08-19"}
    state.save_seen(client, "b", seen, TODAY)
    written = json.loads(client.saved["state/seen-urls.json"])
    assert "https://e.com/new" in written
    assert "https://e.com/old" not in written
