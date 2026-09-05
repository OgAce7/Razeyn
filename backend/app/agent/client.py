"""
Groq API client wrapper for the investigation agent.

This is the ONLY place in the agent module that talks to the network.
Keeping it isolated means:
  - investigate.py can be tested end-to-end with this function mocked,
    with no real API key or network access required.
  - All API-failure handling (retries, timeouts, error classification)
    lives in one place.
  - Swapping model providers only ever touches this one file --
    prompt.py's TOOL_SCHEMA, investigate.py's orchestration, and
    everything downstream of it are provider-agnostic by design and did
    not need to change for this swap.

Uses forced tool-use (see prompt.py) so the return value is already a
parsed dict matching the tool's input_schema -- no text/JSON parsing here.

Provider note: this file previously used Mistral's La Plateforme API.
That was swapped for Groq because Mistral's free "Experiment" tier rate
limit (~2 requests/minute per Mistral's own docs, see
https://help.mistral.ai/en/articles/225174) is too strict for this app's
usage pattern -- seeding/uploading a dataset calls the agent once per
detected candidate incident, several times in a row, and repeatedly hit
429s even with generous per-call retry/backoff (see git history of this
file for that entire debugging saga). Groq's free tier allows 30
requests/minute -- 15x the headroom -- with no credit card required, and
uses the same OpenAI-style function-calling request/response shape as
Mistral did, so this migration only touches this one file plus the
credential name in app/core/config.py.

TOOL_SCHEMA in prompt.py is still defined in Anthropic's {name,
description, input_schema} shape (kept that way since it's a natural,
provider-neutral way to describe a tool, and downstream code doesn't
care) -- _to_groq_tool() below is the only place that shape gets adapted
into the OpenAI-style {type, function: {name, description, parameters}}
wrapper Groq (and Mistral before it) expects.
"""

from __future__ import annotations

import json
import logging
import time

from app.agent.errors import AgentAPIError, MalformedOutputError
from app.agent.prompt import TOOL_SCHEMA
from app.core.config import settings

logger = logging.getLogger(__name__)

MAX_RETRIES = 4
RETRY_BACKOFF_SECONDS = 2.0
# Groq's free tier (30 requests/minute) is far more forgiving than
# Mistral's free tier was, but a rate-limit-specific backoff is still
# kept distinct from the generic 5xx/connection-error backoff -- a 429
# means "you must wait", which is a different situation from "retry
# might just work this time".
RATE_LIMIT_BACKOFF_SECONDS = 3.0
# Hard ceiling on any single backoff wait -- without this, growing
# backoff (RATE_LIMIT_BACKOFF_SECONDS * attempt) compounded with
# MAX_RETRIES could silently add up to several minutes of unlogged
# sleeping per call (worse across a whole batch of incidents at
# startup), which is indistinguishable from a true hang to anyone
# watching the terminal. See the logging in the retry loop below for the
# fix to the "indistinguishable" half of that problem; this constant
# fixes the "several minutes" half.
MAX_BACKOFF_SECONDS = 15.0
MAX_TOKENS = 1500
# The Groq SDK (unlike the Mistral SDK previously used here) DOES apply
# a sane default timeout on its own -- this is set explicitly anyway so
# the behavior doesn't silently change if that default ever changes
# upstream. Without a finite timeout, a stalled connection hangs the
# call indefinitely rather than raising an error the retry loop below
# can act on. Since app startup calls this synchronously in a loop (see
# app/api/pipeline.seed_from_synthetic_dataset), an unbounded hang here
# means "Waiting for application startup" never resolves, with no
# error, no traceback, nothing -- indistinguishable from a true
# deadlock. 45s is generous for a single chat completion; a real, fast
# response typically takes well under 5s on Groq's hardware.
REQUEST_TIMEOUT_SECONDS = 45.0

TOOL_NAME = TOOL_SCHEMA["name"]


def _to_groq_tool(schema: dict) -> dict:
    """Adapt an Anthropic-shaped tool schema ({name, description,
    input_schema}) into the OpenAI-style function-calling shape Groq
    expects ({type: "function", function: {name, description,
    parameters}}). Same JSON Schema body either way -- just a different
    wrapper."""
    return {
        "type": "function",
        "function": {
            "name": schema["name"],
            "description": schema["description"],
            "parameters": schema["input_schema"],
        },
    }


def call_agent_model(
    system_prompt: str,
    user_prompt: str,
    model: str | None = None,
) -> dict:
    """Call Groq with forced tool use and return the parsed tool call
    arguments.

    Raises
    ------
    AgentAPIError : the API call failed (network, auth, rate limit, or
        non-2xx after retries) -- a transport/service-level problem.
    MalformedOutputError : the API call succeeded but the model didn't
        call the tool, called a different tool, or returned arguments
        that weren't valid JSON -- a model-behavior problem distinct
        from a transport failure.
    """
    try:
        from groq import (
            APIConnectionError,
            APIStatusError,
            APITimeoutError,
            Groq,
            RateLimitError,
        )
    except ImportError as e:  # pragma: no cover - dependency always installed per requirements.txt
        raise AgentAPIError(f"groq package not available: {e}") from e

    if not settings.groq_api_key:
        raise AgentAPIError("GROQ_API_KEY is not configured")

    client = Groq(api_key=settings.groq_api_key, timeout=REQUEST_TIMEOUT_SECONDS, max_retries=0)
    model_name = model or settings.groq_agent_model
    tool = _to_groq_tool(TOOL_SCHEMA)

    last_error: Exception | None = None
    last_was_rate_limit = False
    for attempt in range(MAX_RETRIES + 1):
        try:
            response = client.chat.completions.create(
                model=model_name,
                max_completion_tokens=MAX_TOKENS,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                tools=[tool],
                tool_choice={"type": "function", "function": {"name": TOOL_NAME}},
            )
            return _extract_tool_input(response)

        except MalformedOutputError:
            raise  # not retryable -- the model responded, just not correctly

        except RateLimitError as e:
            last_error = e
            last_was_rate_limit = True

        except APIStatusError as e:
            if e.status_code and 500 <= e.status_code < 600:
                last_error = e
                last_was_rate_limit = False
            else:
                # 4xx other than 429 -- bad request, auth, etc. Fail fast.
                raise AgentAPIError(f"Groq API error ({e.status_code}): {e}") from e

        except (APITimeoutError, APIConnectionError) as e:
            # Timeout or connection-level failure (no HTTP response at
            # all) -- treat as retryable, same as a 5xx.
            last_error = e
            last_was_rate_limit = False

        except Exception as e:
            # Anything else from the SDK/transport layer -- treated as
            # retryable, mirroring how a bare connection error is
            # handled above.
            last_error = e
            last_was_rate_limit = False

        if attempt < MAX_RETRIES:
            if last_was_rate_limit:
                retry_after = _retry_after_seconds(last_error)
                wait = retry_after if retry_after is not None else RATE_LIMIT_BACKOFF_SECONDS * (attempt + 1)
            else:
                wait = RETRY_BACKOFF_SECONDS * (attempt + 1)
            wait = min(wait, MAX_BACKOFF_SECONDS)
            logger.warning(
                "Groq API call failed (attempt %d/%d, %s): %s -- retrying in %.1fs",
                attempt + 1, MAX_RETRIES + 1,
                "rate limited" if last_was_rate_limit else "transient error",
                last_error, wait,
            )
            time.sleep(wait)

    logger.error(
        "Groq API call failed permanently after %d attempt(s): %s",
        MAX_RETRIES + 1, last_error,
    )
    raise AgentAPIError(
        f"Groq API call failed after {MAX_RETRIES + 1} attempt(s): {last_error}"
    ) from last_error


def _retry_after_seconds(error: Exception) -> float | None:
    """If the API error carries a Retry-After header, use that instead
    of our own guessed backoff -- the API is telling us exactly how
    long to wait, which is more reliable than a fixed schedule."""
    response = getattr(error, "response", None)
    headers = getattr(response, "headers", None) if response is not None else None
    if not headers:
        return None
    value = headers.get("retry-after") or headers.get("Retry-After")
    if value is None:
        return None
    try:
        return max(float(value), 0.0)
    except (TypeError, ValueError):
        return None


def _extract_tool_input(response) -> dict:
    choices = getattr(response, "choices", None) or []
    if not choices:
        raise MalformedOutputError("Model response contained no choices")

    message = choices[0].message
    tool_calls = getattr(message, "tool_calls", None) or []

    for call in tool_calls:
        function = call.function
        if function.name != TOOL_NAME:
            continue
        arguments = function.arguments
        if isinstance(arguments, dict):
            return arguments
        try:
            parsed = json.loads(arguments)
        except (TypeError, ValueError) as e:
            raise MalformedOutputError(
                f"Model's tool call arguments were not valid JSON: {e}"
            ) from e
        if not isinstance(parsed, dict):
            raise MalformedOutputError(
                f"Model's tool call arguments parsed to {type(parsed).__name__}, expected an object"
            )
        return parsed

    raise MalformedOutputError(
        f"Model response did not include a {TOOL_NAME} tool call"
    )
