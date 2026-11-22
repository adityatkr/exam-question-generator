# Prompt inventory

**The final solution uses exactly 5 prompts**, stored as 5 files in this folder.

| # | File | Kind | Used by | Sent on |
|---|------|------|---------|---------|
| 1 | `generator_system.md` | System | `core/generator.py` | Generate **and** Regenerate |
| 2 | `generator_user.md` | User template | `core/generator.py` | Generate |
| 3 | `quality_check_system.md` | System | `core/quality_checker.py` | Quality check |
| 4 | `quality_check_user.md` | User template | `core/quality_checker.py` | Quality check |
| 5 | `regenerate_user.md` | User template | `core/generator.py` | Regenerate flagged |

Those 5 files compose **3 distinct Gemini calls**, each a system instruction + user prompt pair:

| Call | System prompt | User prompt |
|------|---------------|-------------|
| Generate questions | `generator_system.md` | `generator_user.md` |
| Quality check | `quality_check_system.md` | `quality_check_user.md` |
| Regenerate flagged | `generator_system.md` *(reused)* | `regenerate_user.md` |

Prompt 1 is deliberately shared between the Generate and Regenerate calls: the
examiner persona, the grounding rules and the JSON output contract must not
drift between a first draft and a repair, or a regenerated question would be
judged by the quality checker against different standards than it was written
to.

## Conventions

- Every file opens with an HTML comment naming its number, role and pairing.
  The comment is stripped before the prompt is sent (`core/prompt_loader.py`).
- User templates use `str.format()` placeholders (`{subject}`, `{source_text}`,
  ...). Literal JSON braces inside them are doubled (`{{`, `}}`).
- `core/prompt_loader.py` is the only module that reads these files, and it
  validates that every placeholder a template declares was supplied.

## Iteration history

`iteration_log.md` records 4 versions each of the generator prompt and the
quality-check prompt (8 versions logged in total), with the full prompt text,
the deliberate change, the reason, and the observed effect of a real API run.
