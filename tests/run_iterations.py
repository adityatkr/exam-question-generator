"""Runs every logged prompt version against the real API.

The course deliverable requires that the observed effect recorded in
prompts/iteration_log.md comes from an actual run, not from guesswork. This
script holds the v1..v4 text of both the generator prompt and the
quality-check prompt, sends each version to Claude on a fixed input, and
records what came back.

Usage:
    python tests/run_iterations.py                 # all versions
    python tests/run_iterations.py --only gen      # generator versions only
    python tests/run_iterations.py --only qc       # quality-check versions only

Writes tests/output/iterations.json with, per version: the raw reply, whether
it parsed as JSON, whether it passed validation, and the measured metrics the
log cites (question count, MCQ option counts, answer-in-options, Bloom labels,
score spread, and so on).
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except (AttributeError, OSError):  # pragma: no cover
        pass

import config  # noqa: E402
from core import prompt_loader  # noqa: E402
from core.claude_client import ClaudeError, call_claude, get_api_key  # noqa: E402
from core.validators import extract_json, validate_questions  # noqa: E402

OUT_DIR = Path(__file__).resolve().parent / "output"

# A single fixed input, used for every version so the comparison is fair.
SUBJECT = "Operating Systems"
NOTES = """
CPU Scheduling. The short-term scheduler selects a process from the ready
queue and allocates the CPU to it. First-Come First-Served (FCFS) is
non-preemptive and suffers from the convoy effect, where short processes wait
behind one long process. Shortest Job First (SJF) gives the lowest average
waiting time of any non-preemptive algorithm but requires knowing the next CPU
burst length, which is not available in practice. Round Robin (RR) is
preemptive and assigns each process a fixed time quantum; if the quantum is
too large RR degenerates to FCFS, and if it is too small the context-switch
overhead dominates. Priority scheduling can cause starvation of low-priority
processes, which is solved by ageing: gradually increasing the priority of
processes that wait a long time.
""".strip()
NUM = 4
MARKS = 2
DIFFICULTY = "medium"
BLOOM = "Apply"
TYPES = "MCQ"


# ===========================================================================
# GENERATOR PROMPT VERSIONS
# ===========================================================================
# v1: bare instruction, no role, no format spec.
GEN_V1_SYSTEM = ""
GEN_V1_USER = f"""Make {NUM} exam questions about {SUBJECT} from these notes.

{NOTES}"""

# v2: + role, + explicit JSON format request.
GEN_V2_SYSTEM = "You are a university professor who sets exam papers."
GEN_V2_USER = f"""Create {NUM} multiple-choice exam questions on {SUBJECT}
from the notes below. Difficulty: {DIFFICULTY}. Marks each: {MARKS}.

Notes:
{NOTES}

Return the result as JSON with a "questions" list. Each question should have
the question text, four options, the correct answer, an explanation, the
difficulty, the Bloom's taxonomy level and the marks."""

# v3: + strict schema with field names and enums, + output contract, + constraints.
GEN_V3_SYSTEM = """You are an experienced university examination setter.
You write exam questions that are ready to print.

Rules:
- Every MCQ has exactly one correct option and three plausible wrong options.
- Never use "all of the above" or "both A and B".
- The answer must be the full text of the correct option.
- Do not invent facts that are not in the source material.

Reply with a single JSON object and nothing else. No prose, no markdown code
fences."""
GEN_V3_USER = f"""Generate exam questions from the material below.

<source_material subject="{SUBJECT}">
{NOTES}
</source_material>

Specification:
- Number of questions: exactly {NUM}
- Question types allowed: {TYPES}
- Difficulty: {DIFFICULTY}
- Bloom's taxonomy level: {BLOOM}
- Marks per question: {MARKS}

Required JSON shape:

{{
  "questions": [
    {{
      "id": 1,
      "question": "string",
      "type": "MCQ | Short Answer | Long Answer | True/False",
      "options": ["string", "string", "string", "string"],
      "answer": "string - verbatim text of the correct option",
      "explanation": "string",
      "difficulty": "easy | medium | hard",
      "bloom_level": "Remember | Understand | Apply | Analyze | Evaluate | Create",
      "marks": 0
    }}
  ]
}}

Return the JSON object now."""

# v4 = FINAL: the shipped prompts, rendered from /prompts.
GEN_V4_SYSTEM = prompt_loader.load("generator_system")
GEN_V4_USER = prompt_loader.render(
    "generator_user",
    subject=SUBJECT,
    source_kind="notes",
    source_text=NOTES,
    num_questions=NUM,
    question_types=TYPES,
    difficulty=DIFFICULTY,
    bloom_level=BLOOM,
    marks=MARKS,
)

GENERATOR_VERSIONS = [
    ("gen-v1", "bare instruction, no role, no format", GEN_V1_SYSTEM, GEN_V1_USER),
    ("gen-v2", "+ role, + 'return JSON' in prose", GEN_V2_SYSTEM, GEN_V2_USER),
    ("gen-v3", "+ explicit schema, enums, output contract, MCQ constraints",
     GEN_V3_SYSTEM, GEN_V3_USER),
    ("gen-v4", "FINAL: + grounding/Bloom/marks/duplicate/language rules, "
               "+ unusable-source escape hatch", GEN_V4_SYSTEM, GEN_V4_USER),
]


# ===========================================================================
# QUALITY-CHECK PROMPT VERSIONS
# ===========================================================================
# A fixed, deliberately defective question set, so every version is judged on
# the same material and we can see which defects each version catches.
#
# Planted defects:
#   id 1 - clean (control).
#   id 2 - TWO correct options (RR and SJF are both preemptive-related traps):
#          "all of the above" style ambiguity; answer key also arguable.
#   id 3 - pure recall but LABELLED "Apply" (Bloom mismatch).
#   id 4 - duplicate of id 1, reworded.
#   id 5 - answer key is WRONG (ageing solves starvation, not the convoy effect).
#   id 6 - not answerable from the source (asks about MLFQ, absent from notes).
DEFECTIVE_SET = [
    {
        "id": 1,
        "question": "Which CPU scheduling algorithm is non-preemptive and suffers from the convoy effect?",
        "type": "MCQ",
        "options": ["First-Come First-Served", "Round Robin", "Priority with ageing", "Multilevel queue"],
        "answer": "First-Come First-Served",
        "explanation": "FCFS is non-preemptive and short processes queue behind one long process.",
        "difficulty": "medium",
        "bloom_level": "Understand",
        "marks": 2,
    },
    {
        "id": 2,
        "question": "Which statement about Round Robin scheduling is true?",
        "type": "MCQ",
        "options": [
            "A very large time quantum makes it behave like FCFS",
            "A very small time quantum raises context-switch overhead",
            "It is a preemptive algorithm",
            "It requires knowing the next CPU burst length",
        ],
        "answer": "A very large time quantum makes it behave like FCFS",
        "explanation": "If the quantum exceeds the longest burst, RR degenerates to FCFS.",
        "difficulty": "medium",
        "bloom_level": "Understand",
        "marks": 2,
    },
    {
        "id": 3,
        "question": "What does the abbreviation SJF stand for?",
        "type": "Short Answer",
        "options": [],
        "answer": "Shortest Job First",
        "explanation": "SJF is Shortest Job First.",
        "difficulty": "easy",
        "bloom_level": "Apply",
        "marks": 2,
    },
    {
        "id": 4,
        "question": "Name the scheduling algorithm in which a long process delays the short processes queued behind it.",
        "type": "Short Answer",
        "options": [],
        "answer": "First-Come First-Served",
        "explanation": "This is the convoy effect under FCFS.",
        "difficulty": "medium",
        "bloom_level": "Understand",
        "marks": 2,
    },
    {
        "id": 5,
        "question": "Which problem does the technique of ageing solve in priority scheduling?",
        "type": "MCQ",
        "options": ["The convoy effect", "Starvation", "Thrashing", "Deadlock"],
        "answer": "The convoy effect",
        "explanation": "Ageing raises the priority of long-waiting processes.",
        "difficulty": "medium",
        "bloom_level": "Understand",
        "marks": 2,
    },
    {
        "id": 6,
        "question": "In a multilevel feedback queue, what happens to a process that exhausts its time quantum at the highest-priority level?",
        "type": "Short Answer",
        "options": [],
        "answer": "It is demoted to the next lower-priority queue.",
        "explanation": "MLFQ demotes CPU-bound processes to lower queues.",
        "difficulty": "hard",
        "bloom_level": "Understand",
        "marks": 2,
    },
]
DEFECTIVE_JSON = json.dumps(DEFECTIVE_SET, indent=2, ensure_ascii=False)

# v1: vague ask, no scale, no criteria, no format.
QC_V1_SYSTEM = ""
QC_V1_USER = f"""Check these exam questions and tell me if they are good.

{DEFECTIVE_JSON}"""

# v2: + reviewer role, + a 0-10 score, + JSON asked for in prose.
QC_V2_SYSTEM = "You are a reviewer who checks the quality of exam questions."
QC_V2_USER = f"""Review each of the following exam questions. Give each one a
score out of 10 and list any problems you find.

{DEFECTIVE_JSON}

Return JSON with a "reviews" list containing the id, the score and the issues."""

# v3: + the six named criteria, + schema, + duplicate detection.
QC_V3_SYSTEM = """You are a moderation officer on a university examination
board. You review question papers drafted by teachers and either approve them
or send them back.

Judge each question on: clarity, correctness of the answer key, whether MCQs
have a single unambiguous answer, whether the difficulty matches, whether the
Bloom's taxonomy level matches, and whether it is relevant to the source
material. Also flag duplicates.

Reply with a single JSON object and nothing else. No prose, no markdown code
fences."""
QC_V3_USER = f"""Review the question set below.

Specification: subject {SUBJECT}, difficulty {DIFFICULTY}, Bloom's level
{BLOOM}, {MARKS} marks per question.

<source_material>
{NOTES}
</source_material>

Question set:
{DEFECTIVE_JSON}

Return:

{{
  "reviews": [
    {{
      "id": 1,
      "score": 0.0,
      "issues": ["string"],
      "suggested_fix": "string",
      "duplicate_of": null
    }}
  ]
}}

Return the JSON object now."""

# v4 = FINAL: the shipped prompts, rendered from /prompts.
QC_V4_SYSTEM = prompt_loader.load("quality_check_system")
QC_V4_USER = prompt_loader.render(
    "quality_check_user",
    subject=SUBJECT,
    difficulty=DIFFICULTY,
    bloom_level=BLOOM,
    marks=MARKS,
    question_types=TYPES,
    source_text=NOTES,
    questions_json=DEFECTIVE_JSON,
    num_questions=len(DEFECTIVE_SET),
)

QC_VERSIONS = [
    ("qc-v1", "vague ask, no scale, no criteria, no format", QC_V1_SYSTEM, QC_V1_USER),
    ("qc-v2", "+ reviewer role, + score out of 10, + 'return JSON' in prose",
     QC_V2_SYSTEM, QC_V2_USER),
    ("qc-v3", "+ six named criteria, + schema, + duplicate detection",
     QC_V3_SYSTEM, QC_V3_USER),
    ("qc-v4", "FINAL: + per-criterion sub-scores, + score = min(criteria), "
              "+ calibrated 1-10 rubric, + do-not-invent-issues instruction",
     QC_V4_SYSTEM, QC_V4_USER),
]


# ===========================================================================
# Measurement
# ===========================================================================

def measure_generator(raw: str) -> dict[str, Any]:
    """Objective metrics the iteration log cites for a generator version."""
    metrics: dict[str, Any] = {
        "reply_chars": len(raw),
        "has_code_fence": "```" in raw,
        "starts_with_brace": raw.strip().startswith("{"),
        "prose_before_json": bool(re.match(r"^\s*[A-Za-z]", raw)),
    }
    try:
        payload = extract_json(raw)
    except ValueError as exc:
        metrics.update(
            {"parsed": False, "parse_error": str(exc), "valid": False}
        )
        return metrics
    metrics["parsed"] = True

    questions, errors = validate_questions(payload, NUM)
    metrics["validator_errors"] = errors
    metrics["valid"] = not errors
    metrics["question_count"] = len(payload.get("questions", []) or [])

    raw_qs = payload.get("questions") or []
    if isinstance(raw_qs, list) and raw_qs and isinstance(raw_qs[0], dict):
        keys = sorted(raw_qs[0].keys())
        metrics["first_question_keys"] = keys
        metrics["missing_schema_fields"] = sorted(
            {
                "id", "question", "type", "options", "answer",
                "explanation", "difficulty", "bloom_level", "marks",
            }
            - set().union(*[set(q.keys()) for q in raw_qs if isinstance(q, dict)])
        )
        metrics["option_counts"] = [
            len(q.get("options") or []) for q in raw_qs if isinstance(q, dict)
        ]
        metrics["answer_in_options"] = [
            bool(
                q.get("options")
                and str(q.get("answer", "")).strip()
                in [str(o).strip() for o in q["options"]]
            )
            for q in raw_qs
            if isinstance(q, dict)
        ]
        metrics["bloom_labels"] = [
            q.get("bloom_level") for q in raw_qs if isinstance(q, dict)
        ]
        metrics["marks_values"] = [
            q.get("marks") for q in raw_qs if isinstance(q, dict)
        ]
        metrics["banned_option_phrases"] = sorted(
            {
                str(o)
                for q in raw_qs
                if isinstance(q, dict)
                for o in (q.get("options") or [])
                if re.search(
                    r"all of the above|none of the above|both [ab] and",
                    str(o),
                    re.IGNORECASE,
                )
            }
        )
    return metrics


PLANTED = {
    2: "two defensibly correct options / ambiguous key",
    3: "recall question labelled Apply (Bloom mismatch)",
    4: "duplicate of question 1",
    5: "wrong answer key (ageing solves starvation)",
    6: "not answerable from the source (MLFQ absent)",
}


def measure_quality(raw: str) -> dict[str, Any]:
    """Objective metrics the iteration log cites for a quality-check version."""
    metrics: dict[str, Any] = {
        "reply_chars": len(raw),
        "has_code_fence": "```" in raw,
        "starts_with_brace": raw.strip().startswith("{"),
    }
    try:
        payload = extract_json(raw)
    except ValueError as exc:
        metrics.update({"parsed": False, "parse_error": str(exc)})
        return metrics
    metrics["parsed"] = True

    reviews = payload.get("reviews")
    if not isinstance(reviews, list):
        metrics["has_reviews_list"] = False
        return metrics
    metrics["has_reviews_list"] = True
    metrics["review_count"] = len(reviews)

    scores: dict[int, float] = {}
    issues: dict[int, list[str]] = {}
    has_sub_scores = False
    for item in reviews:
        if not isinstance(item, dict):
            continue
        try:
            rid = int(item.get("id"))
        except (TypeError, ValueError):
            continue
        try:
            scores[rid] = float(item.get("score"))
        except (TypeError, ValueError):
            pass
        raw_issues = item.get("issues") or []
        if isinstance(raw_issues, str):
            raw_issues = [raw_issues]
        issues[rid] = [str(i) for i in raw_issues]
        if isinstance(item.get("scores"), dict):
            has_sub_scores = True

    metrics["has_sub_scores"] = has_sub_scores
    metrics["scores"] = {str(k): scores[k] for k in sorted(scores)}
    if scores:
        values = list(scores.values())
        metrics["score_min"] = min(values)
        metrics["score_max"] = max(values)
        metrics["score_spread"] = round(max(values) - min(values), 2)
        metrics["score_mean"] = round(statistics.fmean(values), 2)
        metrics["clean_control_score"] = scores.get(1)

    # Which planted defects did this version actually catch?
    caught = []
    for qid in PLANTED:
        score = scores.get(qid)
        flagged_by_score = score is not None and score < config.QUALITY_THRESHOLD
        flagged_by_issue = bool(issues.get(qid))
        if flagged_by_score or flagged_by_issue:
            caught.append(qid)
    metrics["planted_defects_caught"] = caught
    metrics["planted_defects_caught_n"] = f"{len(caught)}/{len(PLANTED)}"
    metrics["planted_defects_missed"] = [
        f"q{q}: {PLANTED[q]}" for q in PLANTED if q not in caught
    ]
    # False positive: the clean control question flagged.
    control = scores.get(1)
    metrics["false_positive_on_control"] = bool(
        (control is not None and control < config.QUALITY_THRESHOLD)
        or issues.get(1)
    )
    metrics["duplicate_detected"] = any(
        isinstance(item, dict) and item.get("duplicate_of") not in (None, "", "null")
        for item in reviews
    )
    return metrics


# ===========================================================================
# Runner
# ===========================================================================

def run_version(
    name: str, change: str, system: str, user: str, kind: str
) -> dict[str, Any]:
    print(f"\n--- {name}: {change}")
    started = time.time()
    record: dict[str, Any] = {
        "version": name,
        "kind": kind,
        "change": change,
        "system_prompt": system,
        "user_prompt": user,
        "system_chars": len(system),
        "user_chars": len(user),
    }
    try:
        response = call_claude(
            system=system or "You are a helpful assistant.",
            user=user,
            max_tokens=8000,
            effort=config.GENERATOR_EFFORT,
        )
    except ClaudeError as exc:
        record["error"] = str(exc)
        print(f"    API error: {exc}")
        return record

    record.update(
        {
            "raw_reply": response.text,
            "stop_reason": response.stop_reason,
            "input_tokens": response.input_tokens,
            "output_tokens": response.output_tokens,
            "seconds": round(time.time() - started, 1),
        }
    )
    record["metrics"] = (
        measure_generator(response.text)
        if kind == "generator"
        else measure_quality(response.text)
    )

    m = record["metrics"]
    if kind == "generator":
        print(
            f"    parsed={m.get('parsed')} valid={m.get('valid')} "
            f"count={m.get('question_count')} "
            f"fence={m.get('has_code_fence')} "
            f"options={m.get('option_counts')} "
            f"answer_in_options={m.get('answer_in_options')} "
            f"bloom={m.get('bloom_labels')}"
        )
        if m.get("validator_errors"):
            print(f"    validator: {m['validator_errors']}")
    else:
        print(
            f"    parsed={m.get('parsed')} reviews={m.get('review_count')} "
            f"sub_scores={m.get('has_sub_scores')} "
            f"caught={m.get('planted_defects_caught_n')} "
            f"spread={m.get('score_spread')} "
            f"control={m.get('clean_control_score')} "
            f"dup={m.get('duplicate_detected')}"
        )
        if m.get("planted_defects_missed"):
            print(f"    missed: {m['planted_defects_missed']}")
    print(
        f"    {record['input_tokens']} in / {record['output_tokens']} out "
        f"tokens, {record['seconds']}s"
    )
    return record


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", choices=["gen", "qc"], help="run one family")
    args = parser.parse_args()

    if not get_api_key():
        print("ANTHROPIC_API_KEY is not set.")
        return 2

    print(f"Model: {config.MODEL_ID}  (effort: {config.GENERATOR_EFFORT})")
    print(f"Fixed input: {SUBJECT}, {NUM} MCQs, {DIFFICULTY}, Bloom {BLOOM}")

    records: list[dict[str, Any]] = []
    if args.only != "qc":
        print("\n" + "=" * 70)
        print("GENERATOR PROMPT: v1 -> v4")
        print("=" * 70)
        for name, change, system, user in GENERATOR_VERSIONS:
            records.append(run_version(name, change, system, user, "generator"))

    if args.only != "gen":
        print("\n" + "=" * 70)
        print("QUALITY-CHECK PROMPT: v1 -> v4")
        print("=" * 70)
        for name, change, system, user in QC_VERSIONS:
            records.append(run_version(name, change, system, user, "quality"))

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / "iterations.json"
    path.write_text(
        json.dumps(records, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )

    total_in = sum(r.get("input_tokens", 0) for r in records)
    total_out = sum(r.get("output_tokens", 0) for r in records)
    print(f"\n{'=' * 70}")
    print(
        f"{len(records)} version(s) run · {total_in:,} input / "
        f"{total_out:,} output tokens · "
        f"~${(total_in / 1e6) * 4 + (total_out / 1e6) * 20:.4f}"
    )
    print(f"Wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
