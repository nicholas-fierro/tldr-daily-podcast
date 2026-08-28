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
        email=False,
        email_marker=None,
    )

    assert main.run(args) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["total"] == 1
    assert output["enrichment_rate"] == 0.0


def test_ci_resolved_date_is_pinned_without_bypassing_recency(monkeypatch, tmp_path):
    edition = Edition(
        date="2026-08-10",
        url="https://tldr.tech/ai/2026-08-10",
        html="<html></html>",
        edition="ai",
    )
    fetched = {}

    def fake_fetch(**kwargs):
        fetched.update(kwargs)
        return edition

    monkeypatch.setattr(main.fetch, "fetch_edition", fake_fetch)
    monkeypatch.setattr(main.state, "is_recent", lambda date: False)
    monkeypatch.setattr(
        main.parse,
        "parse_edition",
        lambda html: pytest.fail("stale resolved edition must stop before parse"),
    )

    args = Namespace(
        stage="parse",
        local=str(tmp_path),
        edition="ai",
        date=None,
        resolved_date="2026-08-10",
        force=False,
        no_upload=False,
        email=False,
        email_marker=None,
    )

    assert main.run(args) == 0
    assert fetched == {"edition": "ai", "date": "2026-08-10"}


def test_email_delivery_never_loads_r2(monkeypatch, tmp_path, capsys):
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
    episode_script = main.script.Script(
        date=edition.date,
        segments=[
            main.script.Segment(
                topic="example",
                lines=[main.script.Line(speaker="Ava", text="Example story")],
            )
        ],
    )
    mp3 = tmp_path / f"{edition.edition}-{edition.date}.mp3"
    mp3.write_bytes(b"audio")
    marker = tmp_path / ".email-state" / "tech-2026-08-21.sent"
    smtp = main.config.SMTPConfig(
        host="smtp.example.com",
        port=465,
        username="sender@example.com",
        password="secret",
        sender="sender@example.com",
        recipient="owner@example.com",
        use_ssl=True,
    )
    delivered = {}

    monkeypatch.setattr(main.fetch, "fetch_edition", lambda **kwargs: edition)
    monkeypatch.setattr(main.parse, "parse_edition", lambda html: items)
    monkeypatch.setattr(main.enrich, "enrich_items", lambda parsed: parsed)
    monkeypatch.setattr(main.enrich, "enrichment_rate", lambda enriched: 1.0)
    monkeypatch.setattr(
        main.script,
        "generate_script",
        lambda enriched, date, *, edition: episode_script,
    )
    monkeypatch.setattr(main.tts, "GeminiTTS", lambda: object())
    monkeypatch.setattr(main.tts, "render_segments", lambda generated, provider: [object()])
    monkeypatch.setattr(
        main.audio,
        "build_episode",
        lambda rendered, date, headline, workdir, *, edition: (mp3, 430.0),
    )
    monkeypatch.setattr(main.config, "smtp_config", lambda: smtp)
    monkeypatch.setattr(
        main.config,
        "r2_config",
        lambda: pytest.fail("email delivery must not load R2 credentials"),
    )
    monkeypatch.setattr(
        main.publish,
        "upload_episode",
        lambda *args, **kwargs: pytest.fail("email delivery must not upload to R2"),
    )

    def capture_delivery(path, date, headline, resolved, *, edition):
        delivered.update(
            path=path,
            date=date,
            headline=headline,
            config=resolved,
            edition=edition,
        )

    monkeypatch.setattr(main.email_delivery, "send_episode", capture_delivery)

    args = Namespace(
        stage="publish",
        local=str(tmp_path),
        edition="tech",
        date="2026-08-21",
        force=True,
        no_upload=False,
        email=True,
        email_marker=str(marker),
    )

    assert main.run(args) == 0
    assert delivered["path"] == mp3
    assert delivered["date"] == edition.date
    assert delivered["edition"] == "tech"
    assert delivered["headline"] == "TLDR Daily TECH — 2026-08-21"
    assert delivered["config"] == smtp
    assert marker.read_text(encoding="utf-8") == "tech:2026-08-21\n"
    assert (tmp_path / "tech-2026-08-21.html").exists()
    output = json.loads(capsys.readouterr().out)
    assert output["edition"] == "tech"
    assert output["delivery"] == "email"
