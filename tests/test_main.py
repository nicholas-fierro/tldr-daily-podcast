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
        lambda enriched, date, *, edition, sources=None: episode_script,
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

    def capture_delivery(path, date, headline, resolved, *, edition, coverage=None):
        delivered.update(
            path=path,
            date=date,
            headline=headline,
            config=resolved,
            edition=edition,
            coverage=coverage,
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


# --- combined bundle runs -------------------------------------------------

def bundle_args(tmp_path, stage="combine", **overrides):
    defaults = dict(
        stage=stage,
        local=str(tmp_path),
        edition="tech",
        bundle="daily",
        date="2026-08-28",
        force=True,
        no_upload=False,
        email=False,
        email_marker=None,
    )
    defaults.update(overrides)
    return Namespace(**defaults)


def edition_page(edition, date="2026-08-28"):
    return Edition(
        date=date,
        url=f"https://tldr.tech/{edition}/{date}",
        html=f"<html>{edition}</html>",
        edition=edition,
    )


def stub_sources(monkeypatch, published, items_by_edition=None):
    """Serve only `published` editions; the rest report as not published."""
    requested = []

    def fake_fetch(*, edition, date=None):
        requested.append((edition, date))
        if edition not in published:
            raise main.fetch.EditionNotPublished(
                f"no {edition} edition was published for {date}"
            )
        return edition_page(edition)

    items_by_edition = items_by_edition or {
        edition: [
            Item(
                section="Big Tech & Startups",
                title=f"{edition} story",
                url=f"https://example.com/{edition}",
                blurb="Summary",
            )
        ]
        for edition in published
    }

    monkeypatch.setattr(main.fetch, "fetch_edition", fake_fetch)
    monkeypatch.setattr(
        main.parse,
        "parse_edition",
        lambda html: items_by_edition[html.removeprefix("<html>").removesuffix("</html>")],
    )
    return requested


def test_bundle_fetches_every_source_for_one_target_date(monkeypatch, tmp_path, capsys):
    requested = stub_sources(monkeypatch, {"tech", "ai", "webdev", "fintech"})

    assert main.run(bundle_args(tmp_path)) == 0

    assert requested == [
        ("tech", "2026-08-28"),
        ("ai", "2026-08-28"),
        ("webdev", "2026-08-28"),
        ("fintech", "2026-08-28"),
    ]
    output = json.loads(capsys.readouterr().out)
    assert output["date"] == "2026-08-28"
    assert all(record["status"] == "included" for record in output["coverage"])


def test_missing_source_is_omitted_and_reported_not_fatal(monkeypatch, tmp_path, capsys):
    stub_sources(monkeypatch, {"tech", "ai", "webdev"})

    assert main.run(bundle_args(tmp_path)) == 0

    output = json.loads(capsys.readouterr().out)
    coverage = {record["edition"]: record for record in output["coverage"]}
    assert coverage["fintech"]["status"] == "not_published"
    assert "2026-08-28" in coverage["fintech"]["reason"]
    assert coverage["tech"]["status"] == "included"
    assert len(output["items"]) == 3


def test_a_source_failing_for_any_other_reason_fails_the_episode(monkeypatch, tmp_path):
    def fake_fetch(*, edition, date=None):
        if edition == "ai":
            raise main.fetch.FetchError("tldr.tech returned 503")
        return edition_page(edition)

    monkeypatch.setattr(main.fetch, "fetch_edition", fake_fetch)

    with pytest.raises(main.fetch.FetchError):
        main.run(bundle_args(tmp_path))


def test_a_source_parsing_to_zero_items_fails_the_episode(monkeypatch, tmp_path):
    stub_sources(monkeypatch, {"tech", "ai", "webdev", "fintech"})

    def fail_on_ai(html):
        if "ai" in html:
            raise main.parse.ParseError("0 items parsed")
        return [Item(section="s", title="t", url="https://example.com/a", blurb="b")]

    monkeypatch.setattr(main.parse, "parse_edition", fail_on_ai)

    with pytest.raises(main.parse.ParseError):
        main.run(bundle_args(tmp_path))


def test_the_anchor_edition_fixes_the_target_date(monkeypatch, tmp_path, capsys):
    """With no date given, every source is requested for the anchor's date."""
    requested = []

    def fake_fetch(*, edition, date=None):
        requested.append((edition, date))
        if date is None:
            return edition_page(edition, "2026-08-28")
        if date != "2026-08-28":
            raise AssertionError(f"{edition} requested for {date}")
        return edition_page(edition, date)

    monkeypatch.setattr(main.fetch, "fetch_edition", fake_fetch)
    monkeypatch.setattr(
        main.parse,
        "parse_edition",
        lambda html: [Item(section="s", title="t", url=f"https://e.com/{html}", blurb="b")],
    )

    assert main.run(bundle_args(tmp_path, date=None, resolved_date=None)) == 0

    assert requested[0] == ("tech", None)
    assert [date for _, date in requested[1:]] == ["2026-08-28"] * 3


def test_bundle_identity_is_used_for_delivery(monkeypatch, tmp_path, capsys):
    stub_sources(monkeypatch, {"tech", "ai", "webdev"})
    episode_script = main.script.Script(
        date="2026-08-28",
        segments=[
            main.script.Segment(
                topic="example",
                lines=[main.script.Line(speaker="Ava", text="Example story")],
            )
        ],
    )
    mp3 = tmp_path / "daily-2026-08-28.mp3"
    mp3.write_bytes(b"audio")
    marker = tmp_path / ".email-state" / "daily-2026-08-28.sent"
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
    scripted = {}

    monkeypatch.setattr(main.enrich, "enrich_items", lambda parsed: parsed)
    monkeypatch.setattr(main.enrich, "enrichment_rate", lambda enriched: 1.0)

    def capture_script(items, date, *, edition, sources=None):
        scripted.update(items=items, date=date, edition=edition, sources=sources)
        return episode_script

    monkeypatch.setattr(main.script, "generate_script", capture_script)
    monkeypatch.setattr(main.tts, "GeminiTTS", lambda: object())
    monkeypatch.setattr(main.tts, "render_segments", lambda generated, provider: [object()])
    monkeypatch.setattr(
        main.audio,
        "build_episode",
        lambda rendered, date, headline, workdir, *, edition: (mp3, 600.0),
    )
    monkeypatch.setattr(main.config, "smtp_config", lambda: smtp)

    def capture_delivery(path, date, headline, resolved, *, edition, coverage=None):
        delivered.update(headline=headline, edition=edition, coverage=coverage)

    monkeypatch.setattr(main.email_delivery, "send_episode", capture_delivery)

    args = bundle_args(tmp_path, stage="publish", email=True, email_marker=str(marker))
    assert main.run(args) == 0

    assert scripted["edition"] == "daily"
    assert scripted["sources"] == ["tech", "ai", "webdev"]
    assert delivered["edition"] == "daily"
    assert delivered["headline"] == "TLDR Daily — 2026-08-28"
    assert [record.edition for record in delivered["coverage"]] == [
        "tech", "ai", "webdev", "fintech",
    ]
    assert marker.read_text(encoding="utf-8") == "daily:2026-08-28\n"


def test_bundle_snapshots_stay_source_qualified(monkeypatch, tmp_path):
    stub_sources(monkeypatch, {"tech", "ai", "webdev"})

    assert main.run(bundle_args(tmp_path)) == 0

    written = sorted(path.name for path in tmp_path.glob("*.html"))
    assert written == [
        "ai-2026-08-28.html",
        "tech-2026-08-28.html",
        "webdev-2026-08-28.html",
    ]


def test_bundle_and_edition_are_mutually_exclusive():
    with pytest.raises(SystemExit):
        main.main(["--bundle", "daily", "--edition", "tech"])


def test_single_edition_runs_are_unchanged(monkeypatch, tmp_path, capsys):
    """The bundle path must not disturb per-edition debugging runs."""
    monkeypatch.setattr(
        main.fetch, "fetch_edition", lambda **kwargs: edition_page("tech")
    )
    monkeypatch.setattr(
        main.parse,
        "parse_edition",
        lambda html: [Item(section="s", title="t", url="https://e.com/a", blurb="b")],
    )

    args = bundle_args(tmp_path, stage="parse", bundle=None)
    assert main.run(args) == 0

    output = json.loads(capsys.readouterr().out)
    assert isinstance(output, list)
    assert output[0]["title"] == "t"


def test_a_stale_edition_stops_before_fetching_other_sources(monkeypatch, tmp_path):
    """Guards run before work: a no-op run costs one fetch, not four."""
    requested = []

    def fake_fetch(*, edition, date=None):
        requested.append(edition)
        return edition_page(edition, "2026-08-10")

    monkeypatch.setattr(main.fetch, "fetch_edition", fake_fetch)
    monkeypatch.setattr(main.state, "is_recent", lambda date: False)
    monkeypatch.setattr(
        main.parse,
        "parse_edition",
        lambda html: pytest.fail("a stale edition must stop before parse"),
    )

    args = bundle_args(tmp_path, date=None, resolved_date="2026-08-10", force=False)
    assert main.run(args) == 0
    assert requested == ["tech"]


def test_an_existing_episode_stops_before_fetching_other_sources(monkeypatch, tmp_path):
    requested = []

    def fake_fetch(*, edition, date=None):
        requested.append(edition)
        return edition_page(edition)

    r2 = main.config.R2Config(
        account_id="acct",
        access_key_id="key",
        secret_access_key="secret",
        bucket="episodes",
        public_base_url="https://media.example.com",
        feed_token="0" * 32,
    )
    monkeypatch.setattr(main.fetch, "fetch_edition", fake_fetch)
    monkeypatch.setattr(main.config, "r2_config", lambda: r2)
    monkeypatch.setattr(main.publish, "r2_client", lambda cfg: object())
    monkeypatch.setattr(
        main.state, "episode_exists", lambda client, bucket, edition, date: True
    )
    monkeypatch.setattr(
        main.parse,
        "parse_edition",
        lambda html: pytest.fail("an existing episode must stop before parse"),
    )

    args = bundle_args(tmp_path, stage="publish", force=False)
    assert main.run(args) == 0
    assert requested == ["tech"]
