"""Module 3: the quality check.

A SECOND, independent Claude call with its own system prompt reviews the
generated questions. It is given the paper specification and the source
material but is told nothing about which model wrote the questions, so it
judges the output rather than defending it.

Scores each question out of 10 across six criteria, reports issues and a
suggested fix, flags duplicates, and marks anything below
`config.QUALITY_THRESHOLD` for regeneration.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import anthropic

import config
from core import prompt_loader
from core.claude_client import (
    ClaudeError,
    ClaudeResponse,
    call_claude,
    quality_max_tokens,
)
from core.validators import Settings, extract_json, validate_reviews

QUALITY_SYSTEM = "quality_check_system"
QUALITY_USER = "quality_check_user"

# Field order shown to the reviewer. The answer key is included on purpose:
# criterion 2 is "is the answer key correct", which cannot be judged without it.
_REVIEW_FIELDS = (
    "id",
    "question",
    "type",
    "options",
    "answer",
    "explanation",
    "difficulty",
    "bloom_level",
    "marks",
)


@dataclass
class QualityResult:
    reviews: dict[int, dict[str, Any]] = field(default_factory=dict)
    set_level_notes: str = ""
    attempts: int = 0
    errors: list[str] = field(default_factory=list)
    responses: list[ClaudeResponse] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.reviews) and not self.errors

    @property
    def input_tokens(self) -> int:
        return sum(r.input_tokens for r in self.responses)

    @property
    def output_tokens(self) -> int:
        return sum(r.output_tokens for r in self.responses)

    @property
    def average_score(self) -> float:
        if not self.reviews:
            return 0.0
        return round(
            sum(r["score"] for r in self.reviews.values()) / len(self.reviews), 2
        )

    @property
    def flagged_ids(self) -> list[int]:
        return sorted(i for i, r in self.reviews.items() if r["flagged"])


_REPAIR_NOTE = (
    "\n\n---\n\n"
    "## Correction required\n\n"
    "Your previous reply could not be parsed. The validator reported:\n\n"
    "{problems}\n\n"
    "Send the corrected JSON object only. Start your reply with `{{` and end "
    "it with `}}`. No prose, no markdown code fences. Return one review object "
    "per question, each with an `id`, a numeric `score`, the six `scores`, "
    "`issues`, `suggested_fix` and `duplicate_of`."
)


def check_questions(
    questions: list[dict[str, Any]],
    settings: Settings,
    client: anthropic.Anthropic | None = None,
) -> QualityResult:
    """Run the quality-check pass. Retries once on malformed output."""
    result = QualityResult()
    if not questions:
        result.errors = ["No questions to review."]
        return result

    trimmed = [{k: q.get(k) for k in _REVIEW_FIELDS} for q in questions]
    expected_ids = [int(q["id"]) for q in questions]

    values = settings.as_prompt_values()
    values["num_questions"] = len(questions)
    values["questions_json"] = json.dumps(trimmed, indent=2, ensure_ascii=False)

    system = prompt_loader.load(QUALITY_SYSTEM)
    base_user = prompt_loader.render(QUALITY_USER, **values)
    user = base_user
    max_tokens = quality_max_tokens(len(questions))

    for attempt in range(1, config.MAX_JSON_ATTEMPTS + 1):
        result.attempts = attempt
        try:
            response = call_claude(
                system=system,
                user=user,
                max_tokens=max_tokens,
                effort=config.QUALITY_EFFORT,
                client=client,
            )
        except ClaudeError as exc:
            result.errors = [str(exc)]
            return result

        result.responses.append(response)

        if response.stop_reason == "max_tokens":
            errors = [
                "The review hit the output-token limit and was cut off. Review "
                "fewer questions at a time."
            ]
        else:
            try:
                payload = extract_json(response.text)
            except ValueError as exc:
                errors = [str(exc)]
            else:
                reviews, notes, errors = validate_reviews(payload, expected_ids)
                if not errors:
                    result.reviews = reviews
                    result.set_level_notes = notes
                    result.errors = []
                    return result

        result.errors = errors
        if attempt < config.MAX_JSON_ATTEMPTS:
            problems = "\n".join(f"- {e}" for e in errors)
            user = base_user + _REPAIR_NOTE.format(problems=problems)

    return result


def merge(
    questions: list[dict[str, Any]],
    reviews: dict[int, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Attach each review to its question. Unreviewed questions get score None."""
    merged: list[dict[str, Any]] = []
    for question in questions:
        row = dict(question)
        review = reviews.get(int(question["id"]))
        if review:
            row.update(
                {
                    "score": review["score"],
                    "scores": review["scores"],
                    "issues": review["issues"],
                    "suggested_fix": review["suggested_fix"],
                    "duplicate_of": review["duplicate_of"],
                    "flagged": review["flagged"],
                }
            )
        else:
            row.update(
                {
                    "score": None,
                    "scores": {},
                    "issues": [],
                    "suggested_fix": "",
                    "duplicate_of": None,
                    "flagged": False,
                }
            )
        merged.append(row)
    return merged
