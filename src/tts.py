"""Stage 5: segments -> per-segment audio.

Provider implementations share a small segment-oriented interface and return
24kHz signed 16-bit mono PCM. Gemini renders each segment as one multi-speaker
request. Kokoro runs locally and renders each dialogue line with its mapped
speaker voice before joining the lines.

Every segment gets 3 attempts. A segment that still fails is dropped; too many
failed segments abort the episode rather than ship a mangled recording.
"""

from __future__ import annotations

from array import array
import logging
import sys
import time
from dataclasses import dataclass
from typing import Any, Protocol

from . import config
from .script import Script, Segment

log = logging.getLogger(__name__)


class TTSError(RuntimeError):
    """Too many segments failed to render. Hard failure, per the matrix."""


@dataclass
class RenderedSegment:
    topic: str
    pcm: bytes  # raw signed 16-bit mono PCM at config.TTS_SAMPLE_RATE


class TTSProvider(Protocol):
    def synthesize(self, segment: Segment) -> bytes:
        """Return raw 24kHz signed 16-bit mono PCM for one segment, or raise."""
        ...


def format_segment(segment: Segment) -> str:
    """Multi-speaker input: a plain 'Name: line' transcript, style-prefixed."""
    body = "\n".join(f"{line.speaker}: {line.text}" for line in segment.lines)
    return f"{config.TTS_STYLE_DIRECTION}\n\n{body}"


class GeminiTTS:
    """Gemini multi-speaker TTS. Two speakers per request, which is our format."""

    def __init__(self, api_key: str | None = None, model: str | None = None):
        from google import genai  # type: ignore[reportAttributeAccessIssue]

        self._genai = genai
        self.client = genai.Client(api_key=api_key or config.gemini_key())
        self.model = model or config.TTS_MODEL

    def _speaker_config(self):
        types = self._genai.types
        return types.SpeechConfig(
            multi_speaker_voice_config=types.MultiSpeakerVoiceConfig(
                speaker_voice_configs=[
                    types.SpeakerVoiceConfig(
                        speaker=config.HOST_A,
                        voice_config=types.VoiceConfig(
                            prebuilt_voice_config=types.PrebuiltVoiceConfig(
                                voice_name=config.TTS_VOICE_A
                            )
                        ),
                    ),
                    types.SpeakerVoiceConfig(
                        speaker=config.HOST_B,
                        voice_config=types.VoiceConfig(
                            prebuilt_voice_config=types.PrebuiltVoiceConfig(
                                voice_name=config.TTS_VOICE_B
                            )
                        ),
                    ),
                ]
            )
        )

    def synthesize(self, segment: Segment) -> bytes:
        types = self._genai.types
        response = self.client.models.generate_content(
            model=self.model,
            contents=format_segment(segment),
            config=types.GenerateContentConfig(
                response_modalities=["AUDIO"],
                speech_config=self._speaker_config(),
            ),
        )
        pcm = self._audio_bytes(response)
        if not pcm:
            # The documented failure: text tokens where audio should be.
            raise TTSError("response contained no audio data")
        return pcm

    @staticmethod
    def _audio_bytes(response) -> bytes:
        for candidate in response.candidates or []:
            for part in getattr(candidate.content, "parts", None) or []:
                inline = getattr(part, "inline_data", None)
                if inline and inline.data:
                    return inline.data
        return b""


class KokoroTTS:
    """Local Kokoro TTS. Each dialogue line is rendered with its speaker's voice."""

    def __init__(
        self,
        pipeline=None,
        voice_a: str | None = None,
        voice_b: str | None = None,
        speed: float | None = None,
    ):
        if pipeline is None:
            try:
                from kokoro import KPipeline  # type: ignore[reportMissingImports]
            except ModuleNotFoundError as exc:
                raise TTSError(
                    "Kokoro dependencies are not installed; run "
                    "pip install -r requirements-kokoro.txt"
                ) from exc
            pipeline = KPipeline(lang_code=config.KOKORO_LANG_CODE)

        self.pipeline = pipeline
        self.voices = {
            config.HOST_A: voice_a or config.KOKORO_VOICE_A,
            config.HOST_B: voice_b or config.KOKORO_VOICE_B,
        }
        self.speed = speed if speed is not None else config.KOKORO_SPEED

    @staticmethod
    def _float_audio_to_pcm(audio: Any) -> bytes:
        """Convert Kokoro float samples in [-1, 1] to little-endian signed PCM."""
        numpy = getattr(audio, "numpy", None)
        if callable(numpy):
            # torch.Tensor: detach from the graph before converting.
            detach = getattr(audio, "detach", None)
            audio = numpy() if detach is None else detach().numpy()

        clip = getattr(audio, "clip", None)
        if callable(clip):
            clipped: Any = clip(-1.0, 1.0)
            return (clipped * 32767).astype("<i2").tobytes()

        samples = array(
            "h",
            (
                int(max(-1.0, min(1.0, float(sample))) * 32767)
                for sample in audio
            ),
        )
        if sys.byteorder != "little":
            samples.byteswap()
        return samples.tobytes()

    def _line_pcm(self, text: str, voice: str) -> bytes:
        chunks = [
            self._float_audio_to_pcm(audio)
            for _, _, audio in self.pipeline(text, voice=voice, speed=self.speed)
        ]
        pcm = b"".join(chunk for chunk in chunks if chunk)
        if not pcm:
            raise TTSError(f"Kokoro returned no audio for voice {voice!r}")
        return pcm

    def synthesize(self, segment: Segment) -> bytes:
        lines: list[bytes] = []
        for line in segment.lines:
            voice = self.voices.get(line.speaker)
            if not voice:
                raise TTSError(f"no Kokoro voice configured for speaker {line.speaker!r}")
            lines.append(self._line_pcm(line.text, voice))

        if not lines:
            raise TTSError("segment contained no dialogue lines")

        gap_frames = int(config.TTS_SAMPLE_RATE * config.TTS_LINE_GAP_MS / 1000)
        gap = b"\x00" * (
            gap_frames * config.TTS_SAMPLE_WIDTH * config.TTS_CHANNELS
        )
        return gap.join(lines)


TTS_PROVIDERS = {
    "gemini": GeminiTTS,
    "kokoro": KokoroTTS,
}


def create_provider(name: str | None = None) -> TTSProvider:
    """Build configured provider. New integrations register one factory here."""
    selected = (name or config.TTS_PROVIDER).strip().lower()
    factory = TTS_PROVIDERS.get(selected)
    if factory is None:
        supported = ", ".join(sorted(TTS_PROVIDERS))
        raise TTSError(f"unsupported TTS_PROVIDER {selected!r}; choose one of: {supported}")
    log.info("using %s TTS provider", selected)
    return factory()


def render_segments(script: Script, provider: TTSProvider) -> list[RenderedSegment]:
    """Render every segment, dropping ones that fail all attempts.

    Raises TTSError only when too much of the episode is missing to ship.
    """
    rendered: list[RenderedSegment] = []
    failed: list[str] = []

    for index, segment in enumerate(script.segments, start=1):
        for attempt in range(1, config.TTS_RETRIES + 1):
            try:
                pcm = provider.synthesize(segment)
                rendered.append(RenderedSegment(topic=segment.topic, pcm=pcm))
                log.info("segment %d/%d '%s' rendered (%d bytes)",
                         index, len(script.segments), segment.topic, len(pcm))
                break
            except Exception as exc:  # noqa: BLE001 - retry every provider-side failure
                log.warning("segment '%s' attempt %d/%d failed: %s",
                            segment.topic, attempt, config.TTS_RETRIES, exc)
                if attempt == config.TTS_RETRIES:
                    failed.append(segment.topic)
                else:
                    time.sleep(config.TTS_BACKOFF_BASE**attempt)

    total = len(script.segments)
    if failed:
        rate = len(failed) / total
        log.warning("%d/%d segments failed (%.0f%%): %s",
                    len(failed), total, rate * 100, ", ".join(failed))
        if rate > config.TTS_MAX_SEGMENT_FAILURE_RATE:
            raise TTSError(
                f"{len(failed)}/{total} segments failed, over the "
                f"{config.TTS_MAX_SEGMENT_FAILURE_RATE:.0%} ceiling — refusing to ship "
                "a mangled episode"
            )

    if not rendered:
        raise TTSError("no segments rendered")
    return rendered
