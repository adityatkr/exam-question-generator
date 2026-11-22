"""Module 1 (part 2): input validation and model-output validation.

Two jobs:

* `validate_settings` guards the Claude call. It runs before any money is
  spent, and returns human-readable errors for the UI.
* `validate_questions` / `validate_reviews` guard everything downstream of the
  Claude call: they accept only well-formed JSON of the exact shape the
  prompts asked for, and are what makes the "retry once on malformed output"
  rule possible.
"""

from __future__ import annotations

import io
import json
import re
from dataclasses import dataclass, field
from typing import Any

import config


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

@dataclass
class Settings:
    """A validated request for a question paper."""

    source_text: str
    subject: str
    num_questions: int
    question_types: list[str]
    difficulty: str
    bloom_level: str
    marks: int
    source_kind: str = "topic"          # "topic" | "notes" | "uploaded file"

    def as_prompt_values(self) -> dict[str, Any]:
        """The values the generator / quality-check templates expect."""
        return {
            "subject": self.subject,
            "source_kind": self.source_kind,
            "source_text": self.source_text,
            "num_questions": self.num_questions,
            "question_types": ", ".join(self.question_types),
            "difficulty": self.difficulty,
            "bloom_level": self.bloom_level,
            "marks": self.marks,
        }


@dataclass
class ValidationResult:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def validate_settings(
    source_text: str,
    subject: str,
    num_questions: Any,
    question_types: list[str] | None,
    difficulty: str,
    bloom_level: str,
    marks: Any,
) -> ValidationResult:
    """Validate a paper request. Returns errors (blocking) and warnings."""
    result = ValidationResult()

    # --- source material ---
    text = (source_text or "").strip()
    if not text:
        result.errors.append(
            "Topic or notes is empty. Enter a topic, paste lecture notes, or "
            "upload a .txt/.pdf file."
        )
    elif len(text) < config.MIN_SOURCE_CHARS:
        result.errors.append(
            f"Topic or notes is too short ({len(text)} characters). "
            f"Give at least {config.MIN_SOURCE_CHARS} characters."
        )
    elif len(text) > config.MAX_SOURCE_CHARS:
        result.errors.append(
            f"Notes are too long ({len(text):,} characters). The limit is "
            f"{config.MAX_SOURCE_CHARS:,}. Trim the notes or split them into "
            "two papers."
        )

    # --- subject ---
    subj = (subject or "").strip()
    if not subj:
        result.errors.append("Subject is empty. Enter the subject or course name.")
    elif len(subj) > config.MAX_SUBJECT_CHARS:
        result.errors.append(
            f"Subject is too long (limit {config.MAX_SUBJECT_CHARS} characters)."
        )

    # --- number of questions ---
    try:
        n = int(num_questions)
    except (TypeError, ValueError):
        result.errors.append("Number of questions must be a whole number.")
        n = None
    else:
        if not config.MIN_QUESTIONS <= n <= config.MAX_QUESTIONS:
            result.errors.append(
                f"Number of questions must be between {config.MIN_QUESTIONS} "
                f"and {config.MAX_QUESTIONS} (got {n})."
            )

    # --- question types ---
    types = question_types or []
    if not types:
        result.errors.append("Select at least one question type.")
    else:
        unknown = [t for t in types if t not in config.QUESTION_TYPES]
        if unknown:
            result.errors.append(
                f"Unknown question type(s): {', '.join(unknown)}. "
                f"Allowed: {', '.join(config.QUESTION_TYPES)}."
            )

    # --- difficulty / Bloom ---
    if difficulty not in config.DIFFICULTIES:
        result.errors.append(
            f"Difficulty must be one of: {', '.join(config.DIFFICULTIES)}."
        )
    if bloom_level not in config.BLOOM_LEVELS:
        result.errors.append(
            f"Bloom's level must be one of: {', '.join(config.BLOOM_LEVELS)}."
        )

    # --- marks ---
    try:
        m = int(marks)
    except (TypeError, ValueError):
        result.errors.append("Marks per question must be a whole number.")
        m = None
    else:
        if not config.MIN_MARKS <= m <= config.MAX_MARKS:
            result.errors.append(
                f"Marks per question must be between {config.MIN_MARKS} and "
                f"{config.MAX_MARKS} (got {m})."
            )

    # --- conflicting / implausible combinations (warn, do not block) ---
    if m is not None and types:
        only_short = set(types) <= {"True/False"}
        if only_short and m > 2:
            result.warnings.append(
                f"{m} marks for a True/False question is unusual; the generator "
                "will scale the marks down to a defensible value."
            )
        if set(types) == {"Long Answer"} and m <= 2:
            result.warnings.append(
                f"{m} mark(s) for a Long Answer question is unusual; expect "
                "short answers or a marks adjustment."
            )
    if n is not None and n > 10 and len(text) < 300:
        result.warnings.append(
            f"{n} questions from {len(text)} characters of source is a lot. "
            "Expect broad questions or near-duplicates."
        )
    if bloom_level in {"Create", "Evaluate"} and set(types) <= {
        "True/False",
        "MCQ",
    } and types:
        result.warnings.append(
            f"Bloom's level '{bloom_level}' is hard to assess with "
            f"{', '.join(types)} only; the quality checker will likely flag a "
            "Bloom mismatch."
        )

    return result


# ---------------------------------------------------------------------------
# JSON extraction
# ---------------------------------------------------------------------------

_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)


def extract_json(raw: str) -> dict[str, Any]:
    """Pull the first JSON object out of a model reply.

    The prompts forbid code fences and prose, but a model can still slip.
    This strips fences and falls back to the outermost brace pair, so one
    stray sentence does not cost a retry.
    """
    if not raw or not raw.strip():
        raise ValueError("Model returned an empty response.")

    text = _FENCE.sub("", raw.strip())
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise ValueError("No JSON object found in the model response.")
        try:
            parsed = json.loads(text[start : end + 1])
        except json.JSONDecodeError as exc:
            raise ValueError(f"Malformed JSON in model response: {exc}") from exc

    if not isinstance(parsed, dict):
        raise ValueError(
            f"Expected a JSON object, got {type(parsed).__name__}."
        )
    return parsed


# ---------------------------------------------------------------------------
# Question validation
# ---------------------------------------------------------------------------

_REQUIRED_KEYS = (
    "question",
    "type",
    "answer",
    "explanation",
    "difficulty",
    "bloom_level",
    "marks",
)

_TYPE_ALIASES = {
    "mcq": "MCQ",
    "multiple choice": "MCQ",
    "multiple-choice": "MCQ",
    "short answer": "Short Answer",
    "short-answer": "Short Answer",
    "short": "Short Answer",
    "long answer": "Long Answer",
    "long-answer": "Long Answer",
    "long": "Long Answer",
    "true/false": "True/False",
    "true or false": "True/False",
    "truefalse": "True/False",
    "true-false": "True/False",
}


def _normalise_type(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    return _TYPE_ALIASES.get(value.strip().lower())


def validate_questions(
    payload: dict[str, Any],
    expected_count: int | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Validate a generator payload.

    Returns `(questions, errors)`. A non-empty `errors` list means the payload
    is unusable and the caller should retry the API call.
    """
    errors: list[str] = []

    if "error" in payload:
        reason = payload.get("reason") or "the model reported the source as unusable"
        return [], [f"unusable_source: {reason}"]

    raw_questions = payload.get("questions")
    if not isinstance(raw_questions, list):
        return [], ["Payload has no 'questions' list."]
    if not raw_questions:
        return [], ["Payload contains zero questions."]

    questions: list[dict[str, Any]] = []
    for index, item in enumerate(raw_questions, start=1):
        label = f"Question {index}"
        if not isinstance(item, dict):
            errors.append(f"{label} is not a JSON object.")
            continue

        missing = [k for k in _REQUIRED_KEYS if k not in item]
        if missing:
            errors.append(f"{label} is missing field(s): {', '.join(missing)}.")
            continue

        qtype = _normalise_type(item["type"])
        if qtype is None:
            errors.append(f"{label} has an unrecognised type: {item['type']!r}.")
            continue

        question_text = str(item["question"]).strip()
        if not question_text:
            errors.append(f"{label} has empty question text.")
            continue

        options = item.get("options") or []
        if not isinstance(options, list):
            errors.append(f"{label} has a non-list 'options' field.")
            continue
        options = [str(o).strip() for o in options if str(o).strip()]

        answer = str(item["answer"]).strip()
        if not answer:
            errors.append(f"{label} has an empty answer.")
            continue

        if qtype == "MCQ":
            if len(options) != 4:
                errors.append(
                    f"{label} is an MCQ with {len(options)} option(s); needs exactly 4."
                )
                continue
            if answer not in options:
                errors.append(
                    f"{label} is an MCQ whose answer is not one of its options."
                )
                continue
        elif qtype == "True/False":
            if not options:
                options = ["True", "False"]
            if answer.lower() not in {"true", "false"}:
                errors.append(
                    f"{label} is True/False but its answer is {answer!r}."
                )
                continue
        else:
            options = []

        difficulty = str(item["difficulty"]).strip().lower()
        if difficulty not in {"easy", "medium", "hard"}:
            errors.append(
                f"{label} has an invalid difficulty: {item['difficulty']!r}."
            )
            continue

        bloom = str(item["bloom_level"]).strip().title()
        if bloom not in config.BLOOM_LEVELS:
            errors.append(f"{label} has an invalid Bloom level: {bloom!r}.")
            continue

        try:
            marks = int(float(item["marks"]))
        except (TypeError, ValueError):
            errors.append(f"{label} has non-numeric marks: {item['marks']!r}.")
            continue

        try:
            qid = int(item.get("id", index))
        except (TypeError, ValueError):
            qid = index

        questions.append(
            {
                "id": qid,
                "question": question_text,
                "type": qtype,
                "options": options,
                "answer": answer,
                "explanation": str(item.get("explanation", "")).strip(),
                "difficulty": difficulty,
                "bloom_level": bloom,
                "marks": marks,
            }
        )

    if errors:
        return questions, errors

    if expected_count is not None and len(questions) != expected_count:
        errors.append(
            f"Asked for {expected_count} question(s), got {len(questions)}."
        )

    return questions, errors


def validate_reviews(
    payload: dict[str, Any],
    expected_ids: list[int],
) -> tuple[dict[int, dict[str, Any]], str, list[str]]:
    """Validate a quality-check payload.

    Returns `(reviews_by_id, set_level_notes, errors)`.
    """
    errors: list[str] = []
    raw_reviews = payload.get("reviews")
    if not isinstance(raw_reviews, list) or not raw_reviews:
        return {}, "", ["Payload has no 'reviews' list."]

    reviews: dict[int, dict[str, Any]] = {}
    for index, item in enumerate(raw_reviews):
        if not isinstance(item, dict):
            errors.append(f"Review {index + 1} is not a JSON object.")
            continue

        try:
            rid = int(item["id"])
        except (KeyError, TypeError, ValueError):
            rid = expected_ids[index] if index < len(expected_ids) else index + 1

        try:
            score = float(item["score"])
        except (KeyError, TypeError, ValueError):
            errors.append(f"Review for question {rid} has a non-numeric score.")
            continue
        score = max(0.0, min(10.0, score))

        sub = item.get("scores")
        sub_scores: dict[str, int] = {}
        if isinstance(sub, dict):
            for key, value in sub.items():
                try:
                    sub_scores[str(key)] = int(float(value))
                except (TypeError, ValueError):
                    continue

        issues = item.get("issues") or []
        if isinstance(issues, str):
            issues = [issues]
        issues = [str(i).strip() for i in issues if str(i).strip()]

        dup = item.get("duplicate_of")
        try:
            dup = int(dup) if dup not in (None, "", "null") else None
        except (TypeError, ValueError):
            dup = None

        reviews[rid] = {
            "id": rid,
            "score": round(score, 1),
            "scores": sub_scores,
            "issues": issues,
            "suggested_fix": str(item.get("suggested_fix", "")).strip(),
            "duplicate_of": dup,
            "flagged": score < config.QUALITY_THRESHOLD or dup is not None,
        }

    missing = [i for i in expected_ids if i not in reviews]
    if missing:
        errors.append(
            "No review returned for question id(s): "
            + ", ".join(str(i) for i in missing)
        )

    notes = str(payload.get("set_level_notes", "")).strip()
    return reviews, notes, errors


# ---------------------------------------------------------------------------
# File uploads
# ---------------------------------------------------------------------------

def extract_text_from_upload(filename: str, data: bytes) -> tuple[str, list[str]]:
    """Extract plain text from an uploaded .txt or .pdf. Returns (text, errors)."""
    name = (filename or "").lower()

    if name.endswith(".txt"):
        for encoding in ("utf-8", "utf-16", "latin-1"):
            try:
                return data.decode(encoding).strip(), []
            except UnicodeDecodeError:
                continue
        return "", [f"Could not decode {filename} as text."]

    if name.endswith(".pdf"):
        try:
            from pypdf import PdfReader
        except ImportError:
            return "", ["pypdf is not installed; cannot read PDF uploads."]
        try:
            reader = PdfReader(io.BytesIO(data))
            pages = [(page.extract_text() or "") for page in reader.pages]
        except Exception as exc:  # pypdf raises a wide variety of errors
            return "", [f"Could not read {filename} as a PDF: {exc}"]
        text = "\n\n".join(p.strip() for p in pages if p.strip()).strip()
        if not text:
            return "", [
                f"{filename} has no extractable text. It is probably a scanned "
                "PDF; this app does not do OCR. Paste the notes instead."
            ]
        return text, []

    return "", [f"Unsupported file type: {filename}. Upload a .txt or .pdf."]
