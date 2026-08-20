"""Enrichment: extraction quality gates and the blurb fallback.

The rule that matters here is that nothing fails the run and no item is ever
dropped — a failed fetch degrades to the newsletter blurb and gets marked so
the script writer hedges.
"""

import asyncio

import httpx
import pytest

from src import config, enrich
from src.parse import Item


def make_item(url="https://example.com/a", blurb="The newsletter summary.") -> Item:
    return Item(section="Big Tech", title="A Story", url=url, blurb=blurb)


def run(coro):
    return asyncio.run(coro)


async def enrich_with(handler, item: Item) -> Item:
    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, follow_redirects=True) as client:
        return await enrich._enrich_one(client, item, asyncio.Semaphore(5))


ARTICLE = (
    "<html><body><article>"
    + "<p>" + ("The inquiry focuses on how location data was resold. " * 40) + "</p>"
    + "</article></body></html>"
)


# --- paywall detection ----------------------------------------------------

def test_short_extraction_is_a_stub():
    assert enrich._is_paywall_stub("Too short.")


def test_marker_phrase_is_a_stub_even_when_long():
    text = "Subscribe to continue reading this article. " + ("filler text " * 200)
    assert enrich._is_paywall_stub(text)


def test_real_article_is_not_a_stub():
    assert not enrich._is_paywall_stub("Genuine reporting. " * 100)


# --- truncation -----------------------------------------------------------

def test_short_text_is_untouched():
    assert enrich._truncate("short") == "short"


def test_long_text_is_cut_to_the_limit():
    out = enrich._truncate("word " * 5_000)
    assert len(out) <= config.ARTICLE_CHAR_LIMIT


def test_truncation_prefers_a_sentence_boundary():
    text = ("Sentence one. " * 1_000)
    out = enrich._truncate(text)
    assert out.endswith(".")


# --- fallback behavior ----------------------------------------------------

def test_successful_fetch_marks_enriched():
    item = run(enrich_with(lambda request: httpx.Response(200, text=ARTICLE), make_item()))
    assert item.enriched
    assert "location data" in item.text


def test_404_falls_back_to_blurb():
    item = run(enrich_with(lambda request: httpx.Response(404, text="nope"), make_item()))
    assert not item.enriched
    assert item.text == "The newsletter summary."


def test_paywall_stub_falls_back_to_blurb():
    stub = "<html><body><p>Subscribe to continue reading.</p></body></html>"
    item = run(enrich_with(lambda request: httpx.Response(200, text=stub), make_item()))
    assert not item.enriched
    assert item.text == "The newsletter summary."


def test_timeout_falls_back_to_blurb_without_raising():
    def handler(request):
        raise httpx.ConnectTimeout("timed out", request=request)

    item = run(enrich_with(handler, make_item()))
    assert not item.enriched
    assert item.text == "The newsletter summary."


def test_item_is_never_dropped_on_failure():
    def handler(request):
        raise httpx.ConnectError("dns", request=request)

    item = run(enrich_with(handler, make_item()))
    assert item.url == "https://example.com/a"
    assert item.title == "A Story"


def test_server_error_is_retried_then_falls_back():
    calls = []

    def handler(request):
        calls.append(request.url)
        return httpx.Response(503, text="down")

    item = run(enrich_with(handler, make_item()))
    assert len(calls) == config.ENRICH_RETRIES + 1
    assert not item.enriched


def test_client_error_is_not_retried():
    calls = []

    def handler(request):
        calls.append(request.url)
        return httpx.Response(403, text="forbidden")

    run(enrich_with(handler, make_item()))
    assert len(calls) == 1


# --- github repos ---------------------------------------------------------

def test_github_repo_reads_the_readme_through_the_api():
    import base64

    seen = []

    def handler(request):
        seen.append(str(request.url))
        payload = {
            "encoding": "base64",
            "content": base64.b64encode(
                b"# example-tool\n\n![badge](https://img.shields.io/x)\n\n"
                + b"A single-binary log tailer with structured filtering. " * 20
            ).decode(),
        }
        return httpx.Response(200, json=payload)

    item = make_item(url="https://github.com/example-org/example-tool")
    item.is_github_repo = True
    result = run(enrich_with(handler, item))

    assert seen == ["https://api.github.com/repos/example-org/example-tool/readme"]
    assert result.enriched
    assert "log tailer" in result.text
    assert "img.shields.io" not in result.text  # badges stripped


# --- rate reporting -------------------------------------------------------

def test_enrichment_rate():
    items = [make_item() for _ in range(4)]
    items[0].enriched = items[1].enriched = True
    assert enrich.enrichment_rate(items) == 0.5
    assert enrich.enrichment_rate([]) == 0.0
