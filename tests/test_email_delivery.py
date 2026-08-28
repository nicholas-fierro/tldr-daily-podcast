"""SMTP delivery without network access."""

import smtplib

import pytest

from src import config, email_delivery
from src.config import SMTPConfig


class FakeSMTP:
    def __init__(self, host, port, **kwargs):
        self.host = host
        self.port = port
        self.kwargs = kwargs
        self.login_args = None
        self.message = None
        self.from_addr = None
        self.to_addrs = None
        self.starttls_calls = 0
        self.ehlo_calls = 0

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def ehlo(self):
        self.ehlo_calls += 1

    def starttls(self, **kwargs):
        self.starttls_calls += 1

    def login(self, username, password):
        self.login_args = (username, password)

    def send_message(self, message, from_addr, to_addrs):
        self.message = message
        self.from_addr = from_addr
        self.to_addrs = to_addrs


def smtp_config(**overrides):
    defaults = dict(
        host="smtp.example.com",
        port=465,
        username="sender@example.com",
        password="secret",
        sender="podcast@example.com",
        recipient="owner@example.com",
        use_ssl=True,
    )
    return SMTPConfig(**{**defaults, **overrides})


def test_ssl_delivery_attaches_mp3(monkeypatch, tmp_path):
    created = []

    def fake_smtp(*args, **kwargs):
        server = FakeSMTP(*args, **kwargs)
        created.append(server)
        return server

    monkeypatch.setattr(email_delivery.smtplib, "SMTP_SSL", fake_smtp)
    mp3 = tmp_path / "episode.mp3"
    mp3.write_bytes(b"mp3 audio")

    email_delivery.send_episode(
        mp3,
        "2026-08-21",
        "TLDR Daily AI — 2026-08-21",
        smtp_config(recipient="one@example.com, two@example.com"),
        edition="ai",
    )

    server = created[0]
    assert server.host == "smtp.example.com"
    assert server.port == 465
    assert server.login_args == ("sender@example.com", "secret")
    assert server.from_addr == "podcast@example.com"
    assert server.to_addrs == ["one@example.com", "two@example.com"]
    assert server.message["Subject"] == "TLDR Daily AI — 2026-08-21"
    assert "TLDR AI podcast episode" in server.message.get_body().get_content()
    attachment = next(server.message.iter_attachments())
    assert attachment.get_content_type() == "audio/mpeg"
    assert attachment.get_filename() == "episode.mp3"
    assert attachment.get_payload(decode=True) == b"mp3 audio"


def test_starttls_delivery(monkeypatch, tmp_path):
    created = []

    def fake_smtp(*args, **kwargs):
        server = FakeSMTP(*args, **kwargs)
        created.append(server)
        return server

    monkeypatch.setattr(email_delivery.smtplib, "SMTP", fake_smtp)
    mp3 = tmp_path / "episode.mp3"
    mp3.write_bytes(b"audio")

    email_delivery.send_episode(
        mp3,
        "2026-08-21",
        "Episode",
        smtp_config(port=587, use_ssl=False),
    )

    assert created[0].starttls_calls == 1
    assert created[0].ehlo_calls == 2


def test_smtp_failure_is_wrapped(monkeypatch, tmp_path):
    class FailingSMTP(FakeSMTP):
        def login(self, username, password):
            raise smtplib.SMTPAuthenticationError(535, b"bad credentials")

    monkeypatch.setattr(email_delivery.smtplib, "SMTP_SSL", FailingSMTP)
    mp3 = tmp_path / "episode.mp3"
    mp3.write_bytes(b"audio")

    with pytest.raises(email_delivery.EmailDeliveryError, match="SMTP delivery failed"):
        email_delivery.send_episode(
            mp3, "2026-08-21", "Episode", smtp_config()
        )


def test_missing_attachment_fails_before_smtp(tmp_path):
    with pytest.raises(email_delivery.EmailDeliveryError, match="does not exist"):
        email_delivery.send_episode(
            tmp_path / "missing.mp3", "2026-08-21", "Episode", smtp_config()
        )


def test_smtp_config_uses_username_as_default_sender(monkeypatch):
    values = {
        "SMTP_HOST": "smtp.example.com",
        "SMTP_PORT": "587",
        "SMTP_USE_SSL": "false",
        "SMTP_USERNAME": "sender@example.com",
        "SMTP_PASSWORD": "password",
        "EMAIL_TO": "owner@example.com",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("EMAIL_FROM", raising=False)

    resolved = config.smtp_config()

    assert resolved.host == "smtp.example.com"
    assert resolved.port == 587
    assert resolved.sender == "sender@example.com"
    assert resolved.recipient == "owner@example.com"
    assert resolved.use_ssl is False


@pytest.mark.parametrize("name,value", [("SMTP_PORT", "nope"), ("SMTP_USE_SSL", "sometimes")])
def test_smtp_config_rejects_invalid_values(monkeypatch, name, value):
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_USERNAME", "sender@example.com")
    monkeypatch.setenv("SMTP_PASSWORD", "password")
    monkeypatch.setenv("EMAIL_TO", "owner@example.com")
    monkeypatch.setenv(name, value)

    with pytest.raises(config.MissingCredential):
        config.smtp_config()
