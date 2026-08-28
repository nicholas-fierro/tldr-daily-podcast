"""Stage 6: rendered segments -> one tagged MP3.

Concatenate with a short silence between segments so topic transitions do not
sound clipped, normalize loudness so volume does not vary between days, and
encode small. The ffmpeg command construction is kept as a pure function so it
can be tested without a working ffmpeg.
"""

from __future__ import annotations

import json
import logging
import subprocess
import wave
from pathlib import Path

from . import config
from .tts import RenderedSegment

log = logging.getLogger(__name__)


class AudioError(RuntimeError):
    """Muxing failed. Hard failure — better no episode than a broken file."""


def write_wav(pcm: bytes, path: Path) -> Path:
    """Wrap raw PCM in a WAV container so ffmpeg can read it without -f s16le."""
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(config.TTS_CHANNELS)
        handle.setsampwidth(config.TTS_SAMPLE_WIDTH)
        handle.setframerate(config.TTS_SAMPLE_RATE)
        handle.writeframes(pcm)
    return path


def silence_pcm(milliseconds: int) -> bytes:
    frames = int(config.TTS_SAMPLE_RATE * milliseconds / 1000)
    return b"\x00" * (frames * config.TTS_SAMPLE_WIDTH * config.TTS_CHANNELS)


def concat_pcm(segments: list[RenderedSegment], gap_ms: int = config.SEGMENT_GAP_MS) -> bytes:
    """Join segments with padding. Done on raw PCM: no re-encode, no seams."""
    if not segments:
        raise AudioError("nothing to concatenate")
    gap = silence_pcm(gap_ms)
    joined = bytearray()
    for index, segment in enumerate(segments):
        if index:
            joined.extend(gap)
        joined.extend(segment.pcm)
    return bytes(joined)


def build_ffmpeg_command(source: Path, destination: Path, tags: dict[str, str]) -> list[str]:
    """Pure: the exact argv for normalize + encode + tag. Tested directly."""
    command = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(source),
        "-af", f"loudnorm=I={config.LOUDNORM_TARGET_LUFS}:TP=-4:LRA=11",
        "-ac", "1",
        "-b:a", config.MP3_BITRATE,
        "-codec:a", "libmp3lame",
    ]
    for key, value in tags.items():
        if value:
            command += ["-metadata", f"{key}={value}"]
    command.append(str(destination))
    return command


def episode_tags(
    date: str,
    headline: str,
    edition: str = config.EDITION,
) -> dict[str, str]:
    edition_name = edition.upper()
    return {
        "title": headline or f"{config.PODCAST_TITLE} {edition_name} — {date}",
        "artist": config.PODCAST_AUTHOR,
        "album": config.PODCAST_TITLE,
        "date": date,
        "genre": "Podcast",
        "comment": f"TLDR {edition_name}, {date}",
    }


def probe_duration(path: Path) -> float:
    """Seconds, via ffprobe. Feeds <itunes:duration> and the sanity check."""
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "json", str(path)],
        capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        raise AudioError(f"ffprobe failed: {result.stderr.strip()}")
    return float(json.loads(result.stdout)["format"]["duration"])


def build_episode(
    segments: list[RenderedSegment],
    date: str,
    headline: str,
    workdir: Path,
    edition: str = config.EDITION,
) -> tuple[Path, float]:
    """PCM segments -> normalized, tagged MP3. Returns (path, duration_seconds)."""
    workdir.mkdir(parents=True, exist_ok=True)
    identity = f"{edition}-{date}"
    joined_wav = write_wav(concat_pcm(segments), workdir / f"{identity}.wav")
    mp3 = workdir / f"{identity}.mp3"

    command = build_ffmpeg_command(
        joined_wav,
        mp3,
        episode_tags(date, headline, edition),
    )
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0 or not mp3.exists():
        raise AudioError(f"ffmpeg failed: {result.stderr.strip()}")

    duration = probe_duration(mp3)
    log.info("episode: %s (%.1f min, %.1f MB)",
             mp3.name, duration / 60, mp3.stat().st_size / 1e6)
    if not config.DURATION_MIN_S <= duration <= config.DURATION_MAX_S:
        log.warning(
            "duration %.1f min is outside the %d-%d min target — adjust the word target "
            "in config, not the playback speed",
            duration / 60, config.DURATION_MIN_S // 60, config.DURATION_MAX_S // 60,
        )
    return mp3, duration
