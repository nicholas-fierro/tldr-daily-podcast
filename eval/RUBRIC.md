# Listening-evaluation rubric (spike NFI-89)

A repeatable, offline yardstick for the experiments that try to make the show
sound like two people talking (richer TTS direction, responsive turns, a
critic pass). Every axis is computable with the standard library only — no
model call, no network — via `eval/score_script.py`. Run it as:

```sh
python3.11 eval/score_script.py \
    --script eval/fixtures/sample-script.synthetic.json \
    --input eval/fixtures/sample-input.synthetic.json
```

Tunables live in `src/config.py` under the `EVAL_` prefix, per the repo rule
that tunables never hide in modules.

## Axes

### 1. Turn responsiveness (0–1, higher is better)

**What it measures.** Whether each non-opening turn is contingent on the
immediately prior turn — a question, an answer, an acknowledgement, a
challenge, or a visible lexical thread. Two hosts who never pick up each
other's words are two monologues, not a conversation.

**How it is scored.** Each segment's first line is a segment opener (segments
are voiced separately and must stand alone), and the final line of the final
segment is the listener sign-off (it addresses the audience, not the prior
turn). Both are excluded. Every other turn is contingent if it shows any of:

- ends with `?` (a follow-up question) — `question`;
- follows a prior turn ending with `?` (an answer) — `answer`;
- starts with a contingency marker (`EVAL_CONTINGENCY_MARKERS`: yes / right /
  exactly / but / however / actually / …) — `discourse-marker`;
- shares a content word (length ≥ 4, outside `EVAL_STOPWORDS`) with the prior
  turn — `lexical-overlap`;
- is at most `EVAL_BACKCHANNEL_MAX_WORDS` words with no other signal
  ("Got it", "Makes sense") — `backchannel`.

Score = contingent / scored. The report lists every non-contingent turn, which
is the actionable output: each one is a candidate for the responsive-turns
experiment to fix.

### 2. Opener-token repetition (0–1, higher is better)

**What it measures.** Compliance with the script prompt's "avoid repetitive
starts such as And, Right, Exactly" rule. Counted over *all* turns, openers
included.

**How it is scored.** Share of turns whose first word is in
`EVAL_OPENER_TOKENS` (and / right / exactly / so / well / okay / yeah). Full
marks at or under `EVAL_OPENER_TOLERANCE` (0.05 — a rare discourse "Right" is
conversation), falling linearly to zero at `EVAL_OPENER_CAP` (0.30). The
report lists every hitting turn.

### 3. Grounding fidelity (0–1, higher is better) — the load-bearing metric

**What it measures.** Whether any factual claim in the script lacks support in
the writer's source input (item titles, blurbs, article texts, edition date —
the `build_user_prompt` payload in `src/script.py`). An unsupported number or
name on air is the failure the show's "never invent specifics" rule exists to
prevent.

**How it is scored.** Every number (`\d…`) and every likely named entity in
the script is a checkable claim. A Titlecase token counts only when it appears
mid-sentence somewhere (proper nouns capitalise everywhere; ordinary words
capitalise only at sentence starts); ALL-CAPS tokens always count; host names
and show-title words are excluded. Each claim passes if it appears as a
case-insensitive substring of the source corpus (commas stripped, so "2,100"
matches "2100"). Score = 1 − unsupported / checked.

**This check is lexical, not semantic.** Paraphrase can false-positive (same
meaning, different words) and same-string-different-meaning can
false-negative. The score is the summary; the `unsupported` list is the point
— review every token in it before trusting a script. A heteronym-grade
check needs a judge; see below.

### 4. Sentence-length variance (0–1, higher is better)

**What it measures.** Whether sentence lengths mix short reactions with long
explanations. Monotone equal-length sentences read as one voice.

**How it is scored.** Coefficient of variation (stdev / mean) of sentence
word-counts across all turns, relative to `EVAL_SENTENCE_CV_TARGET` (0.55):
`min(1, CV / target)`. The report includes mean, stdev, CV, and sentence
count.

### 5. Segment-seam audibility (not scored — noted only)

Whether the stitch points between separately voiced segments are audible
(tone jumps, repeated framing). It needs rendered audio, which this offline
harness deliberately excludes. Do not build audio analysis into this tool;
judge seams by listening.

## Optional LLM-as-judge

The harness never calls a model itself. To attach a judge verdict, produce it
with any external tool and pass `--judge-file verdict.json`; the JSON is
merged verbatim into the report's `judge` field. Suggested verdict shape:

```json
{"model": "<id>", "responsive": 4, "grounded": 5, "notes": "..."}
```

The judge is for semantic grounding and overall naturalness — the two things
lexical heuristics cannot see. It is optional and off by default; the four
structural scores must always stand alone.

## Overall

The report's `overall` is the unweighted mean of the four structural axes. It
exists for tracking experiments against each other, not for pass/fail: a
script can score 1.0 on structure and still mispronounce a name. When in
doubt, listen.
