<!--
Prompt 5 of 5 - regenerate_user.md
Role: user-message template for the REGENERATE-FLAGGED-QUESTIONS call.
      Reuses generator_system.md as its system prompt, so the setter persona
      and the output contract are identical to a first-pass generation.
Placeholders {like_this} are filled by core/generator.py via str.format().
Literal JSON braces are doubled ({{ }}).
-->

The moderation board rejected {num_questions} question(s) from a paper you
drafted. Replace each rejected question with a new one that fixes the stated
defect.

<source_material subject="{subject}" kind="{source_kind}">
{source_text}
</source_material>

## Paper specification (unchanged)

- Subject: {subject}
- Question types allowed: {question_types}
- Difficulty: {difficulty}
- Bloom's taxonomy level: {bloom_level}
- Marks per question: {marks}

## Rejected questions and the board's findings

{rejected_json}

## How to replace them

- Return exactly {num_questions} question objects - one replacement per
  rejected question, in the same order.
- Keep each replacement's `id` identical to the `id` of the question it
  replaces, so it drops back into the paper in the same slot.
- Fix the defect named in `issues` and carry out `suggested_fix`. That is the
  point of this pass: a replacement that repeats the same defect is a failure.
- Where `duplicate_of` is set, test a **different** point from the source
  material - do not reword the duplicate.
- Stay on the same part of the source material and the same specification, so
  the paper keeps its coverage. Do not drift to an easier or unrelated point to
  make the fix simple.
- Apply every rule from your standing instructions: one defensible answer,
  verbatim MCQ answer text, self-contained wording, honest difficulty and
  Bloom labels.

## Required JSON shape

Identical to a first-pass generation:

{{
  "questions": [
    {{
      "id": 1,
      "question": "string",
      "type": "MCQ | Short Answer | Long Answer | True/False",
      "options": ["string", "string", "string", "string"],
      "answer": "string",
      "explanation": "string",
      "difficulty": "easy | medium | hard",
      "bloom_level": "Remember | Understand | Apply | Analyze | Evaluate | Create",
      "marks": 0
    }}
  ]
}}

Return the JSON object now.
