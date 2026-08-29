"""TTS orchestration: retries, segment dropping, and the failure ceiling.

Exercised against a fake provider — the real one needs a key and is what M4's
listen-through checkpoint is for.
"""

from types import SimpleNamespace

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

    def synthesize(self, segment: Segment) -> bytes:
        self.calls += 1
        index = int(segment.topic.rsplit("-", 1)[-1])
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


def test_gemini_formats_the_structured_segment_for_its_request():
    captured = {}

    class Models:
        @staticmethod
        def generate_content(**kwargs):
            captured.update(kwargs)
            part = SimpleNamespace(inline_data=SimpleNamespace(data=b"pcm"))
            return SimpleNamespace(
                candidates=[SimpleNamespace(content=SimpleNamespace(parts=[part]))]
            )

    provider = tts.GeminiTTS.__new__(tts.GeminiTTS)
    provider.model = "test-model"
    provider.client = SimpleNamespace(models=Models())
    provider._genai = SimpleNamespace(
        types=SimpleNamespace(GenerateContentConfig=lambda **kwargs: kwargs)
    )
    provider._speaker_config = lambda: "speaker-config"
    segment = script_with(1).segments[0]

    assert provider.synthesize(segment) == b"pcm"
    assert captured["contents"] == tts.format_segment(segment)
    assert captured["model"] == "test-model"


def test_provider_factory_uses_selected_registry_entry(monkeypatch):
    provider = object()
    monkeypatch.setattr(config, "TTS_PROVIDER", "FAKE")
    monkeypatch.setitem(tts.TTS_PROVIDERS, "fake", lambda: provider)
    assert tts.create_provider() is provider


def test_provider_factory_rejects_unknown_provider():
    with pytest.raises(tts.TTSError, match="unsupported TTS_PROVIDER"):
        tts.create_provider("unknown")


def test_render_segments_passes_structured_segment_to_provider():
    received = []

    class CapturingProvider:
        def synthesize(self, segment):
            received.append(segment)
            return b"audio"

    source = script_with(1)
    tts.render_segments(source, CapturingProvider())
    assert received == [source.segments[0]]


def test_kokoro_renders_each_speaker_with_its_configured_voice():
    calls = []

    def pipeline(text, voice, speed):
        calls.append((text, voice, speed))
        yield text, "phonemes", [0.0, 0.5, -0.5]

    segment = Segment(topic="topic", lines=[
        Line(speaker=config.HOST_A, text="First line."),
        Line(speaker=config.HOST_B, text="Second line."),
    ])
    provider = tts.KokoroTTS(
        pipeline=pipeline,
        voice_a="voice-a",
        voice_b="voice-b",
        speed=1.25,
    )

    pcm = provider.synthesize(segment)

    assert calls == [
        ("First line.", "voice-a", 1.25),
        ("Second line.", "voice-b", 1.25),
    ]
    line_pcm = b"\x00\x00\xff\x3f\x01\xc0"
    gap_frames = int(config.TTS_SAMPLE_RATE * config.TTS_LINE_GAP_MS / 1000)
    gap = b"\x00" * (gap_frames * config.TTS_SAMPLE_WIDTH * config.TTS_CHANNELS)
    assert pcm == line_pcm + gap + line_pcm


def test_kokoro_converts_torch_tensor_audio_without_astype():
    """Kokoro yields torch.Tensor, which has .clip() but not .astype()."""

    class FakeTensor:
        def __init__(self, values):
            self.values = values

        def detach(self):
            return self

        def numpy(self):
            import numpy as np
            return np.array(self.values, dtype="float32")

    def pipeline(text, voice, speed):
        yield text, "phonemes", FakeTensor([0.0, 0.5, -0.5])

    segment = Segment(topic="topic", lines=[Line(speaker=config.HOST_A, text="Hi.")])
    provider = tts.KokoroTTS(pipeline=pipeline, voice_a="voice-a", voice_b="voice-b")

    pcm = provider.synthesize(segment)

    assert pcm == b"\x00\x00\xff\x3f\x01\xc0"


def test_kokoro_rejects_an_unmapped_speaker():
    provider = tts.KokoroTTS(pipeline=lambda *args, **kwargs: ())
    segment = Segment(topic="topic", lines=[Line(speaker="Other", text="Hello")])

    with pytest.raises(tts.TTSError, match="no Kokoro voice configured"):
        provider.synthesize(segment)


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
