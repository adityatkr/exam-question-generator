"""Module 2: question generation (and regeneration of flagged questions).

Flow for one generation:

    settings -> render generator_user.md -> Claude (generator_system.md)
             -> extract JSON -> validate -> questions

If the JSON is malformed or fails validation, the call is retried **once**
with a repair note appended to the prompt, as the spec requires.
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
    generation_max_tokens,
)
from core.validators import Settings, extract_json, validate_questions

GENERATOR_SYSTEM = "generator_system"
GENERATOR_USER = "generator_user"
REGENERATE_USER = "regenerate_user"


@dataclass
class GenerationResult:
    questions: list[dict[str, Any]] = field(default_factory=list)
    attempts: int = 0
    errors: list[str] = field(default_factory=list)
    responses: list[ClaudeResponse] = field(default_factory=list)
    repaired: bool = False          # True when attempt 1 failed and 2 worked
    unusable_source: bool = False   # model reported the source as unusable

    @property
    def ok(self) -> bool:
        return bool(self.questions) and not self.errors

    @property
    def input_tokens(self) -> int:
        return sum(r.input_tokens for r in self.responses)

    @property
    def output_tokens(self) -> int:
        return sum(r.output_tokens for r in self.responses)


_REPAIR_NOTE = (
    "\n\n---\n\n"
    "## Correction required\n\n"
    "Your previous reply could not be parsed. The validator reported:\n\n"
    "{problems}\n\n"
    "Send the corrected JSON object only. Start your reply with `{{` and end "
    "it with `}}`. No prose, no markdown code fences, no comments. Fix every "
    "problem listed above."
)


def _attempt(
    system: str,
    user: str,
    max_tokens: int,
    expected_count: int | None,
    client: anthropic.Anthropic | None,
) -> tuple[list[dict[str, Any]], list[str], ClaudeResponse, bool]:
    """One API call plus parse + validate. Returns (questions, errors, resp, unusable)."""
    response = call_claude(
        system=system,
        user=user,
        max_tokens=max_tokens,
        effort=config.GENERATOR_EFFORT,
        client=client,
    )

    if response.stop_reason == "max_tokens":
        return (
            [],
            [
                "The reply hit the output-token limit and was cut off. Ask for "
                "fewer questions, or raise MAX_TOKENS_PER_QUESTION in config.py."
            ],
            response,
            False,
        )

    try:
        payload = extract_json(response.text)
    except ValueError as exc:
        return [], [str(exc)], response, False

    questions, errors = validate_questions(payload, expected_count)
    unusable = any(e.startswith("unusable_source") for e in errors)
    return questions, errors, response, unusable


def generate_questions(
    settings: Settings,
    client: anthropic.Anthropic | None = None,
) -> GenerationResult:
    """Generate a fresh question paper. Retries once on malformed output."""
    result = GenerationResult()
    system = prompt_loader.load(GENERATOR_SYSTEM)
    user = prompt_loader.render(GENERATOR_USER, **settings.as_prompt_values())
    max_tokens = generation_max_tokens(settings.num_questions)

    for attempt in range(1, config.MAX_JSON_ATTEMPTS + 1):
        result.attempts = attempt
        try:
            questions, errors, response, unusable = _attempt(
                system, user, max_tokens, settings.num_questions, client
            )
        except ClaudeError as exc:
            result.errors = [str(exc)]
            return result

        result.responses.append(response)

        if unusable:
            result.unusable_source = True
            result.errors = errors
            return result

        if not errors:
            result.questions = questions
            result.errors = []
            result.repaired = attempt > 1
            return result

        result.errors = errors
        if attempt < config.MAX_JSON_ATTEMPTS:
            problems = "\n".join(f"- {e}" for e in errors)
            user = (
                prompt_loader.render(
                    GENERATOR_USER, **settings.as_prompt_values()
                )
                + _REPAIR_NOTE.format(problems=problems)
            )

    return result


def regenerate_flagged(
    settings: Settings,
    flagged: list[dict[str, Any]],
    client: anthropic.Anthropic | None = None,
) -> GenerationResult:
    """Replace flagged questions, keeping their ids so they drop into the same slots.

    `flagged` items are merged question+review dicts, i.e. each carries the
    original question plus `issues`, `suggested_fix` and `duplicate_of` from
    the quality check.
    """
    result = GenerationResult()
    if not flagged:
        result.errors = ["No flagged questions to regenerate."]
        return result

    rejected = [
        {
            "id": q["id"],
            "question": q["question"],
            "type": q["type"],
            "options": q.get("options", []),
            "answer": q["answer"],
            "difficulty": q["difficulty"],
            "bloom_level": q["bloom_level"],
            "score": q.get("score"),
            "issues": q.get("issues", []),
            "suggested_fix": q.get("suggested_fix", ""),
            "duplicate_of": q.get("duplicate_of"),
        }
        for q in flagged
    ]

    values = settings.as_prompt_values()
    values["num_questions"] = len(rejected)
    values["rejected_json"] = json.dumps(rejected, indent=2, ensure_ascii=False)

    system = prompt_loader.load(GENERATOR_SYSTEM)
    user = prompt_loader.render(REGENERATE_USER, **values)
    max_tokens = generation_max_tokens(len(rejected))

    for attempt in range(1, config.MAX_JSON_ATTEMPTS + 1):
        result.attempts = attempt
        try:
            questions, errors, response, unusable = _attempt(
                system, user, max_tokens, len(rejected), client
            )
        except ClaudeError as exc:
            result.errors = [str(exc)]
            return result

        result.responses.append(response)

        if unusable:
            result.unusable_source = True
            result.errors = errors
            return result

        if not errors:
            # Force the original ids back on, in order, so the replacements
            # land in the same slots even if the model renumbered them.
            for new_q, old in zip(questions, rejected):
                new_q["id"] = old["id"]
            result.questions = questions
            result.errors = []
            result.repaired = attempt > 1
            return result

        result.errors = errors
        if attempt < config.MAX_JSON_ATTEMPTS:
            problems = "\n".join(f"- {e}" for e in errors)
            user = prompt_loader.render(REGENERATE_USER, **values) + (
                _REPAIR_NOTE.format(problems=problems)
            )

    return result
