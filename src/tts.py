"""Stage 5: segments -> per-segment audio.

Deliberately behind a small interface. If Gemini's voices disappoint, an
ElevenLabs implementation of TTSProvider is the only thing that has to change.

Two constraints from the handoff shape this module:
  1. Quality drifts on long outputs, so we render one request per topic
     segment (~60-120s) and concatenate later. Never one 10-minute request.
  2. The model sometimes returns text tokens instead of audio and 500s, so
     every segment gets 3 attempts; a segment that still fails is dropped.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Protocol

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
    def synthesize(self, text: str) -> bytes:
        """Return raw PCM for one segment, or raise."""


def format_segment(segment: Segment) -> str:
    """Multi-speaker input: a plain 'Name: line' transcript, style-prefixed."""
    body = "\n".join(f"{line.speaker}: {line.text}" for line in segment.lines)
    return f"{config.TTS_STYLE_DIRECTION}\n\n{body}"


class GeminiTTS:
    """Gemini multi-speaker TTS. Two speakers per request, which is our format."""

    def __init__(self, api_key: str | None = None, model: str | None = None):
        from google import genai

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

    def synthesize(self, text: str) -> bytes:
        types = self._genai.types
        response = self.client.models.generate_content(
            model=self.model,
            contents=text,
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


def render_segments(script: Script, provider: TTSProvider) -> list[RenderedSegment]:
    """Render every segment, dropping ones that fail all attempts.

    Raises TTSError only when too much of the episode is missing to ship.
    """
    rendered: list[RenderedSegment] = []
    failed: list[str] = []

    for index, segment in enumerate(script.segments, start=1):
        text = format_segment(segment)
        for attempt in range(1, config.TTS_RETRIES + 1):
            try:
                pcm = provider.synthesize(text)
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
