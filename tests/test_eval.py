"""Structural scorers for the offline listening-evaluation harness (NFI-89).

Stdlib only: no model call, no network, no weights. The scorer module lives
outside the pipeline stages on purpose — it judges scripts, it never builds
them — so these tests import straight from eval/score_script.py.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from eval import score_script as scorer  # noqa: E402

FIXTURES = Path(__file__).resolve().parent.parent / "eval" / "fixtures"


def lines(*texts, speaker="Ava"):
    return [{"speaker": speaker, "text": text} for text in texts]


def script_of(*segments, date="2026-09-11"):
    return {
        "date": date,
        "segments": [
            {"topic": f"seg-{i}", "lines": segment}
            for i, segment in enumerate(segments)
        ],
    }


def source_of(*items, date="2026-09-11"):
    return {
        "edition_date": date,
        "items": [
            {
                "title": item.get("title", ""),
                "blurb": item.get("blurb", ""),
                "article_text": item.get("article_text"),
            }
            for item in items
        ],
    }


# --- loading ---------------------------------------------------------------


def test_load_script_accepts_to_dict_and_raw_model_shapes(tmp_path):
    shaped = {"date": "2026-09-11", "word_count": 3,
              "segments": [{"topic": "t", "lines": lines("Hello there.")}]}
    raw = {"segments": [{"topic": "t", "lines": lines("Hello there.")}]}
    for payload in (shaped, raw):
        path = tmp_path / "s.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        assert scorer.load_script(path)["segments"][0]["lines"][0]["text"] == \
            "Hello there."


def test_load_script_rejects_empty_segments(tmp_path):
    path = tmp_path / "s.json"
    path.write_text(json.dumps({"segments": []}), encoding="utf-8")
    with pytest.raises(ValueError):
        scorer.load_script(path)


def test_load_source_input_accepts_prompt_text(tmp_path):
    prompt = "Here is the TLDR TECH edition for 2026-08-20.\n\n" + json.dumps(
        {"edition_date": "2026-08-20",
         "items": [{"title": "T", "blurb": "b", "article_text": None}]}
    )
    path = tmp_path / "i.json"
    path.write_text(prompt, encoding="utf-8")
    assert scorer.load_source_input(path)["edition_date"] == "2026-08-20"


# --- responsiveness ----------------------------------------------------------


def test_question_answer_pair_is_contingent():
    script = script_of(
        lines("Welcome back. Big story today.", speaker="Ava")
        + lines("Why does it matter though?", speaker="Ben")
        + lines("Because the numbers moved, that is why.", speaker="Ava")
        + lines("Thanks for listening.", speaker="Ben"),
    )
    result = scorer.score_responsiveness(script)
    # Question and answer score; the final line is the sign-off, excluded.
    assert result["scored"] == 2
    assert result["contingent"] == 2
    assert result["score"] == 1.0


def test_segment_openers_and_signoff_are_excluded():
    script = script_of(
        lines("Segment one opener.", "Segment one follow-up sharing opener words."),
        lines("Segment two opener.", "That opener deserves a second sentence.",
              "Have a great rest of your day."),
    )
    result = scorer.score_responsiveness(script)
    assert result["scored"] == 2
    assert result["contingent"] == 2
    assert result["score"] == 1.0


def test_non_contingent_turn_is_reported():
    script = script_of(
        lines("The Harbor deal closed Tuesday.", speaker="Ava")
        + lines("Photonic qubits are quite promising for future hardware systems.",
                speaker="Ben")
        + lines("Thanks for listening.", speaker="Ava"),
    )
    result = scorer.score_responsiveness(script)
    assert result["scored"] == 1
    assert result["non_contingent_turns"][0]["text"].startswith("Photonic")


def test_multi_line_signoff_is_excluded():
    # A real closing spans several audience-directed lines, not just the last.
    script = script_of(
        lines("First topic opener.", "Contingent follow-up on that topic."),
        lines(
            "Final topic opener.",
            "Thanks for listening to Daily Standup, everyone.",
            "Whole new subject with zero contingency whatsoever here.",
            "See you tomorrow.",
        ),
    )
    result = scorer.score_responsiveness(script)
    # The "Thanks for listening" line and the "See you tomorrow." final line are
    # both exempt; only the middle non-contingent line is scored and flagged.
    assert result["scored"] == 2
    flagged = [t["text"] for t in result["non_contingent_turns"]]
    assert any(t.startswith("Whole new subject") for t in flagged)
    assert all("Thanks for listening" not in t for t in flagged)


# --- opener repetition -------------------------------------------------------


def test_clean_script_scores_full_marks():
    script = script_of(lines("Harbor closed today.", "The price moved fast."))
    result = scorer.score_opener_repetition(script)
    assert result == {"score": 1.0, "rate": 0.0, "hits": 0, "turns": 2,
                      "hit_turns": []}


def test_repetition_hits_are_listed():
    script = script_of(lines(
        "And then it closed.",
        *["The price moved fast for several hours today."] * 9,
    ))
    result = scorer.score_opener_repetition(script)
    assert result["hits"] == 1
    assert result["hit_turns"][0]["text"] == "And then it closed."
    assert result["rate"] == 0.1
    assert 0.0 < result["score"] < 1.0


# --- grounding ----------------------------------------------------------------


def test_supported_numbers_and_entities_pass():
    source = source_of({
        "title": "Northwind acquires Harbor AI",
        "blurb": "A $2.1 billion cash deal.",
        "article_text": "Chief executive Dana Whitfield announced the deal Tuesday.",
    })
    script = script_of(lines(
        "Northwind paid 2.1 billion dollars in cash, says chief "
        "executive Dana Whitfield."
    ))
    result = scorer.score_grounding(script, source)
    assert result["unsupported"] == []
    assert result["score"] == 1.0


def test_unsupported_number_is_flagged():
    source = source_of({"title": "A quiet launch", "blurb": "No numbers here."})
    script = script_of(lines("They raised 900 million dollars overnight."))
    result = scorer.score_grounding(script, source)
    assert [u["token"] for u in result["unsupported"]] == ["900"]
    assert result["score"] < 1.0


def test_sentence_initial_common_words_are_not_entities():
    """"The" / "That" at a sentence start are capitalisation, not claims."""
    source = source_of({"title": "T", "blurb": "b"})
    script = script_of(lines("The result was interesting. That seems promising."))
    assert scorer.checkable_claims(script) == []


def test_host_names_and_show_title_are_not_claims():
    source = source_of({"title": "T", "blurb": "b"})
    script = script_of(lines("Welcome back to Daily Standup. Ava, take it away."))
    assert scorer.checkable_claims(script) == []


# --- sentence variance ---------------------------------------------------------


def test_monotone_sentences_score_low():
    script = script_of(lines(
        "The deal closed on Tuesday morning.",
        "The price moved up quite quickly.",
        "The team reacted with real surprise.",
    ))
    result = scorer.score_sentence_variance(script)
    assert result["cv"] == 0.0
    assert result["score"] == 0.0


def test_mixed_lengths_score_full_marks():
    script = script_of(lines(
        "Right.",
        "The Lausanne Institute for Photonic Systems built a processor that "
        "held coherence far longer than anyone expected at room temperature.",
    ))
    result = scorer.score_sentence_variance(script)
    assert result["score"] == 1.0


# --- report -------------------------------------------------------------------


def test_report_merges_judge_and_marks_seams_out_of_scope():
    report = scorer.score_script(
        script_of(lines("Harbor closed today.", "The price moved fast.")),
        source_of({"title": "Harbor closed today",
                   "blurb": "The price moved fast"}),
        judge={"model": "external", "grounded": 5},
    )
    assert set(report["axes"]) == {"responsiveness", "opener_repetition",
                                   "grounding", "sentence_variance"}
    assert report["judge"] == {"model": "external", "grounded": 5}
    assert any("seam" in note for note in report["notes"])
    assert 0.0 <= report["overall"] <= 1.0


def test_frozen_fixture_pair_scores_clean_grounding():
    report = scorer.score_script(
        scorer.load_script(FIXTURES / "sample-script.synthetic.json"),
        scorer.load_source_input(FIXTURES / "sample-input.synthetic.json"),
    )
    assert report["script"]["turns"] == 10
    assert report["axes"]["grounding"] == {
        "score": 1.0, "checked": 33, "unsupported": []}
    assert report["overall"] > 0.8
