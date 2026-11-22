"""Thin wrapper around the Google Gemini SDK (`google-genai`).

Every Gemini call in the app goes through `call_gemini`, so model selection,
API-key resolution, token accounting, timeouts and error translation live in
one place.

Key resolution order (no key is ever hard-coded):
  1. `st.secrets["GEMINI_API_KEY"]` -- how Streamlit Community Cloud supplies
     it in production.
  2. the `GEMINI_API_KEY` environment variable.
  3. `.streamlit/secrets.toml` read directly -- so the test scripts work from
     the same file the app uses, without exporting an environment variable.

That file is git-ignored and must never be committed.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from google import genai
from google.genai import errors as genai_errors
from google.genai import types

import config


class GeminiError(RuntimeError):
    """A user-presentable failure from the Gemini API."""


class MissingAPIKey(GeminiError):
    pass


# ---------------------------------------------------------------------------
# API key + client
# ---------------------------------------------------------------------------

SECRETS_PATH = Path(__file__).resolve().parent.parent / ".streamlit" / "secrets.toml"
KEY_NAME = "GEMINI_API_KEY"


def get_api_key() -> str | None:
    """Resolve the API key: Streamlit secrets, then env var, then secrets.toml."""
    try:
        import streamlit as st

        key = st.secrets.get(KEY_NAME)  # type: ignore[union-attr]
        if key:
            return str(key).strip()
    except Exception:
        # Not running under Streamlit, or no secrets file. Fall through.
        pass

    key = os.environ.get(KEY_NAME)
    if key and key.strip():
        return key.strip()

    # Plain-file fallback so `python tests/run_tests.py` works with no env var.
    if SECRETS_PATH.exists():
        try:
            with SECRETS_PATH.open("rb") as handle:
                data = tomllib.load(handle)
        except (tomllib.TOMLDecodeError, OSError):
            return None
        key = data.get(KEY_NAME)
        if key and str(key).strip() and "REPLACE" not in str(key):
            return str(key).strip()

    return None


def get_client() -> genai.Client:
    key = get_api_key()
    if not key:
        raise MissingAPIKey(
            f"No Gemini API key found. Add {KEY_NAME} to "
            ".streamlit/secrets.toml (local) or to the app's Secrets box on "
            "Streamlit Community Cloud (deployed). Get a free key at "
            "https://aistudio.google.com/apikey"
        )
    return genai.Client(
        api_key=key,
        http_options=types.HttpOptions(
            timeout=config.REQUEST_TIMEOUT_MS,
            # Without a bounded attempt count the SDK retries a 503 for
            # minutes with no output. Bound it here and let call_gemini fall
            # back to the next model instead.
            retry_options=types.HttpRetryOptions(
                attempts=config.HTTP_RETRY_ATTEMPTS
            ),
        ),
    )


# ---------------------------------------------------------------------------
# Token budgets
# ---------------------------------------------------------------------------

def generation_max_tokens(num_questions: int) -> int:
    """Scale the output ceiling with the size of the paper, to control cost."""
    budget = config.MAX_TOKENS_BASE + config.MAX_TOKENS_PER_QUESTION * num_questions
    return min(budget, config.MAX_TOKENS_CEILING)


def quality_max_tokens(num_questions: int) -> int:
    budget = (
        config.MAX_TOKENS_QUALITY_BASE
        + config.MAX_TOKENS_QUALITY_PER_QUESTION * num_questions
    )
    return min(budget, config.MAX_TOKENS_CEILING)


# ---------------------------------------------------------------------------
# The call
# ---------------------------------------------------------------------------

@dataclass
class GeminiResponse:
    text: str
    finish_reason: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    thought_tokens: int = 0
    model: str = config.MODEL_ID
    # Models that returned 503/429/404 before one succeeded, for diagnostics.
    fell_back_from: list[str] = field(default_factory=list)

    @property
    def estimated_cost_usd(self) -> float:
        """Rough cost at the configured per-million-token prices."""
        return (
            self.input_tokens / 1e6 * config.PRICE_INPUT_PER_MTOK
            + (self.output_tokens + self.thought_tokens)
            / 1e6
            * config.PRICE_OUTPUT_PER_MTOK
        )


@dataclass
class UsageTally:
    """Accumulates token usage across the calls made for one paper."""

    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    thought_tokens: int = 0
    details: list[dict[str, Any]] = field(default_factory=list)

    def add(self, label: str, response: GeminiResponse) -> None:
        self.calls += 1
        self.input_tokens += response.input_tokens
        self.output_tokens += response.output_tokens
        self.thought_tokens += response.thought_tokens
        self.details.append(
            {
                "label": label,
                "input_tokens": response.input_tokens,
                "output_tokens": response.output_tokens,
                "thought_tokens": response.thought_tokens,
            }
        )

    @property
    def estimated_cost_usd(self) -> float:
        return (
            self.input_tokens / 1e6 * config.PRICE_INPUT_PER_MTOK
            + (self.output_tokens + self.thought_tokens)
            / 1e6
            * config.PRICE_OUTPUT_PER_MTOK
        )


def _one_call(
    client: genai.Client,
    model: str,
    system: str,
    user: str,
    max_tokens: int,
    json_mode: bool,
    thinking: str,
) -> Any:
    """One generate_content call. Raises the SDK's own exceptions."""
    return client.models.generate_content(
        model=model,
        contents=user,
        config=types.GenerateContentConfig(
            system_instruction=system or None,
            max_output_tokens=max_tokens,
            response_mime_type="application/json" if json_mode else "text/plain",
            thinking_config=types.ThinkingConfig(thinking_level=thinking),
        ),
    )


def call_gemini(
    system: str,
    user: str,
    max_tokens: int,
    json_mode: bool = True,
    thinking: str | None = None,
    client: genai.Client | None = None,
) -> GeminiResponse:
    """Send one system instruction + user prompt to Gemini and return the text.

    `json_mode` sets `response_mime_type="application/json"`, which makes the
    model emit syntactically valid JSON. It does **not** guarantee the JSON has
    the shape we asked for -- that is still the job of core/validators.py, and
    it is why the retry-once loop still earns its place. The prompt-iteration
    script turns `json_mode` off for the early prompt versions so the effect of
    the prompt alone can be measured.

    Flash models return 503 UNAVAILABLE under load often enough that one
    overloaded model must not break the app, so on 503/429 we move down
    `config.FALLBACK_MODELS` before giving up.
    """
    client = client or get_client()
    thinking = thinking or config.GENERATOR_THINKING

    candidates = [config.MODEL_ID, *config.FALLBACK_MODELS]
    overloaded: list[str] = []
    response = None
    used_model = config.MODEL_ID

    for index, model in enumerate(candidates):
        try:
            response = _one_call(
                client, model, system, user, max_tokens, json_mode, thinking
            )
            used_model = model
            break
        except genai_errors.ServerError as exc:
            # 503 "high demand" / 500. Try the next model.
            overloaded.append(f"{model} ({getattr(exc, 'code', '5xx')})")
            if index == len(candidates) - 1:
                raise GeminiError(
                    "Every configured Gemini model is currently unavailable: "
                    + ", ".join(overloaded)
                    + ". This is a temporary capacity problem at Google, not a "
                    "problem with the app or the key. Wait a minute and retry."
                ) from exc
            continue
        except genai_errors.ClientError as exc:
            code = getattr(exc, "code", None)
            if code == 429:
                overloaded.append(f"{model} (429 quota)")
                if index == len(candidates) - 1:
                    raise GeminiError(
                        "Rate limited / quota exhausted on every configured "
                        "model: " + ", ".join(overloaded) + ". Check your quota "
                        "at https://aistudio.google.com."
                    ) from exc
                continue
            if code == 404:
                # This model is not on the key; try the next one.
                overloaded.append(f"{model} (404 not available)")
                if index == len(candidates) - 1:
                    raise GeminiError(
                        f"None of the configured models are available to this "
                        "key: " + ", ".join(overloaded) + ". Update MODEL_ID in "
                        "config.py."
                    ) from exc
                continue
            if code == 400 and "API key" in str(exc):
                raise GeminiError(
                    f"Google rejected the API key. Check {KEY_NAME}."
                ) from exc
            if code == 403:
                raise GeminiError(
                    f"This API key is not permitted to use {model}. Check the "
                    "key's project and permissions."
                ) from exc
            raise GeminiError(
                f"The Gemini API rejected the request: {exc}"
            ) from exc
        except genai_errors.APIError as exc:
            raise GeminiError(f"Gemini API error: {exc}") from exc
        except Exception as exc:  # transport-level failures
            name = type(exc).__name__
            if "Timeout" in name or "timeout" in str(exc).lower():
                raise GeminiError(
                    "The Gemini request timed out after "
                    f"{config.REQUEST_TIMEOUT_MS // 1000}s. Ask for fewer "
                    "questions, or raise REQUEST_TIMEOUT_MS in config.py."
                ) from exc
            raise GeminiError(
                f"Could not reach the Gemini API ({name}): {exc}"
            ) from exc

    if response is None:  # pragma: no cover - defensive
        raise GeminiError("No response from any configured Gemini model.")

    # Safety filters and token caps surface as a finish_reason, not an exception.
    candidate = response.candidates[0] if response.candidates else None
    finish_reason = None
    if candidate is not None and candidate.finish_reason is not None:
        finish_reason = str(
            getattr(candidate.finish_reason, "name", candidate.finish_reason)
        )

    if finish_reason in {"SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST"}:
        raise GeminiError(
            f"Gemini blocked this request ({finish_reason}). Rephrase the "
            "topic or notes."
        )
    if finish_reason == "RECITATION":
        raise GeminiError(
            "Gemini blocked the reply as a recitation of copyrighted text. "
            "Paraphrase the notes and try again."
        )

    usage = response.usage_metadata
    text = response.text or ""

    return GeminiResponse(
        text=text.strip(),
        finish_reason=finish_reason,
        input_tokens=getattr(usage, "prompt_token_count", 0) or 0,
        output_tokens=getattr(usage, "candidates_token_count", 0) or 0,
        thought_tokens=getattr(usage, "thoughts_token_count", 0) or 0,
        model=used_model,
        fell_back_from=overloaded,
    )
