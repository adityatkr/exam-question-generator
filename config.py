"""Central configuration for the Exam Question Generator.

Every tunable constant lives here so the model, limits and thresholds can be
changed in exactly one place.
"""

# --- Model -------------------------------------------------------------------
# Change this single constant to swap models.
#
# Chosen on 2026-11-22 by listing the models available to our key
# (client.models.list()) and calling each candidate.
#
# Two separate constraints decided this, both measured, not assumed:
#   * Capacity: gemini-3.8-flash and gemini-3.7-flash returned 503 UNAVAILABLE
#     ("high demand") on every attempt.
#   * Daily quota: the free tier allows only 20 generateContent requests per
#     day PER MODEL (quotaId GenerateRequestsPerDayPerProjectPerModel-FreeTier).
#     Building and testing this project exhausted 3.6-flash and 3.5-flash.
#
# gemini-3.5-flash-lite had capacity and quota, so it is the default: a demo
# that runs beats a marginally better model that 429s. The fallback chain below
# is ordered newest-first, so the app upgrades itself automatically as capacity
# and quota free up (quota resets ~15h after exhaustion).
MODEL_ID = "gemini-3.5-flash-lite"

# Tried in order if MODEL_ID returns 503 UNAVAILABLE / 429. Newer Flash models
# are listed first so the app upgrades itself automatically once demand on them
# eases, then degrades to older ones rather than failing.
FALLBACK_MODELS = [
    "gemini-3.8-flash",
    "gemini-3.7-flash",
    "gemini-3.6-flash",
    "gemini-3.5-flash",
]

# Thinking depth. One of: low | medium | high.
# Generation runs at "low": the generator prompt is highly specified, so extra
# reasoning buys little. Review runs at "medium": verifying an answer key and
# judging Bloom's level is the part that genuinely needs reasoning.
GENERATOR_THINKING = "low"
QUALITY_THINKING = "medium"

# Hard ceiling on one HTTP request. Without this the SDK retries a 503 for
# minutes on end -- which is exactly what we hit while choosing the model.
REQUEST_TIMEOUT_MS = 120_000
# Transport-level retry attempts per model before we move to the next model.
HTTP_RETRY_ATTEMPTS = 2

# Free-tier keys have a low requests-per-minute limit, and a 429 there means
# "wait", not "this model is broken" -- so we wait on the SAME model rather
# than falling straight through to the fallback chain. Measured: the free-tier
# RPM window recovers in well under a minute.
RATE_LIMIT_RETRIES = 3
RATE_LIMIT_WAIT_S = 20

# --- Pricing (for the cost estimate in the diagnostics panel) ----------------
# gemini-3.6-flash introductory rate, valid through 2026-12-31; rises to
# 1.50 / 7.50 on 2027-01-01. Output price includes thinking tokens.
# Source: https://ai.google.dev/gemini-api/docs/pricing
PRICE_INPUT_PER_MTOK = 0.75
PRICE_OUTPUT_PER_MTOK = 3.75

# --- Output-token ceilings ---------------------------------------------------
# Generation scales with the number of questions so we never pay for a 20k
# ceiling when the user asked for 2 questions.
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
AI_TOOL = "Google Gemini (google-genai Python SDK)"
