"""Script assembly: prompt construction and response parsing.

The prompt's *quality* can only be judged by reading an episode (M3's
checkpoint). What is testable here is that the model gets the right facts and
that malformed output is caught rather than voiced.
"""

import json

import httpx
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


def test_openrouter_provider_requests_strict_schema_and_reports_usage():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["headers"] = request.headers
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "model": "qwen/qwen3-30b-a3b-instruct-2507",
                "choices": [{"message": {"content": VALID}}],
                "usage": {
                    "prompt_tokens": 1_234,
                    "completion_tokens": 456,
                    "cost": 0.0012,
                },
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        provider = script.OpenRouterScriptProvider(
            api_key="test-key",
            model="qwen/qwen3-30b-a3b-instruct-2507",
            client=client,
        )
        result = provider.generate("system", "user")

    body = captured["body"]
    assert captured["url"] == "https://openrouter.ai/api/v1/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer test-key"
    assert body["model"] == "qwen/qwen3-30b-a3b-instruct-2507"
    assert body["response_format"]["type"] == "json_schema"
    assert body["response_format"]["json_schema"]["strict"] is True
    assert body["provider"]["require_parameters"] is True
    assert body["plugins"] == [{"id": "response-healing"}]
    speaker_schema = body["response_format"]["json_schema"]["schema"]["properties"][
        "segments"
    ]["items"]["properties"]["lines"]["items"]["properties"]["speaker"]
    assert speaker_schema["enum"] == [config.HOST_A, config.HOST_B]
    assert result.text == VALID
    assert result.input_tokens == 1_234
    assert result.output_tokens == 456
    assert result.cost_usd == 0.0012


def test_generate_script_accepts_an_injected_provider():
    class FakeProvider:
        def generate(self, system_prompt: str, user_prompt: str) -> script.ScriptGeneration:
            assert system_prompt == script.SYSTEM_PROMPT
            assert "Acquisition" in user_prompt
            return script.ScriptGeneration(
                text=VALID,
                model="test/model",
                input_tokens=100,
                output_tokens=20,
                cost_usd=0.0001,
            )

    generated = script.generate_script(items(), "2026-08-20", provider=FakeProvider())
    assert generated.date == "2026-08-20"
    assert len(generated.segments) == 2


def test_openrouter_http_error_is_a_script_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": "invalid key"}})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        provider = script.OpenRouterScriptProvider(api_key="bad-key", client=client)
        with pytest.raises(script.ScriptError, match="OpenRouter returned HTTP 401"):
            provider.generate("system", "user")


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
