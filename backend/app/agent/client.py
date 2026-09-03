"""
Mistral API client wrapper for the investigation agent.

This is the ONLY place in the agent module that talks to the network.
Keeping it isolated means:
  - investigate.py can be tested end-to-end with this function mocked,
    with no real API key or network access required.
  - All API-failure handling (retries, timeouts, error classification)
    lives in one place.
  - Swapping model providers (this file was originally written against
    Anthropic's API) only ever touches this one file -- prompt.py's
    TOOL_SCHEMA, investigate.py's orchestration, and everything
    downstream of it are provider-agnostic by design and did not need to
    change for this swap.

Uses forced tool-use (see prompt.py) so the return value is already a
parsed dict matching the tool's input_schema -- no text/JSON parsing here.

Provider note: Mistral's chat completions API uses OpenAI-style function
calling rather than Anthropic's tool_use content blocks, so the request/
response SHAPES differ from what an Anthropic-based version of this file
would use, even though the RETRY/ERROR-CLASSIFICATION/EXTRACTION
responsibilities are identical. TOOL_SCHEMA in prompt.py is still defined
in Anthropic's {name, description, input_schema} shape (kept that way
since it's a natural, provider-neutral way to describe a tool, and
downstream code doesn't care) -- _to_mistral_tool() below is the only
place that shape gets adapted into Mistral's {type, function: {name,
description, parameters}} wrapper.
"""

from __future__ import annotations

import json
import time

from app.agent.errors import AgentAPIError, MalformedOutputError
from app.agent.prompt import TOOL_SCHEMA
from app.core.config import settings

MAX_RETRIES = 2
RETRY_BACKOFF_SECONDS = 1.5
MAX_TOKENS = 1500

TOOL_NAME = TOOL_SCHEMA["name"]


def _to_mistral_tool(schema: dict) -> dict:
    """Adapt an Anthropic-shaped tool schema ({name, description,
    input_schema}) into Mistral's OpenAI-style function-calling shape
    ({type: "function", function: {name, description, parameters}}).
    Same JSON Schema body either way -- just a different wrapper."""
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
    """Call Mistral with forced tool use and return the parsed tool call
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
        from mistralai.client import Mistral
        from mistralai.client.errors import SDKError
        from mistralai.client.models import ToolChoice, FunctionName
    except ImportError as e:  # pragma: no cover - dependency always installed per requirements.txt
        raise AgentAPIError(f"mistralai package not available: {e}") from e

    if not settings.mistral_api_key:
        raise AgentAPIError("MISTRAL_API_KEY is not configured")

    client = Mistral(api_key=settings.mistral_api_key)
    model_name = model or settings.mistral_agent_model
    tool = _to_mistral_tool(TOOL_SCHEMA)

    last_error: Exception | None = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            response = client.chat.complete(
                model=model_name,
                max_tokens=MAX_TOKENS,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                tools=[tool],
                tool_choice=ToolChoice(function=FunctionName(name=TOOL_NAME)),
            )
            return _extract_tool_input(response)

        except MalformedOutputError:
            raise  # not retryable -- the model responded, just not correctly

        except SDKError as e:
            status_code = getattr(e.raw_response, "status_code", None) if e.raw_response else None
            if status_code == 429 or (status_code and 500 <= status_code < 600):
                last_error = e
            elif status_code is not None:
                # 4xx other than 429 -- bad request, auth, etc. Fail fast.
                raise AgentAPIError(f"Mistral API error ({status_code}): {e}") from e
            else:
                # No HTTP response at all (connection-level failure) --
                # treat as retryable, same as a 5xx.
                last_error = e

        except Exception as e:
            # Anything else from the SDK/transport layer (e.g. httpx
            # connection errors that aren't wrapped in SDKError) --
            # treated as retryable, mirroring the prior Anthropic
            # version's handling of anthropic.APIConnectionError.
            last_error = e

        if attempt < MAX_RETRIES:
            time.sleep(RETRY_BACKOFF_SECONDS * (attempt + 1))

    raise AgentAPIError(
        f"Mistral API call failed after {MAX_RETRIES + 1} attempt(s): {last_error}"
    ) from last_error


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
