"""Stage 4: items -> a two-host dialogue, as segmented JSON.

One model call. The prompt is the highest-leverage text in the project: it
decides whether the episode is worth listening to. Expect to iterate on it.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, asdict
from typing import Protocol

import httpx

from . import config
from .parse import Item

log = logging.getLogger(__name__)


class ScriptError(RuntimeError):
    """Script generation failed. Hard failure, per the matrix."""


@dataclass
class Line:
    speaker: str
    text: str


@dataclass
class Segment:
    topic: str
    lines: list[Line]

    def word_count(self) -> int:
        return sum(len(line.text.split()) for line in self.lines)


@dataclass
class Script:
    date: str
    segments: list[Segment]

    def word_count(self) -> int:
        return sum(segment.word_count() for segment in self.segments)

    def to_dict(self) -> dict:
        return {
            "date": self.date,
            "word_count": self.word_count(),
            "segments": [
                {"topic": s.topic, "lines": [asdict(line) for line in s.lines]}
                for s in self.segments
            ],
        }


SYSTEM_PROMPT = f"""You write a daily tech-news podcast: a two-host conversation \
about the day's TLDR newsletter, for a technical listener who wants to know what \
happened and why it matters.

The hosts are {config.HOST_A} and {config.HOST_B}. They are the same two people every \
day and the listener knows them.
- {config.HOST_A} frames the day, asks the questions a smart non-specialist would ask, \
and drives the running order.
- {config.HOST_B} explains mechanism and context, supplies numbers, and says what is \
actually new versus what is a rerun of an older story.

Hard requirements:

LENGTH. {config.WORD_TARGET_MIN}-{config.WORD_TARGET_MAX} words total. Going long is a \
failure, not a bonus — this has to land near ten minutes of audio. Before writing, \
silently budget {config.SCRIPT_SEGMENT_MIN}-{config.SCRIPT_SEGMENT_MAX} coherent \
segments and keep the total near the target. Do not output the budget.

STRUCTURE. Return {config.SCRIPT_SEGMENT_MIN}-{config.SCRIPT_SEGMENT_MAX} segments, \
grouped by theme rather than newsletter order. \
Each story may appear in only one segment. The opening segment covers the biggest story \
or one coherent theme; it must not preview stories that later segments repeat. Sections \
in the input are topic hints, not a running order.

WEIGHTING. Weight by substance. A major acquisition or a real technical result earns \
60-90 seconds. A minor item earns one sentence. You are explicitly authorized to omit \
weak items entirely — a tighter episode is a better episode. Do not give every item a turn.

OPENING. Start the opening segment with a brief, natural two- or three-sentence \
welcome from one host: welcome the listener back to TLDR Daily, state the edition date \
supplied in the input, then move directly to the day's single biggest story. Keep it \
concise and do not preview stories covered later. The rest of the opening segment covers \
that story.

CONFIDENCE. Items marked enriched=false are ones where only the newsletter's one-line \
summary was available — the full article could not be read. For these, hedge naturally \
and audibly: "the summary suggests", "going off the writeup", "we haven't seen the \
details on this one". Never invent specifics — numbers, names, dates, quotes, technical \
mechanisms — that are not in the text you were given. This is the single most important \
rule here. A hedged sentence is always better than a confident wrong one.

EVIDENCE. Every factual claim must be supported by the supplied blurb or article text. \
Do not turn a possibility into certainty, infer motives, invent causal explanations, or \
add broad implications that the source does not support.

VOICE. Real conversation: short reactions, follow-up questions, and disagreement where \
honest. Vary sentence length. Avoid repetitive starts such as "And", "Right", and \
"Exactly". No corporate register, no rhetorical questions used as transitions. Never \
invent a listener email, a sponsor, or a segment that does not exist. Do not read URLs.

FORMATTING. No stage directions, no [laughs], no sound or music cues, no emoji, no \
markdown. Plain spoken sentences only — every character you write will be read aloud.

CLOSING. After the last story, add one or two brief, natural sign-off lines wishing \
the listener a good rest of their day. No calls to action, no "subscribe", and no new \
news or factual claims.

Return ONLY a JSON object, no prose around it, in exactly this shape:

{{"segments": [{{"topic": "short-kebab-case-slug", "lines": [{{"speaker": "{config.HOST_A}", "text": "..."}}]}}]}}

Return {config.SCRIPT_SEGMENT_MIN}-{config.SCRIPT_SEGMENT_MAX} segments, usually \
180-300 words each. Segments are voiced separately and stitched, so each must stand \
alone without mid-sentence handoffs. Speaker must be exactly \
"{config.HOST_A}" or "{config.HOST_B}"."""


SCRIPT_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "segments": {
            "type": "array",
            "minItems": config.SCRIPT_SEGMENT_MIN,
            "maxItems": config.SCRIPT_SEGMENT_MAX,
            "items": {
                "type": "object",
                "properties": {
                    "topic": {"type": "string", "minLength": 1},
                    "lines": {
                        "type": "array",
                        "minItems": 1,
                        "items": {
                            "type": "object",
                            "properties": {
                                "speaker": {
                                    "type": "string",
                                    "enum": [config.HOST_A, config.HOST_B],
                                },
                                "text": {"type": "string", "minLength": 1},
                            },
                            "required": ["speaker", "text"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["topic", "lines"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["segments"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class ScriptGeneration:
    text: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float | None = None


class ScriptProvider(Protocol):
    def generate(self, system_prompt: str, user_prompt: str) -> ScriptGeneration:
        """Generate one structured podcast script."""
        ...


class OpenRouterScriptProvider:
    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self.api_key = api_key or config.openrouter_key()
        self.model = model or config.SCRIPT_MODEL
        self.client = client

    def _headers(self) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        if config.OPENROUTER_REFERER:
            headers["HTTP-Referer"] = config.OPENROUTER_REFERER
        if config.OPENROUTER_TITLE:
            headers["X-Title"] = config.OPENROUTER_TITLE
        return headers

    def _body(self, system_prompt: str, user_prompt: str) -> dict:
        return {
            "model": self.model,
            "max_tokens": config.SCRIPT_MAX_TOKENS,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "podcast_script",
                    "strict": True,
                    "schema": SCRIPT_JSON_SCHEMA,
                },
            },
            "provider": {"require_parameters": True, "sort": "throughput"},
            "plugins": [{"id": "response-healing"}],
        }

    def _post(
        self,
        client: httpx.Client,
        system_prompt: str,
        user_prompt: str,
    ) -> ScriptGeneration:
        response = client.post(
            f"{config.OPENROUTER_BASE_URL}/chat/completions",
            headers=self._headers(),
            json=self._body(system_prompt, user_prompt),
        )
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            detail = response.text.strip()[:500]
            raise ScriptError(
                f"OpenRouter returned HTTP {response.status_code}: {detail}"
            ) from exc

        try:
            payload = response.json()
            content = payload["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise ScriptError("OpenRouter returned an invalid completion payload") from exc

        if isinstance(content, list):
            content = "".join(
                str(part.get("text", ""))
                for part in content
                if isinstance(part, dict)
            )
        if not isinstance(content, str) or not content.strip():
            raise ScriptError("OpenRouter returned an empty completion")

        usage = payload.get("usage") or {}
        raw_cost = usage.get("cost")
        try:
            cost = float(raw_cost) if raw_cost is not None else None
        except (TypeError, ValueError):
            cost = None

        return ScriptGeneration(
            text=content,
            model=str(payload.get("model") or self.model),
            input_tokens=int(usage.get("prompt_tokens") or 0),
            output_tokens=int(usage.get("completion_tokens") or 0),
            cost_usd=cost,
        )

    def generate(self, system_prompt: str, user_prompt: str) -> ScriptGeneration:
        if self.client is not None:
            return self._post(self.client, system_prompt, user_prompt)
        with httpx.Client(timeout=config.SCRIPT_TIMEOUT) as client:
            return self._post(client, system_prompt, user_prompt)


def _default_script_provider() -> ScriptProvider:
    if config.SCRIPT_PROVIDER == "openrouter":
        return OpenRouterScriptProvider()
    raise ScriptError(f"unsupported script provider: {config.SCRIPT_PROVIDER}")


def build_user_prompt(items: list[Item], date: str) -> str:
    payload = {
        "edition_date": date,
        "item_count": len(items),
        "items": [
            {
                "section": item.section,
                "title": item.title,
                "url": item.url,
                "read_time_minutes": item.read_time,
                "enriched": item.enriched,
                "blurb": item.blurb,
                "article_text": item.text if item.enriched else None,
            }
            for item in items
        ],
    }
    return (
        f"Here is the TLDR tech edition for {date}. Write today's episode.\n\n"
        f"{json.dumps(payload, indent=2, ensure_ascii=False)}"
    )


def _extract_json(raw: str) -> dict:
    """Models sometimes wrap JSON in a fence or a sentence. Recover both."""
    raw = raw.strip()
    fenced = re.search(r"```(?:json)?\s*(.+?)\s*```", raw, re.S)
    if fenced:
        raw = fenced.group(1)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        start, end = raw.find("{"), raw.rfind("}")
        if start == -1 or end <= start:
            raise
        return json.loads(raw[start : end + 1])


def parse_script_json(raw: str, date: str) -> Script:
    data = _extract_json(raw)
    raw_segments = data.get("segments")
    if not isinstance(raw_segments, list) or not raw_segments:
        raise ScriptError("model returned no segments")

    valid_speakers = {config.HOST_A, config.HOST_B}
    segments: list[Segment] = []
    for index, segment in enumerate(raw_segments):
        lines = [
            Line(speaker=line["speaker"].strip(), text=" ".join(str(line["text"]).split()))
            for line in segment.get("lines", [])
            if str(line.get("text", "")).strip()
        ]
        for line in lines:
            if line.speaker not in valid_speakers:
                raise ScriptError(f"unknown speaker {line.speaker!r} in segment {index}")
        if lines:
            topic = str(segment.get("topic") or f"segment-{index + 1}").strip()
            segments.append(Segment(topic=topic, lines=lines))

    if not segments:
        raise ScriptError("every segment was empty after cleaning")
    return Script(date=date, segments=segments)


def generate_script(
    items: list[Item],
    date: str,
    provider: ScriptProvider | None = None,
) -> Script:
    """Generate one script, retrying API and unusable-output failures."""
    provider = provider or _default_script_provider()
    user_prompt = build_user_prompt(items, date)
    retry_correction = ""
    last_error: Exception | None = None

    for attempt in range(1, config.SCRIPT_RETRIES + 2):
        try:
            generation = provider.generate(
                SYSTEM_PROMPT,
                retry_correction or user_prompt,
            )
            if generation.cost_usd is None:
                log.info(
                    "script model=%s tokens: in=%d out=%d",
                    generation.model,
                    generation.input_tokens,
                    generation.output_tokens,
                )
            else:
                log.info(
                    "script model=%s tokens: in=%d out=%d cost=$%.6f",
                    generation.model,
                    generation.input_tokens,
                    generation.output_tokens,
                    generation.cost_usd,
                )

            episode_script = parse_script_json(generation.text, date)
            words = episode_script.word_count()
            segment_count = len(episode_script.segments)
            log.info("script: %d words across %d segments", words, segment_count)

            valid_length = config.WORD_HARD_MIN <= words <= config.WORD_HARD_MAX
            valid_segments = config.SCRIPT_SEGMENT_MIN <= segment_count <= config.SCRIPT_SEGMENT_MAX
            if not valid_length or not valid_segments:
                target_words = (config.WORD_TARGET_MIN + config.WORD_TARGET_MAX) // 2
                word_delta = target_words - words
                direction = (
                    f"Add approximately {word_delta} words using only supplied facts."
                    if word_delta > 0
                    else f"Cut approximately {-word_delta} words without losing key facts."
                )
                retry_correction = (
                    "Revise the previous podcast JSON below. Use only facts already "
                    "present in it; do not introduce new claims. Preserve its strongest "
                    "material while fixing length and structure. "
                    f"It had {words} words across {segment_count} segments. {direction} "
                    f"Return {config.SCRIPT_SEGMENT_MIN}-{config.SCRIPT_SEGMENT_MAX} "
                    f"non-overlapping segments and {config.WORD_TARGET_MIN}-"
                    f"{config.WORD_TARGET_MAX} words total. Do not repeat a story in "
                    "multiple segments. Return only corrected JSON.\n\n"
                    f"PREVIOUS JSON:\n{generation.text}"
                )
                raise ScriptError(
                    f"script was {words} words across {segment_count} segments; hard limits "
                    f"are {config.WORD_HARD_MIN}-{config.WORD_HARD_MAX} words and "
                    f"{config.SCRIPT_SEGMENT_MIN}-{config.SCRIPT_SEGMENT_MAX} segments"
                )

            if not config.WORD_TARGET_MIN <= words <= config.WORD_TARGET_MAX:
                if config.WORD_ACCEPT_MIN <= words <= config.WORD_ACCEPT_MAX:
                    log.warning(
                        "script is %d words, outside the %d-%d target but within the "
                        "%d-%d accepted range",
                        words,
                        config.WORD_TARGET_MIN,
                        config.WORD_TARGET_MAX,
                        config.WORD_ACCEPT_MIN,
                        config.WORD_ACCEPT_MAX,
                    )
                else:
                    log.warning(
                        "script is %d words, outside the preferred %d-%d range but within "
                        "hard safety limits; verify duration carefully at M4",
                        words,
                        config.WORD_ACCEPT_MIN,
                        config.WORD_ACCEPT_MAX,
                    )

            return episode_script

        except Exception as exc:  # noqa: BLE001 - retry API and malformed-output alike
            last_error = exc
            log.warning("script attempt %d failed: %s", attempt, exc)
            if attempt <= config.SCRIPT_RETRIES:
                time.sleep(2.0**attempt)

    raise ScriptError(f"script generation failed after retries: {last_error}")
