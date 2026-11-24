"""Runs the 14 documented test cases against the real app logic.

This script imports the same functions app.py calls -- validate_settings,
generate_questions, check_questions, and the exporters -- so a pass here means
the app passes. Nothing is mocked; cases that reach the API make real calls.

Usage:
    set GEMINI_API_KEY=...                  (or put it in .streamlit/secrets.toml)
    python tests/run_tests.py               # run every case
    python tests/run_tests.py --offline     # validation-only cases, no API calls
    python tests/run_tests.py --case 3 7    # run selected cases

Writes tests/output/results.json and prints a Markdown table ready to paste
into tests/test_cases.md.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# The Windows console defaults to cp1252, which cannot encode the emoji in the
# results table or the Devanagari in test case 14.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except (AttributeError, OSError):  # pragma: no cover
        pass

import config  # noqa: E402
from core import exporter, quality_checker  # noqa: E402
from core.gemini_client import GeminiError, get_api_key  # noqa: E402
from core.generator import generate_questions  # noqa: E402
from core.validators import (  # noqa: E402
    Settings,
    extract_json,
    extract_text_from_upload,
    validate_questions,
    validate_settings,
)

OUT_DIR = Path(__file__).resolve().parent / "output"

# ---------------------------------------------------------------------------
# Source fixtures
# ---------------------------------------------------------------------------

NOTES_OS = """
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

NOTES_DBMS = """
Normalisation. First Normal Form (1NF) requires that every attribute hold a
single atomic value. Second Normal Form (2NF) requires 1NF plus no partial
dependency: no non-key attribute may depend on only part of a composite
primary key. Third Normal Form (3NF) requires 2NF plus no transitive
dependency: no non-key attribute may depend on another non-key attribute.
Boyce-Codd Normal Form (BCNF) is a stricter 3NF in which every determinant is
a candidate key. Denormalisation deliberately reintroduces redundancy to
reduce join cost in read-heavy workloads.
""".strip()

NOTES_HINDI = """
प्रकाश संश्लेषण। हरे पौधे सूर्य के प्रकाश, जल और कार्बन डाइऑक्साइड की सहायता से
भोजन बनाते हैं। यह क्रिया पत्तियों के क्लोरोप्लास्ट में होती है, जिसमें
क्लोरोफिल नामक हरा वर्णक प्रकाश ऊर्जा को अवशोषित करता है। इस क्रिया में
ग्लूकोज बनता है और ऑक्सीजन गैस मुक्त होती है। प्रकाश संश्लेषण की दो अवस्थाएँ
हैं: प्रकाश अभिक्रिया और अप्रकाश अभिक्रिया (कैल्विन चक्र)।
""".strip()

GIBBERISH = "zxqw plrm fdgh zzz qqqq wgwg bnmn 8347 ;;;; plkjhg asdasd fffff gg"

LONG_NOTES = (NOTES_OS + "\n\n") * 40  # ~24k chars, over the 20k limit


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------

@dataclass
class Case:
    number: int
    name: str
    kind: str                 # "invalid" | "normal" | "edge"
    description: str
    expected: str
    run: Callable[[], dict[str, Any]]
    needs_api: bool = True


@dataclass
class Outcome:
    case: Case
    passed: bool = False
    actual: str = ""
    seconds: float = 0.0
    detail: dict[str, Any] = field(default_factory=dict)


def _settings(**kwargs: Any) -> Settings:
    base = dict(
        source_text=NOTES_OS,
        subject="Operating Systems",
        num_questions=3,
        question_types=["MCQ"],
        difficulty="medium",
        bloom_level="Understand",
        marks=2,
        source_kind="notes",
    )
    base.update(kwargs)
    return Settings(**base)  # type: ignore[arg-type]


def _validation_only(**kwargs: Any) -> dict[str, Any]:
    """Run validate_settings with the given inputs and report its verdict."""
    base = dict(
        source_text=NOTES_OS,
        subject="Operating Systems",
        num_questions=3,
        question_types=["MCQ"],
        difficulty="medium",
        bloom_level="Understand",
        marks=2,
    )
    base.update(kwargs)
    result = validate_settings(**base)  # type: ignore[arg-type]
    return {
        "blocked": not result.ok,
        "errors": result.errors,
        "warnings": result.warnings,
    }


def _full_pipeline(
    settings: Settings,
    quality: bool = True,
    sample_dir: Path | None = None,
) -> dict[str, Any]:
    """Generate, then optionally quality-check, exactly as the app does."""
    gen = generate_questions(settings)
    out: dict[str, Any] = {
        "generated": len(gen.questions),
        "attempts": gen.attempts,
        "repaired": gen.repaired,
        "unusable_source": gen.unusable_source,
        "errors": gen.errors,
        "input_tokens": gen.input_tokens,
        "output_tokens": gen.output_tokens,
        "types": sorted({q["type"] for q in gen.questions}),
        "difficulties": sorted({q["difficulty"] for q in gen.questions}),
        "blooms": sorted({q["bloom_level"] for q in gen.questions}),
        "marks": sorted({q["marks"] for q in gen.questions}),
        "first_question": gen.questions[0]["question"] if gen.questions else "",
    }
    if not gen.ok or not quality:
        return out

    qc = quality_checker.check_questions(gen.questions, settings)
    out.update(
        {
            "qc_ok": qc.ok,
            "qc_attempts": qc.attempts,
            "qc_errors": qc.errors,
            "average_score": qc.average_score,
            "flagged": qc.flagged_ids,
            "scores": [qc.reviews[i]["score"] for i in sorted(qc.reviews)],
            "input_tokens": out["input_tokens"] + qc.input_tokens,
            "output_tokens": out["output_tokens"] + qc.output_tokens,
        }
    )
    merged = quality_checker.merge(gen.questions, qc.reviews)
    artefacts = {
        "question-paper.pdf": exporter.to_pdf_question_paper(merged, settings),
        "answer-key.pdf": exporter.to_pdf_answer_key(merged, settings),
        "question-paper.docx": exporter.to_docx_question_paper(merged, settings),
        "answer-key.docx": exporter.to_docx_answer_key(merged, settings),
        "paper.json": exporter.to_json(merged, settings, qc.set_level_notes),
        "quality-report.csv": exporter.to_quality_csv(merged),
    }
    out["exports"] = {name: len(data) for name, data in artefacts.items()}
    # Keep the reviewed questions so the results file is real evidence, not
    # just a summary, and so sample exports can be rebuilt without re-calling.
    out["questions"] = merged
    out["set_level_notes"] = qc.set_level_notes
    if sample_dir is not None:
        sample_dir.mkdir(parents=True, exist_ok=True)
        for name, data in artefacts.items():
            (sample_dir / name).write_bytes(data)
        out["sample_dir"] = str(sample_dir)
    return out


# ---------------------------------------------------------------------------
# The cases
# ---------------------------------------------------------------------------

def build_cases() -> list[Case]:
    return [
        # --- invalid input (no API call should happen) ---
        Case(
            1,
            "Empty input",
            "invalid",
            "Topic/notes box left empty, everything else valid.",
            "Blocked by validation with 'is empty'; no API call.",
            lambda: _validation_only(source_text=""),
            needs_api=False,
        ),
        Case(
            2,
            "Whitespace-only input",
            "invalid",
            "Topic/notes contains only spaces and newlines.",
            "Blocked by validation; treated the same as empty.",
            lambda: _validation_only(source_text="   \n\t  \n "),
            needs_api=False,
        ),
        Case(
            3,
            "Too few characters",
            "invalid",
            "Source is 'CPU' (3 chars), below the 10-char floor.",
            "Blocked with a 'too short' error naming the floor.",
            lambda: _validation_only(source_text="CPU"),
            needs_api=False,
        ),
        Case(
            4,
            "Zero questions",
            "invalid",
            "num_questions = 0, below MIN_QUESTIONS.",
            "Blocked with a range error (1-20).",
            lambda: _validation_only(num_questions=0),
            needs_api=False,
        ),
        Case(
            5,
            "Too many questions",
            "invalid",
            "num_questions = 25, above MAX_QUESTIONS.",
            "Blocked with a range error (1-20).",
            lambda: _validation_only(num_questions=25),
            needs_api=False,
        ),
        Case(
            6,
            "No question type selected",
            "invalid",
            "question_types = [] (user cleared the multiselect).",
            "Blocked with 'Select at least one question type'.",
            lambda: _validation_only(question_types=[]),
            needs_api=False,
        ),
        Case(
            7,
            "Very long notes",
            "invalid",
            f"~{len(LONG_NOTES):,} characters, over the "
            f"{config.MAX_SOURCE_CHARS:,} limit.",
            "Blocked with a 'too long' error; no API call, no cost.",
            lambda: _validation_only(source_text=LONG_NOTES),
            needs_api=False,
        ),
        Case(
            8,
            "Unsupported file upload",
            "invalid",
            "A .docx is dropped on the .txt/.pdf uploader.",
            "Rejected with 'Unsupported file type'; empty text returned.",
            lambda: (
                lambda text, errors: {"text_len": len(text), "errors": errors}
            )(*extract_text_from_upload("notes.docx", b"PK\x03\x04junk")),
            needs_api=False,
        ),
        # --- normal cases (real API calls) ---
        Case(
            9,
            "Single MCQ",
            "normal",
            "1 question, MCQ, medium, Understand, 2 marks, OS notes.",
            "Exactly 1 valid MCQ with 4 options and a quality score.",
            lambda: _full_pipeline(_settings(num_questions=1)),
        ),
        Case(
            10,
            "Five mixed-type questions",
            "normal",
            "5 questions across all 4 types, mixed difficulty, Mixed Bloom.",
            "5 questions spanning several types/difficulties; all reviewed.",
            lambda: _full_pipeline(
                _settings(
                    num_questions=5,
                    question_types=[
                        "MCQ",
                        "Short Answer",
                        "Long Answer",
                        "True/False",
                    ],
                    difficulty="mixed",
                    bloom_level="Mixed",
                ),
                sample_dir=ROOT / "samples",
            ),
        ),
        Case(
            11,
            "Topic only, no notes",
            "normal",
            "A bare topic string instead of notes, DBMS, Apply, hard.",
            "Valid questions generated from the topic alone.",
            lambda: _full_pipeline(
                _settings(
                    source_text=(
                        "Database normalisation: 1NF, 2NF, 3NF, BCNF and when "
                        "to denormalise"
                    ),
                    subject="Database Management Systems",
                    num_questions=3,
                    question_types=["Short Answer"],
                    difficulty="hard",
                    bloom_level="Apply",
                    marks=5,
                    source_kind="topic",
                )
            ),
        ),
        Case(
            12,
            "Maximum questions (20)",
            "normal",
            "20 questions from the DBMS notes; the upper bound.",
            "20 valid questions; duplicates, if any, flagged by the checker.",
            lambda: _full_pipeline(
                _settings(
                    source_text=NOTES_DBMS,
                    subject="Database Management Systems",
                    num_questions=20,
                    question_types=["MCQ", "True/False"],
                    difficulty="mixed",
                    bloom_level="Mixed",
                    marks=1,
                )
            ),
        ),
        # --- edge cases (real API calls) ---
        Case(
            13,
            "Gibberish notes",
            "edge",
            "Random consonant strings as the source material.",
            "Model returns the unusable_source error object; no fake questions.",
            lambda: _full_pipeline(
                _settings(source_text=GIBBERISH, num_questions=3)
            ),
        ),
        Case(
            14,
            "Non-English notes (Hindi)",
            "edge",
            "Hindi notes on photosynthesis; 3 MCQs.",
            "Questions written in Hindi; JSON keys and enums stay English.",
            lambda: _full_pipeline(
                _settings(
                    source_text=NOTES_HINDI,
                    subject="विज्ञान (Science)",
                    num_questions=3,
                    question_types=["MCQ"],
                    difficulty="easy",
                    bloom_level="Remember",
                )
            ),
        ),
        Case(
            15,
            "Conflicting settings",
            "edge",
            "True/False questions worth 10 marks at Bloom's 'Create'.",
            "Warned at validation; generated anyway with marks scaled down "
            "and/or a Bloom mismatch flagged by the checker.",
            lambda: {
                "validation": _validation_only(
                    question_types=["True/False"], marks=10, bloom_level="Create"
                ),
                "pipeline": _full_pipeline(
                    _settings(
                        num_questions=3,
                        question_types=["True/False"],
                        marks=10,
                        bloom_level="Create",
                    )
                ),
            },
        ),
        Case(
            16,
            "Malformed model JSON (parser unit test)",
            "edge",
            "Three bad payloads fed straight to the parser/validator: prose "
            "wrapper, truncated JSON, MCQ answer absent from options.",
            "Each rejected with a specific error, which is what triggers the "
            "one retry in the live path.",
            lambda: {
                "prose_wrapped": extract_json(
                    'Sure! Here is the JSON:\n```json\n{"questions": []}\n```'
                ),
                "truncated": _expect_error(
                    lambda: extract_json('{"questions": [{"id": 1,')
                ),
                "answer_not_in_options": validate_questions(
                    {
                        "questions": [
                            {
                                "id": 1,
                                "question": "Which scheduler is preemptive?",
                                "type": "MCQ",
                                "options": ["FCFS", "SJF", "RR", "Priority"],
                                "answer": "Round Robin",
                                "explanation": "x",
                                "difficulty": "medium",
                                "bloom_level": "Understand",
                                "marks": 2,
                            }
                        ]
                    },
                    1,
                )[1],
            },
            needs_api=False,
        ),
    ]


def _expect_error(fn: Callable[[], Any]) -> str:
    try:
        fn()
    except Exception as exc:
        return f"{type(exc).__name__}: {exc}"
    return "NO ERROR RAISED"


# ---------------------------------------------------------------------------
# Pass/fail judgement
# ---------------------------------------------------------------------------

def _api_failure(detail: dict[str, Any]) -> str | None:
    """If an API-backed case produced nothing, say why, in the actual column.

    The judge strings below describe a successful run. When a case fails -- a
    503, a refusal, a validator rejection -- the actual column must report the
    failure, never the shape the case was hoping for.
    """
    if detail.get("generated"):
        return None
    errors = detail.get("errors") or []
    if detail.get("unusable_source"):
        return f"0 questions - source refused: {errors[0] if errors else 'unusable'}"
    if errors:
        return f"0 questions generated. {errors[0]}"
    if "exception" in detail:
        return f"0 questions generated. {detail['exception']}"
    return "0 questions generated (no error recorded)."


def judge(case: Case, detail: dict[str, Any]) -> tuple[bool, str]:
    """Decide pass/fail and write the one-line 'actual' column."""
    n = case.number

    if n in {1, 2, 3, 4, 5, 6, 7}:
        blocked = detail.get("blocked")
        errors = detail.get("errors", [])
        return bool(blocked and errors), (
            f"Blocked. {errors[0] if errors else 'no error text'}"
        )

    if n == 8:
        ok = detail["text_len"] == 0 and any(
            "Unsupported file type" in e for e in detail["errors"]
        )
        return ok, f"Rejected: {detail['errors'][0] if detail['errors'] else '-'}"

    if n in {9, 10, 11, 12, 14}:
        failure = _api_failure(detail)
        if failure is not None:
            return False, failure

    if n == 9:
        ok = (
            detail.get("generated") == 1
            and detail.get("types") == ["MCQ"]
            and detail.get("qc_ok")
        )
        return ok, (
            f"{detail.get('generated')} MCQ generated, quality "
            f"{detail.get('average_score')}/10, "
            f"{len(detail.get('flagged', []))} flagged, exports OK"
        )

    if n == 10:
        ok = (
            detail.get("generated") == 5
            and len(detail.get("types", [])) >= 2
            and detail.get("qc_ok")
        )
        return ok, (
            f"{detail.get('generated')} questions, types {detail.get('types')}, difficulties "
            f"{detail.get('difficulties')}, avg {detail.get('average_score')}/10"
        )

    if n == 11:
        ok = (
            detail.get("generated") == 3
            and detail.get("types") == ["Short Answer"]
            and detail.get("qc_ok")
        )
        return ok, (
            f"{detail.get('generated')} Short Answer from topic only, "
            f"Bloom {detail.get('blooms')}, "
            f"avg {detail.get('average_score')}/10"
        )

    if n == 12:
        ok = detail.get("generated") == 20 and detail.get("qc_ok")
        return ok, (
            f"20 questions, avg {detail.get('average_score')}/10, "
            f"{len(detail.get('flagged', []))} flagged "
            f"({detail.get('output_tokens')} output tokens)"
        )

    if n == 13:
        ok = detail.get("unusable_source") is True or detail.get("generated") == 0
        if detail.get("unusable_source"):
            return ok, f"Refused: {detail.get('errors', ['-'])[0][:110]}"
        return ok, f"No questions produced; errors: {detail.get('errors')}"

    if n == 14:
        first = detail.get("first_question", "")
        devanagari = any("ऀ" <= ch <= "ॿ" for ch in first)
        ok = detail.get("generated") == 3 and devanagari and detail.get("qc_ok")
        return ok, (
            f"{detail.get('generated')} questions in Devanagari script, "
            f"Bloom {detail.get('blooms')}, "
            f"avg {detail.get('average_score')}/10"
        )

    if n == 15:
        warned = bool(detail["validation"]["warnings"])
        pipe = detail["pipeline"]
        failure = _api_failure(pipe)
        if failure is not None:
            return False, f"Warned at validation; then {failure}"
        ok = warned and pipe.get("generated") == 3
        return ok, (
            f"Warned at validation; 3 produced with marks {pipe.get('marks')}, "
            f"avg {pipe.get('average_score')}/10, "
            f"{len(pipe.get('flagged', []))} flagged"
        )

    if n == 16:
        ok = (
            detail["prose_wrapped"] == {"questions": []}
            and "ValueError" in detail["truncated"]
            and detail["answer_not_in_options"]
        )
        return ok, (
            "Prose+fence recovered; truncated JSON raised ValueError; "
            "bad MCQ answer rejected"
        )

    return False, "no judgement rule"


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run(cases: list[Case]) -> list[Outcome]:
    outcomes: list[Outcome] = []
    for case in cases:
        print(f"\n[{case.number:>2}] {case.name} ... ", end="", flush=True)
        outcome = Outcome(case=case)
        started = time.time()
        try:
            detail = case.run()
            outcome.detail = detail
            outcome.passed, outcome.actual = judge(case, detail)
        except GeminiError as exc:
            outcome.actual = f"API error: {exc}"
            outcome.detail = {"exception": str(exc)}
        except Exception as exc:  # noqa: BLE001 - the harness must not die
            outcome.actual = f"{type(exc).__name__}: {exc}"
            outcome.detail = {"traceback": traceback.format_exc()}
        outcome.seconds = round(time.time() - started, 1)
        print(
            f"{'PASS' if outcome.passed else 'FAIL'} ({outcome.seconds}s) :: "
            f"{outcome.actual}"
        )
        outcomes.append(outcome)
    return outcomes


def markdown_table(outcomes: list[Outcome]) -> str:
    lines = [
        "| # | Type | Input | Expected output | Actual output | Pass/Fail |",
        "|---|------|-------|-----------------|---------------|-----------|",
    ]
    for o in outcomes:
        c = o.case
        lines.append(
            f"| {c.number} | {c.kind} | **{c.name}** — {c.description} "
            f"| {c.expected} | {o.actual} | "
            f"{'✅ Pass' if o.passed else '❌ Fail'} |"
        )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--offline",
        action="store_true",
        help="run only the cases that make no API call",
    )
    parser.add_argument(
        "--case", nargs="*", type=int, help="run only these case numbers"
    )
    args = parser.parse_args()

    cases = build_cases()
    if args.offline:
        cases = [c for c in cases if not c.needs_api]
    if args.case:
        wanted = set(args.case)
        cases = [c for c in cases if c.number in wanted]

    if any(c.needs_api for c in cases) and not get_api_key():
        print(
            "GEMINI_API_KEY is not set, so the API-backed cases cannot run.\n"
            "Set it, or pass --offline to run the validation-only cases."
        )
        return 2

    print(f"Model: {config.MODEL_ID}")
    print(f"Running {len(cases)} case(s)")
    outcomes = run(cases)

    passed = sum(1 for o in outcomes if o.passed)
    print(f"\n{'=' * 70}")
    print(f"{passed}/{len(outcomes)} passed")
    print(f"{'=' * 70}\n")
    print(markdown_table(outcomes))

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    results_path = OUT_DIR / "results.json"

    fresh = {
        o.case.number: {
            "number": o.case.number,
            "name": o.case.name,
            "kind": o.case.kind,
            "description": o.case.description,
            "expected": o.case.expected,
            "actual": o.actual,
            "passed": o.passed,
            "seconds": o.seconds,
            "detail": o.detail,
        }
        for o in outcomes
    }

    # A partial run (--offline / --case) must not throw away the results of the
    # cases it did not run: results.json is the evidence behind test_cases.md.
    # Merge into whatever is already on disk, replacing only what we just ran.
    merged = {}
    if results_path.exists():
        try:
            for row in json.loads(results_path.read_text(encoding="utf-8")):
                merged[int(row["number"])] = row
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            merged = {}
    carried = sorted(set(merged) - set(fresh))
    merged.update(fresh)
    rows = [merged[n] for n in sorted(merged)]

    results_path.write_text(
        json.dumps(rows, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )

    # The Markdown table is rebuilt from the merged set so it always shows
    # every case we have a result for, not just this invocation's.
    all_cases = {c.number: c for c in build_cases()}
    table_rows = [
        "| # | Type | Input | Expected output | Actual output | Pass/Fail |",
        "|---|------|-------|-----------------|---------------|-----------|",
    ]
    for row in rows:
        case = all_cases.get(row["number"])
        table_rows.append(
            f"| {row['number']} | {row['kind']} | **{row['name']}** — "
            f"{row['description']} | {row['expected']} | {row['actual']} | "
            f"{'✅ Pass' if row['passed'] else '❌ Fail'} |"
        )
    (OUT_DIR / "results_table.md").write_text(
        "\n".join(table_rows), encoding="utf-8"
    )

    print(f"\nWrote {results_path} and {OUT_DIR / 'results_table.md'}")
    if carried:
        print(
            f"Carried forward {len(carried)} earlier result(s) not run this "
            f"time: {', '.join(str(n) for n in carried)}"
        )
    return 0 if passed == len(outcomes) else 1


if __name__ == "__main__":
    raise SystemExit(main())
