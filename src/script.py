"""Stage 4: items -> a two-host dialogue, as segmented JSON.

One Claude call. The prompt is the highest-leverage text in the project: it
decides whether the episode is worth listening to. Expect to iterate on it.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, asdict

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
failure, not a bonus — this has to land near ten minutes of audio. Cut material to fit.

STRUCTURE. Group by theme, not by the newsletter's ordering. "Three things happened in \
AI infrastructure today" is the goal; reading a list in order is the failure mode. \
Sections in the input are a hint about topic, not a running order.

WEIGHTING. Weight by substance. A major acquisition or a real technical result earns \
60-90 seconds. A minor item earns one sentence. You are explicitly authorized to omit \
weak items entirely — a tighter episode is a better episode. Do not give every item a turn.

OPENING. Cold open on the day's single biggest story. No "welcome back", no "on today's \
episode", no throat-clearing of any kind. The first sentence should be about the news.

CONFIDENCE. Items marked enriched=false are ones where only the newsletter's one-line \
summary was available — the full article could not be read. For these, hedge naturally \
and audibly: "the summary suggests", "going off the writeup", "we haven't seen the \
details on this one". Never invent specifics — numbers, names, dates, quotes, technical \
mechanisms — that are not in the text you were given. This is the single most important \
rule here. A hedged sentence is always better than a confident wrong one.

VOICE. Real conversation: interruptions, short reactions, disagreement where honest. \
Vary sentence length. No corporate register, no "in today's fast-moving landscape", no \
rhetorical questions used as transitions. Never invent a listener email, a sponsor, or a \
segment that does not exist. Do not read out URLs.

FORMATTING. No stage directions, no [laughs], no sound or music cues, no emoji, no \
markdown. Plain spoken sentences only — every character you write will be read aloud.

CLOSING. End on the last story or a one-line sign-off. No calls to action, no "subscribe".

Return ONLY a JSON object, no prose around it, in exactly this shape:

{{"segments": [{{"topic": "short-kebab-case-slug", "lines": [{{"speaker": "{config.HOST_A}", "text": "..."}}]}}]}}

Each segment is one theme and should run 60-120 seconds of speech (roughly 150-300 \
words) — segments are voiced separately and stitched, so they must stand alone without \
mid-sentence handoffs. Aim for 5-8 segments. Speaker must be exactly \
"{config.HOST_A}" or "{config.HOST_B}"."""


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


def generate_script(items: list[Item], date: str) -> Script:
    """One Claude call, retried twice. Raises ScriptError if it can't produce one."""
    import anthropic

    client = anthropic.Anthropic(api_key=config.anthropic_key())
    user_prompt = build_user_prompt(items, date)
    last_error: Exception | None = None

    for attempt in range(1, config.SCRIPT_RETRIES + 2):
        try:
            response = client.messages.create(
                model=config.SCRIPT_MODEL,
                max_tokens=config.SCRIPT_MAX_TOKENS,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_prompt}],
            )
            usage = response.usage
            log.info("script tokens: in=%d out=%d", usage.input_tokens, usage.output_tokens)

            script = parse_script_json(
                "".join(block.text for block in response.content if block.type == "text"), date
            )
            words = script.word_count()
            log.info("script: %d words across %d segments", words, len(script.segments))
            if not config.WORD_TARGET_MIN * 0.8 <= words <= config.WORD_TARGET_MAX * 1.2:
                log.warning(
                    "script is %d words, well outside the %d-%d target — episode duration "
                    "will drift; consider tightening the prompt",
                    words, config.WORD_TARGET_MIN, config.WORD_TARGET_MAX,
                )
            return script

        except Exception as exc:  # noqa: BLE001 - retry API and malformed-output alike
            last_error = exc
            log.warning("script attempt %d failed: %s", attempt, exc)
            if attempt <= config.SCRIPT_RETRIES:
                time.sleep(2.0**attempt)

    raise ScriptError(f"script generation failed after retries: {last_error}")
