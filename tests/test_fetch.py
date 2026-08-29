"""Exact-date fetching, and the one fetch outcome allowed to degrade.

A source that did not publish that day is omitted and reported. Every other
fetch failure fails the whole episode — better no episode than one silently
missing a newsletter, or one built from the wrong day's news.
"""

import httpx
import pytest

from src import config, fetch


DATED_PAGE = "<html><head><title>TLDR 2026-08-28</title></head><body></body></html>"


def fetch_with(handler, monkeypatch, **kwargs):
    """Route fetch_edition through a mock transport, without real sleeps."""
    monkeypatch.setattr(fetch.time, "sleep", lambda seconds: None)
    real_client = httpx.Client

    def client_factory(**client_kwargs):
        client_kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(**client_kwargs)

    monkeypatch.setattr(fetch.httpx, "Client", client_factory)
    return fetch.fetch_edition(**kwargs)


# --- page slugs -----------------------------------------------------------

def test_webdev_requests_the_dev_page():
    assert fetch.page_slug("webdev") == "dev"


def test_other_editions_use_their_own_slug():
    for edition in ("tech", "ai", "fintech", "infosec"):
        assert fetch.page_slug(edition) == edition


def test_dated_url_uses_the_page_slug(monkeypatch):
    requested = []

    def handler(request):
        requested.append(str(request.url))
        return httpx.Response(200, text=DATED_PAGE)

    edition = fetch_with(handler, monkeypatch, edition="webdev", date="2026-08-28")

    assert requested == ["https://tldr.tech/dev/2026-08-28"]
    assert edition.edition == "webdev"
    assert edition.date == "2026-08-28"


# --- the not-published case ----------------------------------------------

def test_unpublished_edition_redirecting_to_the_landing_page_degrades(monkeypatch):
    """Verified against tldr.tech 2026-08-28: an edition that does not exist
    307s to the undated landing page rather than returning 404."""
    def handler(request):
        if request.url.path == "/fintech/2026-08-28":
            return httpx.Response(307, headers={"Location": "https://tldr.tech/fintech"})
        return httpx.Response(200, text="<html><body>Latest fintech</body></html>")

    with pytest.raises(fetch.EditionNotPublished):
        fetch_with(handler, monkeypatch, edition="fintech", date="2026-08-28")


def test_a_different_dates_edition_is_never_substituted(monkeypatch):
    """The previous day's edition is not an acceptable stand-in."""
    def handler(request):
        if request.url.path == "/fintech/2026-08-28":
            return httpx.Response(
                307, headers={"Location": "https://tldr.tech/fintech/2026-08-27"}
            )
        return httpx.Response(
            200, text="<html><head><title>TLDR 2026-08-27</title></head></html>"
        )

    with pytest.raises(fetch.EditionNotPublished):
        fetch_with(handler, monkeypatch, edition="fintech", date="2026-08-28")


def test_dated_404_degrades(monkeypatch):
    with pytest.raises(fetch.EditionNotPublished):
        fetch_with(
            lambda request: httpx.Response(404),
            monkeypatch,
            edition="fintech",
            date="2026-08-28",
        )


def test_not_published_is_a_fetch_error_subclass():
    """So a single-edition run still fails on a missing edition."""
    assert issubclass(fetch.EditionNotPublished, fetch.FetchError)


# --- everything else stays fatal -----------------------------------------

def test_server_error_is_fatal_not_a_missing_edition(monkeypatch):
    with pytest.raises(fetch.FetchError) as caught:
        fetch_with(
            lambda request: httpx.Response(503),
            monkeypatch,
            edition="ai",
            date="2026-08-28",
        )
    assert not isinstance(caught.value, fetch.EditionNotPublished)


def test_transport_error_is_fatal_not_a_missing_edition(monkeypatch):
    def handler(request):
        raise httpx.ConnectError("connection refused")

    with pytest.raises(fetch.FetchError) as caught:
        fetch_with(handler, monkeypatch, edition="ai", date="2026-08-28")
    assert not isinstance(caught.value, fetch.EditionNotPublished)


def test_server_errors_are_retried(monkeypatch):
    attempts = []

    def handler(request):
        attempts.append(1)
        return httpx.Response(503)

    with pytest.raises(fetch.FetchError):
        fetch_with(handler, monkeypatch, edition="ai", date="2026-08-28")
    assert len(attempts) == config.FETCH_RETRIES


# --- resolving the latest edition ----------------------------------------

def test_latest_resolves_the_date_from_the_redirect(monkeypatch):
    def handler(request):
        if request.url.path == "/api/latest/tech":
            return httpx.Response(
                302, headers={"Location": "https://tldr.tech/tech/2026-08-28"}
            )
        return httpx.Response(200, text=DATED_PAGE)

    edition = fetch_with(handler, monkeypatch, edition="tech")
    assert edition.date == "2026-08-28"
    assert edition.url == "https://tldr.tech/tech/2026-08-28"


def test_an_undatable_latest_page_is_fatal(monkeypatch):
    with pytest.raises(fetch.FetchError):
        fetch_with(
            lambda request: httpx.Response(200, text="<html><body>hi</body></html>"),
            monkeypatch,
            edition="tech",
        )
