"""Audio assembly. ffmpeg is not invoked here — the argv construction and the
PCM math are pure, and those are where the bugs that ship a broken episode live.
"""

import wave
from pathlib import Path

import pytest

from src import audio, config
from src.tts import RenderedSegment


def segment(topic="t", samples=1_000) -> RenderedSegment:
    return RenderedSegment(topic=topic, pcm=b"\x01\x02" * samples)


# --- concatenation --------------------------------------------------------

def test_gap_is_inserted_between_segments_only():
    gap = len(audio.silence_pcm(config.SEGMENT_GAP_MS))
    joined = audio.concat_pcm([segment(), segment(), segment()])
    assert len(joined) == 3 * 2_000 + 2 * gap  # 2 gaps for 3 segments


def test_single_segment_gets_no_padding():
    assert len(audio.concat_pcm([segment()])) == 2_000


def test_silence_length_matches_the_sample_rate():
    pcm = audio.silence_pcm(1_000)  # one second
    expected = config.TTS_SAMPLE_RATE * config.TTS_SAMPLE_WIDTH * config.TTS_CHANNELS
    assert len(pcm) == expected
    assert set(pcm) == {0}


def test_empty_input_raises():
    with pytest.raises(audio.AudioError):
        audio.concat_pcm([])


# --- wav container --------------------------------------------------------

def test_wav_header_matches_the_tts_output_format(tmp_path: Path):
    path = audio.write_wav(b"\x00\x01" * 5_000, tmp_path / "a.wav")
    with wave.open(str(path)) as handle:
        assert handle.getnchannels() == config.TTS_CHANNELS
        assert handle.getsampwidth() == config.TTS_SAMPLE_WIDTH
        assert handle.getframerate() == config.TTS_SAMPLE_RATE
        assert handle.getnframes() == 5_000


# --- ffmpeg argv ----------------------------------------------------------

@pytest.fixture
def command(tmp_path: Path) -> list[str]:
    return audio.build_ffmpeg_command(
        tmp_path / "in.wav", tmp_path / "out.mp3",
        audio.episode_tags("2026-08-20", "TLDR Daily — 2026-08-20"),
    )


def test_loudnorm_targets_broadcast_standard(command):
    filters = command[command.index("-af") + 1]
    assert f"I={config.LOUDNORM_TARGET_LUFS}" in filters
    assert "loudnorm" in filters


def test_output_is_mono_mp3_at_the_configured_bitrate(command):
    assert command[command.index("-ac") + 1] == "1"
    assert command[command.index("-b:a") + 1] == config.MP3_BITRATE
    assert command[command.index("-codec:a") + 1] == "libmp3lame"


def test_overwrite_is_forced(command):
    assert "-y" in command  # a re-run must not hang on a prompt


def test_tags_are_passed_as_metadata(command):
    pairs = [command[i + 1] for i, arg in enumerate(command) if arg == "-metadata"]
    assert "title=TLDR Daily — 2026-08-20" in pairs
    assert f"album={config.PODCAST_TITLE}" in pairs
    assert "date=2026" in pairs


def test_empty_tags_are_omitted(tmp_path: Path):
    command = audio.build_ffmpeg_command(
        tmp_path / "in.wav", tmp_path / "out.mp3", {"title": "T", "artist": ""}
    )
    pairs = [command[i + 1] for i, arg in enumerate(command) if arg == "-metadata"]
    assert pairs == ["title=T"]


def test_output_path_is_last(command):
    assert command[-1].endswith(".mp3")


def test_headline_falls_back_when_absent():
    assert audio.episode_tags("2026-08-20", "")["title"] == (
        f"{config.PODCAST_TITLE} — 2026-08-20"
    )
