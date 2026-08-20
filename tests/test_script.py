"""Script assembly: prompt construction and response parsing.

The prompt's *quality* can only be judged by reading an episode (M3's
checkpoint). What is testable here is that the model gets the right facts and
that malformed output is caught rather than voiced.
"""

import json

import pytest

from src import config, script
from src.parse import Item


def items() -> list[Item]:
    enriched = Item(section="Big Tech", title="Acquisition", url="https://e.com/1",
                    blurb="Short blurb.", read_time=4)
    enriched.text = "The full article text, at length."
    enriched.enriched = True

    unenriched = Item(section="Quick Links", title="Paywalled", url="https://e.com/2",
                      blurb="Only the blurb.", read_time=2)
    unenriched.text = "Only the blurb."
    return [enriched, unenriched]


# --- prompt ---------------------------------------------------------------

def test_prompt_carries_every_item():
    payload = json.loads(script.build_user_prompt(items(), "2026-08-20").split("\n\n", 1)[1])
    assert payload["item_count"] == 2
    assert {i["title"] for i in payload["items"]} == {"Acquisition", "Paywalled"}


def test_enriched_items_ship_their_article_text():
    payload = json.loads(script.build_user_prompt(items(), "2026-08-20").split("\n\n", 1)[1])
    enriched = next(i for i in payload["items"] if i["title"] == "Acquisition")
    assert enriched["enriched"] is True
    assert enriched["article_text"] == "The full article text, at length."


def test_unenriched_items_send_no_article_text():
    """The model must be able to tell 'we did not read this' from 'this is short'."""
    payload = json.loads(script.build_user_prompt(items(), "2026-08-20").split("\n\n", 1)[1])
    unenriched = next(i for i in payload["items"] if i["title"] == "Paywalled")
    assert unenriched["enriched"] is False
    assert unenriched["article_text"] is None
    assert unenriched["blurb"] == "Only the blurb."


def test_system_prompt_states_the_hard_constraints():
    prompt = script.SYSTEM_PROMPT
    assert str(config.WORD_TARGET_MIN) in prompt and str(config.WORD_TARGET_MAX) in prompt
    assert config.HOST_A in prompt and config.HOST_B in prompt
    assert "enriched=false" in prompt          # the hedging rule
    assert "Cold open" in prompt               # no throat-clearing
    assert "omit weak items" in prompt         # permission to cut


# --- response parsing -----------------------------------------------------

VALID = json.dumps({
    "segments": [
        {"topic": "ai-infra", "lines": [
            {"speaker": config.HOST_A, "text": "Big acquisition today."},
            {"speaker": config.HOST_B, "text": "Four billion, all cash."},
        ]},
        {"topic": "research", "lines": [
            {"speaker": config.HOST_B, "text": "Eight hundred cycles."},
        ]},
    ]
})


def test_parses_valid_output():
    parsed = script.parse_script_json(VALID, "2026-08-20")
    assert len(parsed.segments) == 2
    assert parsed.segments[0].topic == "ai-infra"
    assert parsed.segments[0].lines[0].speaker == config.HOST_A


def test_word_count_spans_all_segments():
    assert script.parse_script_json(VALID, "2026-08-20").word_count() == 10


def test_fenced_json_is_recovered():
    parsed = script.parse_script_json(f"```json\n{VALID}\n```", "2026-08-20")
    assert len(parsed.segments) == 2


def test_json_with_surrounding_prose_is_recovered():
    parsed = script.parse_script_json(f"Here is the script:\n{VALID}\nHope that works!",
                                      "2026-08-20")
    assert len(parsed.segments) == 2


def test_whitespace_in_lines_is_collapsed():
    raw = json.dumps({"segments": [{"topic": "t", "lines": [
        {"speaker": config.HOST_A, "text": "Line   with\n\nragged   spacing."}]}]})
    parsed = script.parse_script_json(raw, "2026-08-20")
    assert parsed.segments[0].lines[0].text == "Line with ragged spacing."


def test_empty_lines_are_dropped():
    raw = json.dumps({"segments": [{"topic": "t", "lines": [
        {"speaker": config.HOST_A, "text": "Real line."},
        {"speaker": config.HOST_B, "text": "   "}]}]})
    parsed = script.parse_script_json(raw, "2026-08-20")
    assert len(parsed.segments[0].lines) == 1


def test_unknown_speaker_is_rejected():
    """A third voice would silently vanish at TTS time — two speakers is the ceiling."""
    raw = json.dumps({"segments": [{"topic": "t", "lines": [
        {"speaker": "Narrator", "text": "Hello."}]}]})
    with pytest.raises(script.ScriptError):
        script.parse_script_json(raw, "2026-08-20")


def test_no_segments_is_rejected():
    with pytest.raises(script.ScriptError):
        script.parse_script_json(json.dumps({"segments": []}), "2026-08-20")


def test_all_empty_segments_is_rejected():
    raw = json.dumps({"segments": [{"topic": "t", "lines": []}]})
    with pytest.raises(script.ScriptError):
        script.parse_script_json(raw, "2026-08-20")


def test_unparseable_output_raises():
    with pytest.raises(Exception):
        script.parse_script_json("not json at all", "2026-08-20")


def test_serialized_script_round_trips():
    parsed = script.parse_script_json(VALID, "2026-08-20")
    data = parsed.to_dict()
    assert data["date"] == "2026-08-20"
    assert data["word_count"] == 10
    assert data["segments"][0]["lines"][0]["speaker"] == config.HOST_A
