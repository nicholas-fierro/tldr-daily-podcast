"""CLI stage-boundary behavior."""

from argparse import Namespace
import json

import pytest

import main
from src.fetch import Edition
from src.parse import Item


def test_enrich_checkpoint_does_not_require_r2(monkeypatch, tmp_path, capsys):
    edition = Edition(
        date="2026-08-21",
        url="https://tldr.tech/tech/2026-08-21",
        html="<html></html>",
        edition="tech",
    )
    items = [
        Item(
            section="Big Tech & Startups",
            title="Example",
            url="https://example.com/story",
            blurb="Summary",
        )
    ]

    monkeypatch.setattr(main.fetch, "fetch_edition", lambda **kwargs: edition)
    monkeypatch.setattr(main.parse, "parse_edition", lambda html: items)
    monkeypatch.setattr(main.enrich, "enrich_items", lambda parsed: parsed)
    monkeypatch.setattr(main.enrich, "enrichment_rate", lambda enriched: 0.0)
    monkeypatch.setattr(
        main.config,
        "r2_config",
        lambda: pytest.fail("enrich checkpoint must not load R2 credentials"),
    )

    args = Namespace(
        stage="enrich",
        local=str(tmp_path),
        edition="tech",
        date=None,
        force=True,
        no_upload=False,
    )

    assert main.run(args) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["total"] == 1
    assert output["enrichment_rate"] == 0.0
