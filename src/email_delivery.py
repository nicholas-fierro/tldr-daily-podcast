"""Email delivery for generated episodes."""

from __future__ import annotations

from email.message import EmailMessage
import logging
from pathlib import Path
import smtplib
import ssl

from .combine import EditionCoverage, coverage_summary
from .config import EDITION, SMTPConfig

log = logging.getLogger(__name__)


class EmailDeliveryError(RuntimeError):
    """Raised when an episode cannot be delivered by email."""


def _recipients(value: str) -> list[str]:
    recipients = [address.strip() for address in value.split(",") if address.strip()]
    if not recipients:
        raise EmailDeliveryError("EMAIL_TO contains no recipient addresses")
    return recipients


def _body(date: str, edition: str, coverage: list[EditionCoverage] | None) -> str:
    """Coverage is reported in full: a missing source must never be silent."""
    if coverage is None:
        return f"Attached is the TLDR {edition.upper()} podcast episode for {date}.\n"
    return (
        f"Attached is the combined TLDR podcast episode for {date}.\n\n"
        f"{coverage_summary(coverage)}\n"
    )


def send_episode(
    mp3: Path,
    date: str,
    headline: str,
    config: SMTPConfig,
    *,
    edition: str = EDITION,
    coverage: list[EditionCoverage] | None = None,
) -> None:
    """Send one generated episode as an MP3 attachment."""
    if not mp3.is_file():
        raise EmailDeliveryError(f"episode file does not exist: {mp3}")

    recipients = _recipients(config.recipient)
    message = EmailMessage()
    message["Subject"] = headline
    message["From"] = config.sender
    message["To"] = ", ".join(recipients)
    message.set_content(_body(date, edition, coverage))
    message.add_attachment(
        mp3.read_bytes(),
        maintype="audio",
        subtype="mpeg",
        filename=mp3.name,
    )

    context = ssl.create_default_context()
    try:
        if config.use_ssl:
            with smtplib.SMTP_SSL(
                config.host, config.port, context=context, timeout=30
            ) as server:
                server.login(config.username, config.password)
                server.send_message(
                    message, from_addr=config.sender, to_addrs=recipients
                )
        else:
            with smtplib.SMTP(config.host, config.port, timeout=30) as server:
                server.ehlo()
                server.starttls(context=context)
                server.ehlo()
                server.login(config.username, config.password)
                server.send_message(
                    message, from_addr=config.sender, to_addrs=recipients
                )
    except (OSError, smtplib.SMTPException) as exc:
        raise EmailDeliveryError(f"SMTP delivery failed: {exc}") from exc

    log.info("episode emailed successfully (%s, %.1f MB)", mp3.name, mp3.stat().st_size / 1e6)
