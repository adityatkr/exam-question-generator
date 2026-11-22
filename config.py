"""Central configuration for the Exam Question Generator.

Every tunable constant lives here so the model, limits and thresholds can be
changed in exactly one place.
"""

# --- Model -------------------------------------------------------------------
# Change this single constant to swap models.
MODEL_ID = "claude-opus-5-5"

# Thinking depth / token spend. One of: low | medium | high | xhigh | max.
# Claude Opus 5.5 always thinks; `effort` is the only depth control.
GENERATOR_EFFORT = "medium"
QUALITY_EFFORT = "medium"

# Output-token ceilings. Generation scales with the number of questions so we
# never pay for 16k tokens when the user asked for 2 questions.
MAX_TOKENS_BASE = 2000
MAX_TOKENS_PER_QUESTION = 700
MAX_TOKENS_CEILING = 20000
MAX_TOKENS_QUALITY_BASE = 1500
MAX_TOKENS_QUALITY_PER_QUESTION = 450

# --- Input limits ------------------------------------------------------------
MIN_QUESTIONS = 1
MAX_QUESTIONS = 20
MIN_SOURCE_CHARS = 10
MAX_SOURCE_CHARS = 20000        # ~5k tokens of notes; keeps cost predictable
MAX_SUBJECT_CHARS = 120
MIN_MARKS = 1
MAX_MARKS = 50

# --- Quality check -----------------------------------------------------------
# Questions scoring below this (out of 10) are flagged for regeneration.
QUALITY_THRESHOLD = 7.0

# --- Vocabularies ------------------------------------------------------------
QUESTION_TYPES = ["MCQ", "Short Answer", "Long Answer", "True/False"]
DIFFICULTIES = ["easy", "medium", "hard", "mixed"]
BLOOM_LEVELS = [
    "Remember",
    "Understand",
    "Apply",
    "Analyze",
    "Evaluate",
    "Create",
    "Mixed",
]

# --- Retry -------------------------------------------------------------------
# Spec: retry ONCE on malformed JSON output.
MAX_JSON_ATTEMPTS = 2

APP_TITLE = "Exam Question Generator with Quality Check"
TEAM = "Alt+F4 (PE-B65-01) - Aditya Thakur, Divyansh Vijayvargiya"
COURSE = "SCSE4041 - Prompt Engineering, Milestone 2"
