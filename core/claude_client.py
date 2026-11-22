"""Thin wrapper around the Anthropic Python SDK.

Every Claude call in the app goes through `call_claude`, so model selection,
API-key resolution, token accounting and error translation live in one place.

Key resolution order (no key is ever hard-coded):
  1. `st.secrets["ANTHROPIC_API_KEY"]` -- how Streamlit Community Cloud
     supplies it in production.
  2. the `ANTHROPIC_API_KEY` environment variable.
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

import anthropic

import config


class ClaudeError(RuntimeError):
    """A user-presentable failure from the Claude API."""


class MissingAPIKey(ClaudeError):
    pass


# ---------------------------------------------------------------------------
# API key + client
# ---------------------------------------------------------------------------

SECRETS_PATH = Path(__file__).resolve().parent.parent / ".streamlit" / "secrets.toml"


def get_api_key() -> str | None:
    """Resolve the API key: Streamlit secrets, then env var, then secrets.toml."""
    try:
        import streamlit as st

        key = st.secrets.get("ANTHROPIC_API_KEY")  # type: ignore[union-attr]
        if key:
            return str(key).strip()
    except Exception:
        # Not running under Streamlit, or no secrets file. Fall through.
        pass

    key = os.environ.get("ANTHROPIC_API_KEY")
    if key and key.strip():
        return key.strip()

    # Plain-file fallback so `python tests/run_tests.py` works with no env var.
    if SECRETS_PATH.exists():
        try:
            with SECRETS_PATH.open("rb") as handle:
                data = tomllib.load(handle)
        except (tomllib.TOMLDecodeError, OSError):
            return None
        key = data.get("ANTHROPIC_API_KEY")
        if key and str(key).strip() and "REPLACE" not in str(key):
            return str(key).strip()

    return None


def get_client() -> anthropic.Anthropic:
    key = get_api_key()
    if not key:
        raise MissingAPIKey(
            "No Anthropic API key found. Add ANTHROPIC_API_KEY to "
            ".streamlit/secrets.toml (local) or to the app's Secrets box on "
            "Streamlit Community Cloud (deployed)."
        )
    return anthropic.Anthropic(api_key=key)


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
class ClaudeResponse:
    text: str
    stop_reason: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    model: str = config.MODEL_ID
    request_id: str | None = None
    thinking: str = ""

    @property
    def estimated_cost_usd(self) -> float:
        """Rough cost at Claude Opus 5.5 list pricing ($4 / $20 per MTok)."""
        return (self.input_tokens / 1e6) * 4.0 + (self.output_tokens / 1e6) * 20.0


@dataclass
class UsageTally:
    """Accumulates token usage across the calls made for one paper."""

    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    details: list[dict[str, Any]] = field(default_factory=list)

    def add(self, label: str, response: ClaudeResponse) -> None:
        self.calls += 1
        self.input_tokens += response.input_tokens
        self.output_tokens += response.output_tokens
        self.details.append(
            {
                "label": label,
                "input_tokens": response.input_tokens,
                "output_tokens": response.output_tokens,
            }
        )

    @property
    def estimated_cost_usd(self) -> float:
        return (self.input_tokens / 1e6) * 4.0 + (self.output_tokens / 1e6) * 20.0


def call_claude(
    system: str,
    user: str,
    max_tokens: int,
    effort: str = "medium",
    client: anthropic.Anthropic | None = None,
) -> ClaudeResponse:
    """Send one system+user prompt pair to Claude and return the text reply.

    Streams the response: generation of a 20-question paper can be long, and
    streaming keeps a big `max_tokens` from running into HTTP timeouts.

    Claude Opus 5.5 always thinks, so `thinking` is deliberately omitted and
    depth is controlled with `output_config.effort` instead.
    """
    client = client or get_client()

    try:
        with client.messages.stream(
            model=config.MODEL_ID,
            max_tokens=max_tokens,
            system=system,
            output_config={"effort": effort},
            messages=[{"role": "user", "content": user}],
        ) as stream:
            message = stream.get_final_message()
    except anthropic.AuthenticationError as exc:
        raise ClaudeError(
            "Anthropic rejected the API key. Check ANTHROPIC_API_KEY."
        ) from exc
    except anthropic.PermissionDeniedError as exc:
        raise ClaudeError(
            "This API key is not permitted to use "
            f"{config.MODEL_ID}. Check the key's workspace permissions."
        ) from exc
    except anthropic.NotFoundError as exc:
        raise ClaudeError(
            f"Model '{config.MODEL_ID}' was not found for this account. "
            "Update MODEL_ID in config.py."
        ) from exc
    except anthropic.RateLimitError as exc:
        retry_after = "60"
        try:
            retry_after = exc.response.headers.get("retry-after", "60")
        except Exception:
            pass
        raise ClaudeError(
            f"Rate limited by the Anthropic API. Try again in {retry_after}s."
        ) from exc
    except anthropic.BadRequestError as exc:
        raise ClaudeError(f"The API rejected the request: {exc.message}") from exc
    except anthropic.APIStatusError as exc:
        if exc.status_code >= 500:
            raise ClaudeError(
                f"Anthropic server error ({exc.status_code}). Try again shortly."
            ) from exc
        raise ClaudeError(f"Anthropic API error: {exc.message}") from exc
    except anthropic.APIConnectionError as exc:
        raise ClaudeError(
            "Could not reach the Anthropic API. Check the network connection."
        ) from exc

    # A safety classifier can decline a request: HTTP 200, stop_reason "refusal".
    if message.stop_reason == "refusal":
        detail = ""
        if getattr(message, "stop_details", None):
            detail = f" (category: {message.stop_details.category})"
        raise ClaudeError(
            f"Claude declined this request{detail}. Rephrase the topic or notes."
        )

    text_parts: list[str] = []
    thinking_parts: list[str] = []
    for block in message.content:
        if block.type == "text":
            text_parts.append(block.text)
        elif block.type == "thinking":
            thinking_parts.append(getattr(block, "thinking", "") or "")

    usage = message.usage
    return ClaudeResponse(
        text="".join(text_parts).strip(),
        stop_reason=message.stop_reason,
        input_tokens=getattr(usage, "input_tokens", 0) or 0,
        output_tokens=getattr(usage, "output_tokens", 0) or 0,
        model=message.model,
        request_id=getattr(message, "_request_id", None),
        thinking="".join(thinking_parts).strip(),
    )
