"""Loads prompt text from /prompts and renders the user templates.

This is the only module that touches the prompt files. Keeping it separate
means the prompts stay editable as plain Markdown (which is what the course
deliverable asks for) while the app still fails loudly if a template
placeholder is ever left unfilled.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from string import Formatter

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"

# Each prompt file opens with an HTML comment documenting its number and role.
# That header is for the human reader and the grader, not for Claude.
_HEADER_COMMENT = re.compile(r"^\s*<!--.*?-->\s*", re.DOTALL)


class PromptError(RuntimeError):
    """Raised when a prompt file is missing or a placeholder is unfilled."""


@lru_cache(maxsize=None)
def load(name: str) -> str:
    """Return the prompt text of /prompts/<name>.md, header comment stripped."""
    path = PROMPTS_DIR / f"{name}.md"
    if not path.exists():
        raise PromptError(f"Prompt file not found: {path}")
    text = path.read_text(encoding="utf-8")
    return _HEADER_COMMENT.sub("", text).strip()


def _assert_template(name: str) -> None:
    """System prompts are sent verbatim and are not format templates.

    They legitimately contain literal braces (the output contract tells Claude
    its reply must start with `{`), which str.format() would misread as
    placeholders. Only the user templates may be rendered.
    """
    if name.endswith("_system"):
        raise PromptError(
            f"'{name}' is a system prompt: load() it verbatim, do not render() it."
        )


def placeholders(name: str) -> set[str]:
    """Return the set of {placeholder} names a user template declares."""
    _assert_template(name)
    return {
        field
        for _, field, _, _ in Formatter().parse(load(name))
        if field
    }


def render(name: str, **values: object) -> str:
    """Render a user-prompt template, erroring on any unfilled placeholder."""
    _assert_template(name)
    required = placeholders(name)
    missing = required - set(values)
    if missing:
        raise PromptError(
            f"Prompt '{name}' is missing values for: {', '.join(sorted(missing))}"
        )
    try:
        return load(name).format(**values)
    except (KeyError, IndexError) as exc:  # pragma: no cover - defensive
        raise PromptError(f"Prompt '{name}' failed to render: {exc}") from exc


def inventory() -> list[str]:
    """Names of the prompt files that make up the solution, in order."""
    return [
        "generator_system",
        "generator_user",
        "quality_check_system",
        "quality_check_user",
        "regenerate_user",
    ]
