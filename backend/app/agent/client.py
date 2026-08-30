"""
Claude API client wrapper for the investigation agent.

This is the ONLY place in the agent module that talks to the network.
Keeping it isolated means:
  - investigate.py can be tested end-to-end with this function mocked,
    with no real API key or network access required.
  - All API-failure handling (retries, timeouts, error classification)
    lives in one place.

Uses forced tool-use (see prompt.py) so the return value is already a
parsed dict matching the tool's input_schema -- no text/JSON parsing here.
"""

from __future__ import annotations

import time

from app.agent.errors import AgentAPIError, MalformedOutputError
from app.agent.prompt import TOOL_SCHEMA
from app.core.config import settings

MAX_RETRIES = 2
RETRY_BACKOFF_SECONDS = 1.5
MAX_TOKENS = 1500


def call_agent_model(
    system_prompt: str,
    user_prompt: str,
    model: str | None = None,
) -> dict:
    """Call Claude with forced tool use and return the parsed tool input.

    Raises
    ------
    AgentAPIError : the API call failed (network, auth, rate limit, or
        non-2xx after retries) -- a transport/service-level problem.
    MalformedOutputError : the API call succeeded but the model didn't
        call the tool, or called a different tool -- a model-behavior
        problem distinct from a transport failure.
    """
    try:
        import anthropic
    except ImportError as e:  # pragma: no cover - dependency always installed per requirements.txt
        raise AgentAPIError(f"anthropic package not available: {e}") from e

    if not settings.anthropic_api_key:
        raise AgentAPIError("ANTHROPIC_API_KEY is not configured")

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    model_name = model or settings.anthropic_model

    last_error: Exception | None = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            response = client.messages.create(
                model=model_name,
                max_tokens=MAX_TOKENS,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
                tools=[TOOL_SCHEMA],
                tool_choice={"type": "tool", "name": TOOL_SCHEMA["name"]},
            )
            return _extract_tool_input(response)

        except MalformedOutputError:
            raise  # not retryable -- the model responded, just not correctly

        except anthropic.RateLimitError as e:
            last_error = e
        except anthropic.APIConnectionError as e:
            last_error = e
        except anthropic.APIStatusError as e:
            if e.status_code and 500 <= e.status_code < 600:
                last_error = e
            else:
                raise AgentAPIError(f"Claude API error ({e.status_code}): {e}") from e
        except anthropic.APIError as e:
            last_error = e

        if attempt < MAX_RETRIES:
            time.sleep(RETRY_BACKOFF_SECONDS * (attempt + 1))

    raise AgentAPIError(
        f"Claude API call failed after {MAX_RETRIES + 1} attempt(s): {last_error}"
    ) from last_error


def _extract_tool_input(response) -> dict:
    for block in response.content:
        if getattr(block, "type", None) == "tool_use" and block.name == TOOL_SCHEMA["name"]:
            return dict(block.input)
    raise MalformedOutputError(
        "Model response did not include a submit_investigation tool call"
    )
