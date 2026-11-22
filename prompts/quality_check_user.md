<!--
Prompt 4 of 5 - quality_check_user.md
Role: user-message template for the QUALITY CHECK call
Placeholders {like_this} are filled by core/quality_checker.py via str.format().
Literal JSON braces are doubled ({{ }}).
-->

Moderate the question set below against the paper specification and the source
material it was drafted from.

## Paper specification the teacher was given

- Subject: {subject}
- Requested difficulty: {difficulty}
- Requested Bloom's taxonomy level: {bloom_level}
- Requested marks per question: {marks}
- Requested question types: {question_types}

## Source material the questions must be answerable from

<source_material>
{source_text}
</source_material>

## Question set under review

{questions_json}

## What to return

One review object per question, in the same order, with the same `id`.

{{
  "reviews": [
    {{
      "id": 1,
      "score": 0.0,
      "scores": {{
        "clarity": 0,
        "answer_correctness": 0,
        "single_answer": 0,
        "difficulty_match": 0,
        "bloom_match": 0,
        "source_relevance": 0
      }},
      "issues": ["short, specific defect - name the criterion it breaks"],
      "suggested_fix": "one concrete rewrite instruction, or empty string if none needed",
      "duplicate_of": null
    }}
  ],
  "set_level_notes": "one or two sentences on the set as a whole: coverage, type and difficulty balance, anything a moderator should know"
}}

Rules for the fields:

- `scores` holds the six per-criterion scores, each an integer 0-10.
- `score` is the **minimum** of the six per-criterion scores, as a number.
- `issues` is `[]` when the question is clean. Each issue is one short sentence
  that names the defect, not a general comment.
- `suggested_fix` is an instruction a teacher can act on ("replace option C,
  which is also correct, with ..."), not a restatement of the issue.
- `duplicate_of` is the `id` of the earlier question it duplicates, or `null`.
- Review every question. Return exactly {num_questions} review objects.

Return the JSON object now.
