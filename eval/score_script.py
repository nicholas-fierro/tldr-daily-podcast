"""Offline listening-evaluation harness (Linear spike NFI-89).

Scores a generated podcast script on the axes that determine whether the show
sounds like two people talking:

- turn responsiveness (is each non-opening turn contingent on the prior turn?)
- opener-token repetition ("And" / "Right" / "Exactly" starting turns)
- grounding fidelity (does any factual claim appear that is NOT in the
  supplied source input? — the load-bearing metric)
- sentence-length variance

All structural scores are computed with the standard library only: no model
call, no network, no weights. An optional LLM-as-judge verdict can be MERGED
into the report via --judge-file (produced externally by any tool you like);
this harness never calls a model itself, so it stays runnable in pytest and CI.

Usage:
    python3.11 eval/score_script.py \
        --script eval/fixtures/sample-script.synthetic.json \
        --input eval/fixtures/sample-input.synthetic.json

The source input is the writer's input payload: the JSON object produced by
src/script.py:build_user_prompt (the text after the heading line). A full
prompt file (heading + JSON) is also accepted.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import config  # noqa: E402

_WORD_RE = re.compile(r"[A-Za-z']+")
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")
_NUMBER_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")
_TITLECASE_RE = re.compile(r"\b[A-Z][a-z]{2,}\b")
_ACRONYM_RE = re.compile(r"\b[A-Z]{2,}\b")


# --- loading ----------------------------------------------------------------


def load_script(path: str | Path) -> dict:
    """Return {"date": ..., "segments": [{"topic": ..., "lines": [...]}, ...]}.

    Accepts both Script.to_dict() output (with date/word_count keys) and the
    raw model shape ({"segments": [...]}).
    """
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    segments = data.get("segments")
    if not isinstance(segments, list) or not segments:
        raise ValueError(f"{path}: no segments found")
    cleaned = []
    for segment in segments:
        lines = [
            {"speaker": str(line["speaker"]), "text": str(line["text"])}
            for line in segment.get("lines", [])
            if str(line.get("text", "")).strip()
        ]
        if lines:
            cleaned.append(
                {"topic": str(segment.get("topic") or "untitled"), "lines": lines}
            )
    if not cleaned:
        raise ValueError(f"{path}: every segment was empty")
    return {"date": str(data.get("date") or ""), "segments": cleaned}


def load_source_input(path: str | Path) -> dict:
    """Return {"edition_date": ..., "items": [...]} from a writer input file.

    Accepts the bare payload JSON or the full prompt text (heading line,
    blank line, then JSON) as emitted by build_user_prompt.
    """
    raw = Path(path).read_text(encoding="utf-8")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        payload = json.loads(raw.split("\n\n", 1)[1])
    items = payload.get("items")
    if not isinstance(items, list):
        raise ValueError(f"{path}: no items list found")
    return {"edition_date": str(payload.get("edition_date") or ""), "items": items}


def source_corpus(source: dict) -> str:
    """Everything the writer was allowed to claim: titles, blurbs, articles."""
    parts = [source.get("edition_date", "")]
    for item in source.get("items", []):
        parts.append(str(item.get("title") or ""))
        parts.append(str(item.get("blurb") or ""))
        if item.get("article_text"):
            parts.append(str(item["article_text"]))
    return "\n".join(parts)


def all_turns(script: dict) -> list[dict]:
    turns = []
    for index, segment in enumerate(script["segments"]):
        for line in segment["lines"]:
            turns.append(
                {"segment": index, "speaker": line["speaker"], "text": line["text"]}
            )
    return turns


# --- shared helpers ----------------------------------------------------------


def _words(text: str) -> list[str]:
    return [w.strip("'") for w in _WORD_RE.findall(text) if w.strip("'")]


def _content_words(text: str) -> set[str]:
    return {
        w.lower()
        for w in _words(text)
        if len(w) >= 4 and w.lower() not in config.EVAL_STOPWORDS
    }


def _first_word(text: str) -> str:
    words = _words(text)
    return words[0].lower() if words else ""


# --- axis 1: turn responsiveness ----------------------------------------------


def turn_reasons(text: str, prior_text: str) -> list[str]:
    """Heuristic contingency signals of a turn on the immediately prior turn."""
    reasons = []
    if text.strip().endswith("?"):
        reasons.append("question")
    if prior_text.strip().endswith("?"):
        reasons.append("answer")
    if _first_word(text) in config.EVAL_CONTINGENCY_MARKERS:
        reasons.append("discourse-marker")
    if _content_words(text) & _content_words(prior_text):
        reasons.append("lexical-overlap")
    if len(_words(text)) <= config.EVAL_BACKCHANNEL_MAX_WORDS:
        reasons.append("backchannel")
    return reasons


def _is_signoff(text: str) -> bool:
    """Does a line read as an audience-directed closing rather than a turn?"""
    lowered = text.lower()
    return any(marker in lowered for marker in config.EVAL_SIGNOFF_MARKERS)


def score_responsiveness(script: dict) -> dict:
    """Fraction of non-opening turns contingent on the prior turn.

    Each segment's first line is a segment opener (segments must stand alone
    for separate TTS rendering), and the closing of the final segment is the
    listener sign-off, which addresses the audience rather than the previous
    turn. A real sign-off is more than one line ("Thanks for listening." then
    "See you tomorrow."), so in the final segment any line that is the last
    line OR matches a sign-off marker is excluded; everything else must show
    contingency.
    """
    details = []
    for index, segment in enumerate(script["segments"]):
        lines = segment["lines"]
        is_last_segment = index == len(script["segments"]) - 1
        for position, line in enumerate(lines):
            if position == 0:
                continue
            if is_last_segment and (
                position == len(lines) - 1 or _is_signoff(line["text"])
            ):
                details.append({**line, "scored": False, "reason": "sign-off"})
                continue
            reasons = turn_reasons(line["text"], lines[position - 1]["text"])
            details.append({**line, "scored": True, "reasons": reasons})
    scored = [d for d in details if d["scored"]]
    contingent = [d for d in scored if d["reasons"]]
    score = len(contingent) / len(scored) if scored else 1.0
    return {
        "score": round(score, 3),
        "contingent": len(contingent),
        "scored": len(scored),
        "non_contingent_turns": [
            {"speaker": d["speaker"], "text": d["text"]} for d in scored
            if not d["reasons"]
        ],
    }


# --- axis 2: opener-token repetition ------------------------------------------


def score_opener_repetition(script: dict) -> dict:
    """Share of turns starting with a banned opener token ("And"/"Right"/...).

    The script prompt forbids repetitive starts; this measures compliance.
    Full marks up to EVAL_OPENER_TOLERANCE, falling linearly to zero at
    EVAL_OPENER_CAP.
    """
    turns = all_turns(script)
    hits = [
        {"speaker": t["speaker"], "text": t["text"]}
        for t in turns
        if _first_word(t["text"]) in config.EVAL_OPENER_TOKENS
    ]
    rate = len(hits) / len(turns) if turns else 0.0
    tolerance, cap = config.EVAL_OPENER_TOLERANCE, config.EVAL_OPENER_CAP
    if rate <= tolerance:
        score = 1.0
    elif rate >= cap:
        score = 0.0
    else:
        score = (cap - rate) / (cap - tolerance)
    return {
        "score": round(score, 3),
        "rate": round(rate, 3),
        "hits": len(hits),
        "turns": len(turns),
        "hit_turns": hits,
    }


# --- axis 3: grounding fidelity -----------------------------------------------


def _sentence_initial_tokens(text: str) -> set[str]:
    """Lower-cased first tokens of each sentence in a line."""
    return {
        first.lower()
        for sentence in _SENTENCE_SPLIT.split(text.strip())
        if (words := _words(sentence))
        for first in [words[0]]
    }


def checkable_claims(script: dict) -> list[dict]:
    """Numbers and likely named entities uttered in the script.

    A Titlecase token counts as a checkable entity only when it appears
    mid-sentence somewhere (proper nouns capitalise everywhere; ordinary
    words capitalise only at sentence starts). ALL-CAPS tokens count always.
    Host names are excluded: they are the show's own voices, not claims.
    """
    turns = all_turns(script)
    # Words in the show's own title are identity, not claims.
    title_words = {w.lower() for w in _WORD_RE.findall(config.PODCAST_TITLE)}
    mid_sentence: set[str] = set()
    for turn in turns:
        for sentence in _SENTENCE_SPLIT.split(turn["text"].strip()):
            words = _words(sentence)
            for word in words[1:]:
                if _TITLECASE_RE.fullmatch(word):
                    mid_sentence.add(word.lower())
    claims = []
    for turn in turns:
        initial = _sentence_initial_tokens(turn["text"])
        for match in _NUMBER_RE.findall(turn["text"]):
            claims.append(
                {"token": match.replace(",", ""), "kind": "number",
                 "speaker": turn["speaker"], "text": turn["text"]}
            )
        for match in _ACRONYM_RE.findall(turn["text"]):
            if match.lower() not in config.EVAL_STOPWORDS:
                claims.append(
                    {"token": match, "kind": "entity",
                     "speaker": turn["speaker"], "text": turn["text"]}
                )
        for match in _TITLECASE_RE.findall(turn["text"]):
            lowered = match.lower()
            if len(match) < config.EVAL_ENTITY_MIN_LEN:
                continue
            if lowered in config.EVAL_STOPWORDS or lowered in title_words:
                continue
            if match in (config.HOST_A, config.HOST_B):
                continue
            if lowered not in mid_sentence and lowered in initial:
                continue
            claims.append(
                {"token": match, "kind": "entity",
                 "speaker": turn["speaker"], "text": turn["text"]}
            )
    return claims


def score_grounding(script: dict, source: dict) -> dict:
    """Share of checkable tokens with no lexical support in the source input.

    Case-insensitive substring match against titles, blurbs, article texts,
    and the edition date, with commas stripped so "2,100" matches "2100".
    A lexical check, not a semantic one: paraphrase can false-positive and
    same-string-different-meaning can false-negative. Every unsupported token
    is reported for human review — the list is the point, the score is the
    summary.
    """
    corpus = source_corpus(source).lower().replace(",", "")
    claims = checkable_claims(script)
    unsupported = [
        {"token": c["token"], "kind": c["kind"],
         "speaker": c["speaker"], "text": c["text"]}
        for c in claims
        if c["token"].lower().replace(",", "") not in corpus
    ]
    score = 1.0 - len(unsupported) / len(claims) if claims else 1.0
    return {
        "score": round(score, 3),
        "checked": len(claims),
        "unsupported": unsupported,
    }


# --- axis 4: sentence-length variance ------------------------------------------


def sentence_lengths(script: dict) -> list[int]:
    lengths = []
    for turn in all_turns(script):
        for sentence in _SENTENCE_SPLIT.split(turn["text"].strip()):
            words = _words(sentence)
            if words:
                lengths.append(len(words))
    return lengths


def score_sentence_variance(script: dict) -> dict:
    """Coefficient of variation of sentence lengths vs EVAL_SENTENCE_CV_TARGET.

    Monotone same-length sentences (low CV) read as one voice, not two
    people talking. Full marks at or above the target CV.
    """
    lengths = sentence_lengths(script)
    if len(lengths) < 2:
        return {"score": 0.0, "mean": 0.0, "stdev": 0.0, "cv": 0.0,
                "sentences": len(lengths)}
    mean = sum(lengths) / len(lengths)
    variance = sum((length - mean) ** 2 for length in lengths) / len(lengths)
    stdev = variance**0.5
    cv = stdev / mean if mean else 0.0
    score = min(1.0, cv / config.EVAL_SENTENCE_CV_TARGET) if mean else 0.0
    return {
        "score": round(score, 3),
        "mean": round(mean, 2),
        "stdev": round(stdev, 2),
        "cv": round(cv, 3),
        "sentences": len(lengths),
    }


# --- report -------------------------------------------------------------------


def score_script(script: dict, source: dict, judge: dict | None = None) -> dict:
    """Score one script against its source input on all structural axes."""
    turns = all_turns(script)
    words = sum(len(line["text"].split()) for line in turns)
    axes = {
        "responsiveness": score_responsiveness(script),
        "opener_repetition": score_opener_repetition(script),
        "grounding": score_grounding(script, source),
        "sentence_variance": score_sentence_variance(script),
    }
    overall = sum(axis["score"] for axis in axes.values()) / len(axes)
    return {
        "script": {
            "date": script.get("date", ""),
            "segments": len(script["segments"]),
            "turns": len(turns),
            "words": words,
        },
        "axes": axes,
        "overall": round(overall, 3),
        "notes": [
            "Segment-seam audibility is not scored here: it needs rendered "
            "audio, which this offline harness deliberately excludes.",
            "Grounding is lexical, not semantic: review every token in "
            "axes.grounding.unsupported before trusting the score.",
        ],
        "judge": judge,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Score a podcast script on listening-naturalness axes (offline)."
    )
    parser.add_argument("--script", required=True, help="Script JSON file.")
    parser.add_argument(
        "--input", required=True, help="Writer source-input payload JSON (or full prompt)."
    )
    parser.add_argument(
        "--judge-file",
        default=None,
        help="Optional externally produced LLM-as-judge verdict JSON to merge "
        "into the report. The harness never calls a model itself.",
    )
    parser.add_argument("--output", default=None, help="Write report JSON here.")
    args = parser.parse_args(argv)

    script = load_script(args.script)
    source = load_source_input(args.input)
    judge = None
    if args.judge_file:
        judge = json.loads(Path(args.judge_file).read_text(encoding="utf-8"))
    report = score_script(script, source, judge)
    rendered = json.dumps(report, indent=2, ensure_ascii=False)
    if args.output:
        Path(args.output).write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
