# Prompt iteration log

Four versions each of the **generator prompt** and the **quality-check
prompt** - 8 versions in total - each run against the real API.

## How this was produced

[`tests/run_iterations.py`](../tests/run_iterations.py) holds the full text of
all eight versions, sends each to the API on one **fixed input**, and measures
the reply. Nothing here is estimated or remembered: every "Observed" section
below is taken from `tests/output/iterations.json`, which this repository
tracks so the numbers can be checked.

    python tests/run_iterations.py

**Fixed input for every generator version:** 4 MCQs on Operating Systems from
a 12-line block of CPU-scheduling notes, difficulty `medium`, Bloom's level
`Apply`, 2 marks each.

**Fixed input for every quality-check version:** the same specification plus a
**deliberately defective 6-question set** with five planted defects, so the
versions can be compared on what they catch:

| id | Planted defect |
|----|----------------|
| 1 | *(intended as a clean control - see the note under qc-v4)* |
| 2 | Two defensibly correct options; ambiguous answer key |
| 3 | Pure recall question **labelled "Apply"** (Bloom mismatch) |
| 4 | **Duplicate** of q1, reworded |
| 5 | **Wrong answer key** (ageing solves starvation, not the convoy effect) |
| 6 | **Not answerable from the source** (asks about MLFQ, absent from the notes) |

### Two caveats, stated up front

1. **Model.** This run used `gemini-3.5-flash-lite`. The 16-case test run in
   [`tests/test_cases.md`](../tests/test_cases.md) used `gemini-3.6-flash`.
   They differ because the free tier allows only **20 requests per day per
   model** and 3.6-flash was exhausted by the test run. Version-to-version
   comparisons below are therefore valid (same model throughout this run), but
   absolute scores are not directly comparable with the test-case document.
2. **JSON mode is deliberately OFF here.** The app sets
   `response_mime_type="application/json"`, which would force syntactically
   valid JSON regardless of the prompt and erase the v1-vs-v3 difference. To
   measure the **prompt**, `run_iterations.py` passes `json_mode=False`.

---

# Part 1 - Generator prompt

## `gen-v1` - bare instruction, no role, no format

**What changed, and on purpose:** Nothing yet - this is the baseline. A single sentence naming the task, the count and the subject. No role, no output format, no schema, no constraints.

**Why:** To establish what the model does when simply asked, so every later addition has something to be measured against.

**Observed (real run, gemini-3.5-flash-lite, 194 in / 244 out tokens, 6.3s):**

**Unusable.** The reply was prose, not data: *"Based on your notes, here are 4 exam
questions about Operating Systems: **Question 1 (Multiple Choice)** ..."*.
`extract_json()` failed with *"No JSON object found in the model response."*
The questions themselves were readable, but nothing downstream of the model
could consume them - no parsing, no validation, no quality check, no export.

**Verdict:** FAIL - 0/4 questions usable by the app.

<details>
<summary>Full prompt text for this version</summary>

**System instruction:**

```text
(none - this version sent no system instruction)
```

**User prompt:**

```text
Make 4 exam questions about Operating Systems from these notes.

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
```

</details>

---

## `gen-v2` - + role, + 'return JSON' in prose

**What changed, and on purpose:** Added a **role** ("You are a university professor who sets exam papers") and asked for JSON **in prose**: *"Return the result as JSON with a 'questions' list. Each question should have the question text, four options, the correct answer..."*

**Why:** To test the common assumption that naming a role and asking for JSON is enough.

**Observed (real run, gemini-3.5-flash-lite, 262 in / 727 out tokens, 5.5s):**

**Parsed, but still unusable - and this is the single most instructive result
in the log.** The model did return JSON with 4 questions and 4 options each.
It also invented its own field names, taken from the English words we used:

    { "question text": "...", "options": [...], "correct answer": "...",
      "Bloom's taxonomy level": "...", "difficulty": "...", "marks": 2 }

The validator rejected all four: *"Question 1 is missing field(s): question,
type, answer, bloom_level."* `answer_in_options` was `[False, False, False,
False]` - not because the answers were wrong, but because the key holding them
was called `correct answer`, not `answer`. The reply was also wrapped in a
json code fence, so `starts_with_brace` was `False`.

Describing a field in prose gets you a field named after your prose.

**Verdict:** FAIL - 4 questions generated, 0 accepted by the validator.

<details>
<summary>Full prompt text for this version</summary>

**System instruction:**

```text
You are a university professor who sets exam papers.
```

**User prompt:**

```text
Create 4 multiple-choice exam questions on Operating Systems
from the notes below. Difficulty: medium. Marks each: 2.

Notes:
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

Return the result as JSON with a "questions" list. Each question should have
the question text, four options, the correct answer, an explanation, the
difficulty, the Bloom's taxonomy level and the marks.
```

</details>

---

## `gen-v3` - + explicit schema, enums, output contract, MCQ constraints

**What changed, and on purpose:** Replaced the prose description with an **explicit JSON schema** (exact key names, types, and the allowed enum values for `type`, `difficulty` and `bloom_level`), added an **output contract** ("Reply with a single JSON object and nothing else. No prose, no markdown code fences."), and added **MCQ constraints** (exactly one correct option, three plausible distractors, no "all of the above", answer must be the verbatim option text).

**Why:** v2 showed that field names must be given, not described. The output contract targets the code fence and the preamble; the MCQ constraints target the ambiguous-options failure we had seen by hand.

**Observed (real run, gemini-3.5-flash-lite, 489 in / 795 out tokens, 5.0s):**

**First usable version.** `parsed=True`, `valid=True`, exactly 4 questions,
`starts_with_brace=True`, no code fence, all nine schema fields present,
`option_counts=[4,4,4,4]`, `answer_in_options=[True,True,True,True]`, and every
`bloom_level` correctly `"Apply"` as requested. No banned option phrases.
Input cost 489 tokens.

**Verdict:** PASS - 4/4 questions accepted by the validator.

<details>
<summary>Full prompt text for this version</summary>

**System instruction:**

```text
You are an experienced university examination setter.
You write exam questions that are ready to print.

Rules:
- Every MCQ has exactly one correct option and three plausible wrong options.
- Never use "all of the above" or "both A and B".
- The answer must be the full text of the correct option.
- Do not invent facts that are not in the source material.

Reply with a single JSON object and nothing else. No prose, no markdown code
fences.
```

**User prompt:**

```text
Generate exam questions from the material below.

<source_material subject="Operating Systems">
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
</source_material>

Specification:
- Number of questions: exactly 4
- Question types allowed: MCQ
- Difficulty: medium
- Bloom's taxonomy level: Apply
- Marks per question: 2

Required JSON shape:

{
  "questions": [
    {
      "id": 1,
      "question": "string",
      "type": "MCQ | Short Answer | Long Answer | True/False",
      "options": ["string", "string", "string", "string"],
      "answer": "string - verbatim text of the correct option",
      "explanation": "string",
      "difficulty": "easy | medium | hard",
      "bloom_level": "Remember | Understand | Apply | Analyze | Evaluate | Create",
      "marks": 0
    }
  ]
}

Return the JSON object now.
```

</details>

---

## `gen-v4` - FINAL: + grounding/Bloom/marks/duplicate/language rules, + unusable-source escape hatch

**What changed, and on purpose:** Kept v3 and added: **grounding rules** (answerable from the source only, never invent facts), **self-containment** (never write "according to the passage above"), **marks/effort matching** with permission to scale indefensible marks, a **no-duplicates rule**, a **language rule** (write in the source's language, keep JSON keys and enums English), and the **`unusable_source` escape hatch**.

**Why:** v3 produced structurally valid output, so the remaining failures were about question *quality* and about inputs v3 had no defined way to refuse. Each addition targets a specific failure seen in manual testing rather than being generic 'be better' instruction.

**Observed (real run, gemini-3.5-flash-lite, 1429 in / 866 out tokens, 8.1s):**

**Structurally identical to v3 on this input** - `valid=True`, 4 questions, all
fields, all answers in options, all Bloom `"Apply"` - which is the right
outcome: the v4 additions are not about structure, and they did not regress it.

Their value shows up on inputs v3 was never tested against, and those are
measured in [`tests/test_cases.md`](../tests/test_cases.md) rather than here:
the escape hatch produces a clean refusal on gibberish (case 13, zero questions
invented), the language rule produces Devanagari questions with English enums
(case 14), and the marks rule scales a 10-mark True/False down to 2 (case 15).

**The cost is real and worth stating:** v4's input grew to **1,429 tokens
against v3's 489**, roughly 3x, for no structural gain on a well-formed input.
That is the price of the guarantees, paid on every call.

**Verdict:** PASS - 4/4 accepted; chosen as final for behaviour v3 cannot be tested for here.

<details>
<summary>Full prompt text for this version</summary>

**System instruction:**

```text
You are an experienced university examination setter and subject-matter expert.
You have written and moderated end-semester question papers for an accredited
engineering university for fifteen years. You know how a question paper is
audited: every question must be answerable from the prescribed material, the
answer key must be defensible to a moderation committee, and the marks must
match the cognitive demand.

## What you do

Given a topic or a block of source material, you produce exam questions that
are ready to print - not drafts, not suggestions.

## Non-negotiable rules

1. **Ground every question in the source.** If source material is supplied,
   each question must be answerable using only that material plus standard
   background knowledge for the subject. Never invent facts, figures, authors,
   standards or numbers that are not in the source and not common textbook
   knowledge.
2. **One defensible answer.** Every MCQ must have exactly one correct option
   and three clearly wrong but plausible distractors. No "all of the above",
   no "both A and B", no two options that are arguably correct, no option that
   is wrong only on a technicality.
3. **Answer the question you asked.** The `answer` field must actually answer
   the question as worded. For MCQ, `answer` must be the full text of the
   correct option and must appear verbatim in `options`.
4. **Respect the requested cognitive level.** A "Remember" question asks for
   recall. An "Apply" question requires the student to use the concept on a new
   case. An "Analyze" or "Evaluate" question requires the student to compare,
   justify or critique. Do not label a recall question as "Apply".
5. **Match marks to effort.** A 1-2 mark question is answerable in a sentence.
   A 10+ mark question needs multiple parts or sustained argument. If the marks
   look wrong for the type, still honour the request but scale the scope of the
   question to fit.
6. **No duplicates and no near-duplicates.** Two questions that test the same
   fact in different words count as duplicates. Spread coverage across the
   source material.
7. **Self-contained.** Never write "according to the passage above", "as
   discussed in class", or "in the given figure". The student sees only the
   question.
8. **Language.** Write questions in the same language as the source material.
   If the source is not in English, keep the JSON keys and the enum values
   (`type`, `difficulty`, `bloom_level`) in English but write `question`,
   `options`, `answer` and `explanation` in the source language.

## Refusing impossible requests

If the source material is empty, pure gibberish, or carries no extractable
subject content, do **not** invent a topic. Return the error object described in
the user message instead.

## Output contract

Reply with a single JSON object and nothing else. No prose before it, no prose
after it, no markdown code fences, no comments inside the JSON. The first
character of your reply must be `{` and the last must be `}`.
```

**User prompt:**

```text
Generate exam questions from the material below.

<source_material subject="Operating Systems" kind="notes">
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
</source_material>

## Paper specification

- Subject: Operating Systems
- Number of questions: exactly 4
- Question types allowed: MCQ
- Difficulty: medium
- Bloom's taxonomy level: Apply
- Marks per question: 2

### How to apply the specification

- Produce **exactly 4** question objects. Not more, not fewer.
- Use only the allowed types. If several types are allowed, distribute them as
  evenly as the count permits (e.g. 5 questions and 2 allowed types -> 3 + 2).
- If difficulty is `mixed`, spread questions across easy / medium / hard and
  report the true per-question difficulty in each object.
- If Bloom level is `Mixed`, choose a suitable level per question and report it.
- Set `marks` to 2 on every question unless the type makes that
  indefensible (for example a True/False worth 10 marks) - in that case use the
  nearest defensible value and say so in `explanation`.
- `options` is a list of exactly 4 strings for MCQ, exactly
  `["True", "False"]` for True/False, and `[]` for Short Answer and Long Answer.
- `explanation` is the model answer or marking note for the evaluator: why the
  answer is correct, and for MCQ why the distractors are wrong. 1-3 sentences.

## Required JSON shape

{
  "questions": [
    {
      "id": 1,
      "question": "string - the question as the student will read it",
      "type": "MCQ | Short Answer | Long Answer | True/False",
      "options": ["string", "string", "string", "string"],
      "answer": "string - for MCQ, verbatim text of the correct option",
      "explanation": "string - model answer / marking note",
      "difficulty": "easy | medium | hard",
      "bloom_level": "Remember | Understand | Apply | Analyze | Evaluate | Create",
      "marks": 0
    }
  ]
}

`id` starts at 1 and increments by 1.

## If the material is unusable

If the material is empty, gibberish, or contains no teachable subject content,
return exactly this instead and nothing else:

{"error": "unusable_source", "reason": "one short sentence explaining why"}

Return the JSON object now.
```

</details>

---

# Part 2 - Quality-check prompt

## `qc-v1` - vague ask, no scale, no criteria, no format

**What changed, and on purpose:** Baseline. *"Check these exam questions and tell me if they are good."* No reviewer role, no scale, no criteria, no output format.

**Why:** To see what an unstructured review request produces.

**Observed (real run, gemini-3.5-flash-lite, 779 in / 1450 out tokens, 12.8s):**

**Unusable.** Prose again: *"Overall, this is a **solid set of exam questions**
for an introductory Operating Systems course... They are well-structured, have
clear metadata..."* - a confident approval of a set in which five of six
questions carry a deliberately planted defect.

It did not parse (*"Malformed JSON in model response: Extra data: line 16
column 4"*), so no score, no flags and no machine-readable output at all.

**Verdict:** FAIL - unparseable, and substantively wrong: it approved a defective set.

<details>
<summary>Full prompt text for this version</summary>

**System instruction:**

```text
(none - this version sent no system instruction)
```

**User prompt:**

```text
Check these exam questions and tell me if they are good.

[
  {
    "id": 1,
    "question": "Which CPU scheduling algorithm is non-preemptive and suffers from the convoy effect?",
    "type": "MCQ",
    "options": [
      "First-Come First-Served",
      "Round Robin",
      "Priority with ageing",
      "Multilevel queue"
    ],
    "answer": "First-Come First-Served",
    "explanation": "FCFS is non-preemptive and short processes queue behind one long process.",
    "difficulty": "medium",
    "bloom_level": "Understand",
    "marks": 2
  },
  {
    "id": 2,
    "question": "Which statement about Round Robin scheduling is true?",
    "type": "MCQ",
    "options": [
      "A very large time quantum makes it behave like FCFS",
      "A very small time quantum raises context-switch overhead",
      "It is a preemptive algorithm",
      "It requires knowing the next CPU burst length"
    ],
    "answer": "A very large time quantum makes it behave like FCFS",
    "explanation": "If the quantum exceeds the longest burst, RR degenerates to FCFS.",
    "difficulty": "medium",
    "bloom_level": "Understand",
    "marks": 2
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
    "marks": 2
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
    "marks": 2
  },
  {
    "id": 5,
    "question": "Which problem does the technique of ageing solve in priority scheduling?",
    "type": "MCQ",
    "options": [
      "The convoy effect",
      "Starvation",
      "Thrashing",
      "Deadlock"
    ],
    "answer": "The convoy effect",
    "explanation": "Ageing raises the priority of long-waiting processes.",
    "difficulty": "medium",
    "bloom_level": "Understand",
    "marks": 2
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
    "marks": 2
  }
]
```

</details>

---

## `qc-v2` - + reviewer role, + score out of 10, + 'return JSON' in prose

**What changed, and on purpose:** Added a **reviewer role**, an explicit **score out of 10 per question**, and a request for JSON with `id`, `score` and `issues`.

**Why:** To find out how much of the review quality comes from the scale and the role alone.

**Observed (real run, gemini-3.5-flash-lite, 818 in / 374 out tokens, 3.5s):**

**A large jump.** Parsed, 6 reviews, sensible spread: scores
`{1:10, 2:5, 3:8, 4:9, 5:2, 6:10}`. It caught **4 of the 5 planted defects** -
including the wrong answer key on q5 (scored 2.0) and the two-correct-options
ambiguity on q2 (scored 5.0).

**What it missed matters:** q6 asks about multilevel feedback queues, which do
not appear in the source notes at all. qc-v2 scored it **10.0**. It had no
*"relevance to source"* criterion and, critically, **was never given the source
material** - so it could not have caught it. A reviewer that cannot see the
source cannot judge grounding.

**Verdict:** PARTIAL - 4/5 defects caught; blind to source-grounding by construction.

<details>
<summary>Full prompt text for this version</summary>

**System instruction:**

```text
You are a reviewer who checks the quality of exam questions.
```

**User prompt:**

```text
Review each of the following exam questions. Give each one a
score out of 10 and list any problems you find.

[
  {
    "id": 1,
    "question": "Which CPU scheduling algorithm is non-preemptive and suffers from the convoy effect?",
    "type": "MCQ",
    "options": [
      "First-Come First-Served",
      "Round Robin",
      "Priority with ageing",
      "Multilevel queue"
    ],
    "answer": "First-Come First-Served",
    "explanation": "FCFS is non-preemptive and short processes queue behind one long process.",
    "difficulty": "medium",
    "bloom_level": "Understand",
    "marks": 2
  },
  {
    "id": 2,
    "question": "Which statement about Round Robin scheduling is true?",
    "type": "MCQ",
    "options": [
      "A very large time quantum makes it behave like FCFS",
      "A very small time quantum raises context-switch overhead",
      "It is a preemptive algorithm",
      "It requires knowing the next CPU burst length"
    ],
    "answer": "A very large time quantum makes it behave like FCFS",
    "explanation": "If the quantum exceeds the longest burst, RR degenerates to FCFS.",
    "difficulty": "medium",
    "bloom_level": "Understand",
    "marks": 2
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
    "marks": 2
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
    "marks": 2
  },
  {
    "id": 5,
    "question": "Which problem does the technique of ageing solve in priority scheduling?",
    "type": "MCQ",
    "options": [
      "The convoy effect",
      "Starvation",
      "Thrashing",
      "Deadlock"
    ],
    "answer": "The convoy effect",
    "explanation": "Ageing raises the priority of long-waiting processes.",
    "difficulty": "medium",
    "bloom_level": "Understand",
    "marks": 2
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
    "marks": 2
  }
]

Return JSON with a "reviews" list containing the id, the score and the issues.
```

</details>

---

## `qc-v3` - + six named criteria, + schema, + duplicate detection

**What changed, and on purpose:** Added the **six named criteria**, passed the **source material** into the prompt, added a **JSON schema** for the review objects, and added **duplicate detection** (`duplicate_of`).

**Why:** To close the q6 blind spot by giving the reviewer the source, and to make the judgement reproducible by naming what is being judged.

**Observed (real run, gemini-3.5-flash-lite, 1158 in / 610 out tokens, 5.8s):**

**Caught 5 of 5 planted defects**, including q6 (*"not answerable from the
provided source material"*) - the blind spot closed exactly as intended.

**But it broke the scale.** Scores came back as
`{1:0.7, 2:0.6, 3:0.5, 4:0.7, 5:0.2, 6:0.4}` - a 0-to-1 scale, not 0-to-10.
The schema example showed `"score": 0.0`, and with no stated range the model
read that float as a fraction. Against our 7.0 threshold **every question in
the paper would be flagged**, including good ones. A correct review, rendered
useless by an unusable number.

Spread collapsed to 0.5, so the scores also stopped discriminating.

**Verdict:** FAIL on calibration - 5/5 defects found, but the scores are unusable.

<details>
<summary>Full prompt text for this version</summary>

**System instruction:**

```text
You are a moderation officer on a university examination
board. You review question papers drafted by teachers and either approve them
or send them back.

Judge each question on: clarity, correctness of the answer key, whether MCQs
have a single unambiguous answer, whether the difficulty matches, whether the
Bloom's taxonomy level matches, and whether it is relevant to the source
material. Also flag duplicates.

Reply with a single JSON object and nothing else. No prose, no markdown code
fences.
```

**User prompt:**

```text
Review the question set below.

Specification: subject Operating Systems, difficulty medium, Bloom's level
Apply, 2 marks per question.

<source_material>
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
</source_material>

Question set:
[
  {
    "id": 1,
    "question": "Which CPU scheduling algorithm is non-preemptive and suffers from the convoy effect?",
    "type": "MCQ",
    "options": [
      "First-Come First-Served",
      "Round Robin",
      "Priority with ageing",
      "Multilevel queue"
    ],
    "answer": "First-Come First-Served",
    "explanation": "FCFS is non-preemptive and short processes queue behind one long process.",
    "difficulty": "medium",
    "bloom_level": "Understand",
    "marks": 2
  },
  {
    "id": 2,
    "question": "Which statement about Round Robin scheduling is true?",
    "type": "MCQ",
    "options": [
      "A very large time quantum makes it behave like FCFS",
      "A very small time quantum raises context-switch overhead",
      "It is a preemptive algorithm",
      "It requires knowing the next CPU burst length"
    ],
    "answer": "A very large time quantum makes it behave like FCFS",
    "explanation": "If the quantum exceeds the longest burst, RR degenerates to FCFS.",
    "difficulty": "medium",
    "bloom_level": "Understand",
    "marks": 2
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
    "marks": 2
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
    "marks": 2
  },
  {
    "id": 5,
    "question": "Which problem does the technique of ageing solve in priority scheduling?",
    "type": "MCQ",
    "options": [
      "The convoy effect",
      "Starvation",
      "Thrashing",
      "Deadlock"
    ],
    "answer": "The convoy effect",
    "explanation": "Ageing raises the priority of long-waiting processes.",
    "difficulty": "medium",
    "bloom_level": "Understand",
    "marks": 2
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
    "marks": 2
  }
]

Return:

{
  "reviews": [
    {
      "id": 1,
      "score": 0.0,
      "issues": ["string"],
      "suggested_fix": "string",
      "duplicate_of": null
    }
  ]
}

Return the JSON object now.
```

</details>

---

## `qc-v4` - FINAL: + per-criterion sub-scores, + score = min(criteria), + calibrated 1-10 rubric, + do-not-invent-issues instruction

**What changed, and on purpose:** Added **per-criterion sub-scores** (six integers 0-10), the rule **`score` = the minimum of the six**, an explicit **1-10 calibration rubric** with described bands (9-10 print as is / 7-8 minor nit / 4-6 must fix / 1-3 fatal), the instruction **"do not be generous"**, and the counterweight **"do not manufacture a complaint to look thorough"**.

**Why:** v3 proved the criteria worked and the scale did not. Naming the range in words, giving each band a meaning, and requiring the six sub-scores forces the model to commit to a number per criterion instead of producing one vague overall feeling. `min()` rather than `mean()` because averaging lets a fatal defect hide behind five good scores.

**Observed (real run, gemini-3.5-flash-lite, 2106 in / 1218 out tokens, 4.9s):**

**Scale fixed and reasoning exposed.** `has_sub_scores=True`; scores returned on
the intended 0-10 scale as `{1:5, 2:5, 3:2, 4:2, 5:3, 6:2}`, spread 3.0, and
**5 of 5 planted defects caught**. Each review now shows its working, e.g. q1:
`{"clarity":10, "answer_correctness":10, "single_answer":10,
"difficulty_match":10, "bloom_match":5, "source_relevance":10}` -> score 5.

The `min()` rule is visibly doing its job: q1 scores 10 on five criteria and 5
on one, and comes out **5**, not the 9.2 an average would have produced.

**Verdict:** PASS - adopted as final.

#### A flaw this run exposed in our own test design

The harness labelled q1 the "clean control" and therefore counted
`false_positive_on_control=True` for v3 and v4, which flagged it.

**The harness is wrong, not the reviewer.** The paper specification given to
the quality checker requests Bloom's level **Apply**, and q1 is labelled
**Understand**. That is a genuine Bloom mismatch, so v3 and v4 were right to
mark it down, and qc-v2 - which scored it a clean 10.0 - was wrong. Our
"control" question was never defect-free with respect to the spec it was
reviewed against.

We are recording this rather than quietly fixing the fixture, because the
mislabelled metric is itself the finding: the v4 prompt was strict enough to
catch a defect its own authors had not noticed they had planted.

#### A real limitation, not caught by any version

**No version detected the duplicate.** q4 restates q1 (both ask which algorithm
suffers the convoy effect), and `duplicate_detected` is `False` for v2, v3 and
v4 alike, even though v3 and v4 both define `duplicate_of` and instruct the
model to populate it.

Duplicate detection *does* work in the deployed app - test case 12 flagged five
near-duplicates in a 20-question paper - so this is not dead code. The honest
reading is that it is **unreliable on small sets and on this model**, and that
a faculty member should not assume a clean `duplicate_of` means a duplicate-free
paper.

<details>
<summary>Full prompt text for this version</summary>

**System instruction:**

```text
You are the moderation officer on a university examination board. Question
papers drafted by subject teachers land on your desk and you either sign them
off or send them back. You did not write these questions and you have no stake
in them. Your reputation rests on catching the defective question before it
reaches a student, so you are deliberately sceptical.

## Your job

Score each question out of 10 and say precisely what is wrong with it.

## The six criteria

Judge every question against all six. Each is worth up to 10; the question's
score is the **lowest** of the six, because one fatal defect sinks a question
no matter how good it is otherwise.

1. **Clarity** - unambiguous wording, no double negatives, no two readings, no
   undefined jargon, no dependence on unseen context ("the passage above").
2. **Answer-key correctness** - is `answer` actually correct, and does it
   answer the question as worded? Verify it yourself before you accept it.
   For MCQ, `answer` must appear verbatim in `options`.
3. **Single unambiguous answer (MCQ / True-False)** - exactly one option
   defensibly correct; distractors plausible but clearly wrong; no "all of the
   above"; no two options arguably right. Score 10 on this criterion for Short
   Answer and Long Answer questions, which have no options.
4. **Difficulty match** - does the question's real difficulty match its
   declared `difficulty`, and the paper's requested difficulty?
5. **Bloom's level match** - does the cognitive demand match the declared
   `bloom_level`? A recall question labelled "Apply" or "Analyze" is a defect,
   and a common one - check it carefully.
6. **Relevance to source** - answerable from the supplied source material plus
   standard background for the subject. A question about facts absent from the
   source is a defect even if the facts are true.

Then, across the whole set:

7. **Duplicates** - list the `id` of any question that tests substantially the
   same point as an earlier question. Different wording, same tested fact
   equals a duplicate.

## How to score

- **9-10** - print it as is.
- **7-8** - usable; minor wording or labelling nit.
- **4-6** - must be fixed before use: ambiguous, mislabelled, or weakly
  grounded.
- **1-3** - fatally defective: wrong answer key, two correct options, or not
  answerable from the source.

Do not be generous. A question you would wave through only because it is
"probably fine" scores 6, not 8. Reserve 9-10 for questions you would defend in
front of the board. If a question genuinely has no defect, say so: `issues` is
`[]` and `suggested_fix` is `""`. Do not manufacture a complaint to look
thorough.

## Output contract

Reply with a single JSON object and nothing else. No prose, no markdown code
fences, no comments. The first character of your reply must be `{` and the last
must be `}`.
```

**User prompt:**

```text
Moderate the question set below against the paper specification and the source
material it was drafted from.

## Paper specification the teacher was given

- Subject: Operating Systems
- Requested difficulty: medium
- Requested Bloom's taxonomy level: Apply
- Requested marks per question: 2
- Requested question types: MCQ

## Source material the questions must be answerable from

<source_material>
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
</source_material>

## Question set under review

[
  {
    "id": 1,
    "question": "Which CPU scheduling algorithm is non-preemptive and suffers from the convoy effect?",
    "type": "MCQ",
    "options": [
      "First-Come First-Served",
      "Round Robin",
      "Priority with ageing",
      "Multilevel queue"
    ],
    "answer": "First-Come First-Served",
    "explanation": "FCFS is non-preemptive and short processes queue behind one long process.",
    "difficulty": "medium",
    "bloom_level": "Understand",
    "marks": 2
  },
  {
    "id": 2,
    "question": "Which statement about Round Robin scheduling is true?",
    "type": "MCQ",
    "options": [
      "A very large time quantum makes it behave like FCFS",
      "A very small time quantum raises context-switch overhead",
      "It is a preemptive algorithm",
      "It requires knowing the next CPU burst length"
    ],
    "answer": "A very large time quantum makes it behave like FCFS",
    "explanation": "If the quantum exceeds the longest burst, RR degenerates to FCFS.",
    "difficulty": "medium",
    "bloom_level": "Understand",
    "marks": 2
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
    "marks": 2
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
    "marks": 2
  },
  {
    "id": 5,
    "question": "Which problem does the technique of ageing solve in priority scheduling?",
    "type": "MCQ",
    "options": [
      "The convoy effect",
      "Starvation",
      "Thrashing",
      "Deadlock"
    ],
    "answer": "The convoy effect",
    "explanation": "Ageing raises the priority of long-waiting processes.",
    "difficulty": "medium",
    "bloom_level": "Understand",
    "marks": 2
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
    "marks": 2
  }
]

## What to return

One review object per question, in the same order, with the same `id`.

{
  "reviews": [
    {
      "id": 1,
      "score": 0.0,
      "scores": {
        "clarity": 0,
        "answer_correctness": 0,
        "single_answer": 0,
        "difficulty_match": 0,
        "bloom_match": 0,
        "source_relevance": 0
      },
      "issues": ["short, specific defect - name the criterion it breaks"],
      "suggested_fix": "one concrete rewrite instruction, or empty string if none needed",
      "duplicate_of": null
    }
  ],
  "set_level_notes": "one or two sentences on the set as a whole: coverage, type and difficulty balance, anything a moderator should know"
}

Rules for the fields:

- `scores` holds the six per-criterion scores, each an integer 0-10.
- `score` is the **minimum** of the six per-criterion scores, as a number.
- `issues` is `[]` when the question is clean. Each issue is one short sentence
  that names the defect, not a general comment.
- `suggested_fix` is an instruction a teacher can act on ("replace option C,
  which is also correct, with ..."), not a restatement of the issue.
- `duplicate_of` is the `id` of the earlier question it duplicates, or `null`.
- Review every question. Return exactly 6 review objects.

Return the JSON object now.
```

</details>

---

# Summary

## Generator prompt

| Version | Deliberate change | Parsed? | Validator-accepted | Input tokens |
|---|---|---|---|---|
| v1 | baseline, no role/format | No - prose | 0/4 | 194 |
| v2 | + role, + "return JSON" in prose | Yes | **0/4** (invented field names) | 262 |
| v3 | + explicit schema, enums, output contract, MCQ constraints | Yes | **4/4** | 489 |
| v4 | + grounding, Bloom, marks, duplicates, language, escape hatch | Yes | **4/4** | 1,429 |

**The decisive change was v2 to v3.** v2 proves that naming a role and asking
for JSON in prose is *not* enough: the model returned well-formed JSON whose
keys were `"question text"` and `"correct answer"`, and the validator rejected
every question. Field names must be specified, not described.

## Quality-check prompt

| Version | Deliberate change | Parsed? | Defects caught | Scale | Sub-scores |
|---|---|---|---|---|---|
| v1 | baseline, vague ask | No - prose | 0/5 (approved the set) | - | No |
| v2 | + role, + score /10 | Yes | 4/5 | 0-10 OK | No |
| v3 | + six criteria, + source material, + schema | Yes | **5/5** | **0-1 BROKEN** | No |
| v4 | + sub-scores, + `min()`, + calibrated rubric | Yes | **5/5** | 0-10 OK | Yes |

**Two changes earned their place.** Passing the **source material** in at v3 is
what closed the q6 blind spot - a reviewer that cannot see the source cannot
judge grounding. Writing the **range in words with described bands** at v4 is
what fixed the scale v3 silently broke by reading `"score": 0.0` as a fraction.

**`min()` over `mean()`** is visible in v4's output: q1 scores 10 on five
criteria and 5 on one, and the question scores **5**, where an average would
have returned 9.2 and let the defect through.

## What we would do next, with quota to spare

- Re-run all eight versions on `gemini-3.6-flash` and on a Pro-tier model, to
  separate *prompt* effects from *model* effects. Everything above holds the
  model fixed, so it measures the prompt - but it measures the prompt **on one
  model**.
- Chase the duplicate-detection miss, which no version caught on the 6-question
  fixture despite working on a 20-question paper in the live test run.
- Run each version 3-5 times rather than once. Single samples cannot separate a
  real improvement from run-to-run variance, and we are not claiming otherwise.
