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
    mp3 = tmp_path / f"{edition.date}.mp3"
    mp3.write_bytes(b"audio")
    marker = tmp_path / ".email-state" / "sent"
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
    monkeypatch.setattr(main.script, "generate_script", lambda enriched, date: episode_script)
    monkeypatch.setattr(main.tts, "GeminiTTS", lambda: object())
    monkeypatch.setattr(main.tts, "render_segments", lambda generated, provider: [object()])
    monkeypatch.setattr(
        main.audio,
        "build_episode",
        lambda rendered, date, headline, workdir: (mp3, 430.0),
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

    def capture_delivery(path, date, headline, resolved):
        delivered.update(path=path, date=date, headline=headline, config=resolved)

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
    assert delivered["config"] == smtp
    assert marker.read_text(encoding="utf-8") == "2026-08-21\n"
    output = json.loads(capsys.readouterr().out)
    assert output["delivery"] == "email"
