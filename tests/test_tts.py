"""TTS orchestration: retries, segment dropping, and the failure ceiling.

Exercised against a fake provider — the real one needs a key and is what M4's
listen-through checkpoint is for.
"""

import pytest

from src import config, tts
from src.script import Line, Script, Segment


def script_with(count: int) -> Script:
    return Script(date="2026-08-20", segments=[
        Segment(topic=f"topic-{i}", lines=[
            # The index lives in the dialogue because that is all the provider
            # sees — format_segment does not pass the topic through.
            Line(speaker=config.HOST_A, text=f"A line of dialogue about story {i}."),
            Line(speaker=config.HOST_B, text="A reply."),
        ]) for i in range(count)
    ])


class FakeProvider:
    """Fails the first `fail_times` calls for each listed topic index."""

    def __init__(self, fail_always=(), fail_times=0):
        self.fail_always = set(fail_always)
        self.fail_times = fail_times
        self.calls = 0
        self.transient_left = fail_times

    def synthesize(self, text: str) -> bytes:
        self.calls += 1
        index = int(text.rsplit("about story ", 1)[-1].split(".")[0])
        if index in self.fail_always:
            raise RuntimeError("response contained no audio data")
        if self.transient_left:
            self.transient_left -= 1
            raise RuntimeError("500 from upstream")
        return b"\x00\x01" * 1_000


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(tts.time, "sleep", lambda _: None)


# --- formatting -----------------------------------------------------------

def test_segment_is_formatted_as_a_speaker_transcript():
    segment = script_with(1).segments[0]
    text = tts.format_segment(segment)
    assert f"{config.HOST_A}: A line of dialogue about story 0." in text
    assert f"{config.HOST_B}: A reply." in text


def test_style_direction_prefixes_every_segment():
    text = tts.format_segment(script_with(1).segments[0])
    assert text.startswith(config.TTS_STYLE_DIRECTION)


# --- retry and drop -------------------------------------------------------

def test_all_segments_render_on_a_clean_run():
    rendered = tts.render_segments(script_with(4), FakeProvider())
    assert len(rendered) == 4
    assert all(segment.pcm for segment in rendered)


def test_transient_failure_is_retried_and_recovers():
    provider = FakeProvider(fail_times=2)
    rendered = tts.render_segments(script_with(3), provider)
    assert len(rendered) == 3


def test_segment_failing_all_attempts_is_dropped_not_fatal():
    """A 9-minute episode is a fine outcome; a failed run is not."""
    rendered = tts.render_segments(script_with(8), FakeProvider(fail_always={3}))
    assert len(rendered) == 7
    assert all(segment.topic != "topic-3" for segment in rendered)


def test_each_segment_gets_the_configured_attempt_count():
    # One segment, always failing: 100% failure, so this also trips the ceiling.
    # What is under test is the attempt count, not the raise.
    provider = FakeProvider(fail_always={0})
    with pytest.raises(tts.TTSError):
        tts.render_segments(script_with(1), provider)
    assert provider.calls == config.TTS_RETRIES


def test_too_many_failures_refuses_to_ship():
    with pytest.raises(tts.TTSError, match="refusing to ship"):
        tts.render_segments(script_with(4), FakeProvider(fail_always={0, 1, 2}))


def test_total_failure_raises():
    with pytest.raises(tts.TTSError):
        tts.render_segments(script_with(2), FakeProvider(fail_always={0, 1}))


def test_failure_ceiling_is_a_rate_not_a_count():
    """1 of 8 is fine; the same single failure out of 2 is not."""
    assert len(tts.render_segments(script_with(8), FakeProvider(fail_always={1}))) == 7
    with pytest.raises(tts.TTSError):
        tts.render_segments(script_with(2), FakeProvider(fail_always={1}))
