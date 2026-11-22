<!--
Prompt 2 of 5 - generator_user.md
Role: user-message template for the QUESTION GENERATION call
Placeholders {like_this} are filled by core/generator.py via str.format().
Literal JSON braces are doubled ({{ }}) so str.format() leaves them alone.
-->

Generate exam questions from the material below.

<source_material subject="{subject}" kind="{source_kind}">
{source_text}
</source_material>

## Paper specification

- Subject: {subject}
- Number of questions: exactly {num_questions}
- Question types allowed: {question_types}
- Difficulty: {difficulty}
- Bloom's taxonomy level: {bloom_level}
- Marks per question: {marks}

### How to apply the specification

- Produce **exactly {num_questions}** question objects. Not more, not fewer.
- Use only the allowed types. If several types are allowed, distribute them as
  evenly as the count permits (e.g. 5 questions and 2 allowed types -> 3 + 2).
- If difficulty is `mixed`, spread questions across easy / medium / hard and
  report the true per-question difficulty in each object.
- If Bloom level is `Mixed`, choose a suitable level per question and report it.
- Set `marks` to {marks} on every question unless the type makes that
  indefensible (for example a True/False worth 10 marks) - in that case use the
  nearest defensible value and say so in `explanation`.
- `options` is a list of exactly 4 strings for MCQ, exactly
  `["True", "False"]` for True/False, and `[]` for Short Answer and Long Answer.
- `explanation` is the model answer or marking note for the evaluator: why the
  answer is correct, and for MCQ why the distractors are wrong. 1-3 sentences.

## Required JSON shape

{{
  "questions": [
    {{
      "id": 1,
      "question": "string - the question as the student will read it",
      "type": "MCQ | Short Answer | Long Answer | True/False",
      "options": ["string", "string", "string", "string"],
      "answer": "string - for MCQ, verbatim text of the correct option",
      "explanation": "string - model answer / marking note",
      "difficulty": "easy | medium | hard",
      "bloom_level": "Remember | Understand | Apply | Analyze | Evaluate | Create",
      "marks": 0
    }}
  ]
}}

`id` starts at 1 and increments by 1.

## If the material is unusable

If the material is empty, gibberish, or contains no teachable subject content,
return exactly this instead and nothing else:

{{"error": "unusable_source", "reason": "one short sentence explaining why"}}

Return the JSON object now.
