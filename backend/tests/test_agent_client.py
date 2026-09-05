"""
Tests for app/agent/client.py directly (below the investigate_incident
orchestration layer). Mocks the groq.Groq client itself so no network
access or API key is required, while still exercising the real
retry/error-classification/tool-extraction logic in client.py.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httpx
import pytest

from app.agent.client import MAX_RETRIES, TOOL_NAME, _extract_tool_input, call_agent_model
from app.agent.errors import AgentAPIError, MalformedOutputError


def make_tool_call_response(arguments, tool_name: str = TOOL_NAME):
    """Build a fake Groq ChatCompletion-shaped object. Uses SimpleNamespace
    rather than the real pydantic models so tests don't depend on
    constructing every required field of Groq's generated model classes
    -- client.py only ever reads .choices[0].message.tool_calls, so
    that's all we need to fake.
    """
    function = SimpleNamespace(name=tool_name, arguments=arguments)
    tool_call = SimpleNamespace(function=function)
    message = SimpleNamespace(tool_calls=[tool_call])
    choice = SimpleNamespace(message=message)
    return SimpleNamespace(choices=[choice])


def make_no_tool_call_response():
    message = SimpleNamespace(tool_calls=[])
    choice = SimpleNamespace(message=message)
    return SimpleNamespace(choices=[choice])


def make_no_choices_response():
    return SimpleNamespace(choices=[])


# --------------------------------------------------------------------------
# _extract_tool_input
# --------------------------------------------------------------------------

def test_extract_tool_input_returns_dict_when_arguments_already_a_dict():
    response = make_tool_call_response({"diagnosis": "d", "confidence": 0.5})
    result = _extract_tool_input(response)
    assert result == {"diagnosis": "d", "confidence": 0.5}


def test_extract_tool_input_parses_arguments_given_as_json_string():
    response = make_tool_call_response('{"diagnosis": "d", "confidence": 0.5}')
    result = _extract_tool_input(response)
    assert result == {"diagnosis": "d", "confidence": 0.5}


def test_extract_tool_input_raises_on_invalid_json_string_arguments():
    response = make_tool_call_response("not valid json{{{")
    with pytest.raises(MalformedOutputError):
        _extract_tool_input(response)


def test_extract_tool_input_raises_on_json_array_arguments():
    """A syntactically valid JSON string that isn't an object should
    still be rejected -- the schema requires an object of named fields."""
    response = make_tool_call_response("[1, 2, 3]")
    with pytest.raises(MalformedOutputError):
        _extract_tool_input(response)


def test_extract_tool_input_raises_when_no_tool_call_present():
    response = make_no_tool_call_response()
    with pytest.raises(MalformedOutputError):
        _extract_tool_input(response)


def test_extract_tool_input_raises_when_no_choices_present():
    response = make_no_choices_response()
    with pytest.raises(MalformedOutputError):
        _extract_tool_input(response)


def test_extract_tool_input_raises_when_different_tool_called():
    response = make_tool_call_response({"foo": "bar"}, tool_name="some_other_tool")
    with pytest.raises(MalformedOutputError):
        _extract_tool_input(response)


# --------------------------------------------------------------------------
# call_agent_model — success and configuration errors
# --------------------------------------------------------------------------

def test_call_agent_model_raises_when_no_api_key(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "groq_api_key", "")
    with pytest.raises(AgentAPIError, match="not configured"):
        call_agent_model("system", "user")


def test_call_agent_model_returns_parsed_tool_input_on_success(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "groq_api_key", "test-key")

    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = make_tool_call_response(
        {"diagnosis": "ok", "confidence": 0.7}
    )

    with patch("groq.Groq", return_value=mock_client):
        result = call_agent_model("system prompt", "user prompt")

    assert result == {"diagnosis": "ok", "confidence": 0.7}
    mock_client.chat.completions.create.assert_called_once()
    call_kwargs = mock_client.chat.completions.create.call_args.kwargs
    assert call_kwargs["messages"][0] == {"role": "system", "content": "system prompt"}
    assert call_kwargs["messages"][1] == {"role": "user", "content": "user prompt"}
    assert call_kwargs["tool_choice"] == {"type": "function", "function": {"name": TOOL_NAME}}


def test_call_agent_model_sends_tool_in_openai_function_shape(monkeypatch):
    """TOOL_SCHEMA (app/agent/prompt.py) is defined in Anthropic's
    {name, description, input_schema} shape -- confirm client.py adapts
    it into the OpenAI-style {type: function, function: {name,
    description, parameters}} wrapper Groq expects, rather than sending
    it as-is."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "groq_api_key", "test-key")

    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = make_tool_call_response({"diagnosis": "ok"})

    with patch("groq.Groq", return_value=mock_client):
        call_agent_model("system", "user")

    sent_tools = mock_client.chat.completions.create.call_args.kwargs["tools"]
    assert len(sent_tools) == 1
    assert sent_tools[0]["type"] == "function"
    assert sent_tools[0]["function"]["name"] == TOOL_NAME
    assert "parameters" in sent_tools[0]["function"]


def test_call_agent_model_uses_configured_model_name(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "groq_api_key", "test-key")
    monkeypatch.setattr(settings, "groq_agent_model", "qwen/qwen3-32b")

    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = make_tool_call_response({"diagnosis": "ok"})

    with patch("groq.Groq", return_value=mock_client):
        call_agent_model("system", "user")

    call_kwargs = mock_client.chat.completions.create.call_args.kwargs
    assert call_kwargs["model"] == "qwen/qwen3-32b"
    # reasoning_effort is only added for openai/gpt-oss-* models (see
    # client.py) -- sending it to other model families is rejected by
    # Groq with a 400, so it must be omitted here.
    assert "reasoning_effort" not in call_kwargs


def test_call_agent_model_sets_low_reasoning_effort_for_gpt_oss_models(monkeypatch):
    """openai/gpt-oss-120b (the default model) is a reasoning model --
    reasoning_effort="low" is sent to keep latency and reasoning-token
    usage down for what is a single bounded diagnosis + tool call, not
    deep multi-step reasoning."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "groq_api_key", "test-key")
    monkeypatch.setattr(settings, "groq_agent_model", "openai/gpt-oss-120b")

    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = make_tool_call_response({"diagnosis": "ok"})

    with patch("groq.Groq", return_value=mock_client):
        call_agent_model("system", "user")

    call_kwargs = mock_client.chat.completions.create.call_args.kwargs
    assert call_kwargs["reasoning_effort"] == "low"


# --------------------------------------------------------------------------
# call_agent_model — API failure classification + retry behavior
# --------------------------------------------------------------------------

def _fake_httpx_response(status_code: int, headers: dict | None = None):
    request = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
    return httpx.Response(status_code, request=request, headers=headers or {})


def _rate_limit_error(headers: dict | None = None):
    from groq import RateLimitError

    response = _fake_httpx_response(429, headers=headers)
    return RateLimitError("rate limited", response=response, body=None)


def _status_error(status_code: int):
    from groq import APIStatusError

    response = _fake_httpx_response(status_code)
    return APIStatusError(f"error {status_code}", response=response, body=None)


def test_call_agent_model_retries_on_rate_limit_then_succeeds(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "groq_api_key", "test-key")
    monkeypatch.setattr("app.agent.client.RETRY_BACKOFF_SECONDS", 0)
    monkeypatch.setattr("app.agent.client.RATE_LIMIT_BACKOFF_SECONDS", 0)

    success_response = make_tool_call_response({"diagnosis": "recovered", "confidence": 0.6})

    mock_client = MagicMock()
    mock_client.chat.completions.create.side_effect = [_rate_limit_error(), success_response]

    with patch("groq.Groq", return_value=mock_client):
        result = call_agent_model("system", "user")

    assert result == {"diagnosis": "recovered", "confidence": 0.6}
    assert mock_client.chat.completions.create.call_count == 2


def test_call_agent_model_uses_rate_limit_backoff_not_generic_backoff(monkeypatch):
    """A 429 must wait on RATE_LIMIT_BACKOFF_SECONDS, not the shorter
    generic RETRY_BACKOFF_SECONDS used for 5xx/connection errors --
    regression test carried over from the Mistral-era rate-limit fix
    (429s need their own, longer schedule than generic transient
    errors)."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "groq_api_key", "test-key")
    monkeypatch.setattr("app.agent.client.RETRY_BACKOFF_SECONDS", 0)
    monkeypatch.setattr("app.agent.client.RATE_LIMIT_BACKOFF_SECONDS", 5)

    success_response = make_tool_call_response({"diagnosis": "recovered"})

    mock_client = MagicMock()
    mock_client.chat.completions.create.side_effect = [_rate_limit_error(), success_response]

    with patch("groq.Groq", return_value=mock_client), \
         patch("app.agent.client.time.sleep") as mock_sleep:
        call_agent_model("system", "user")

    mock_sleep.assert_called_once_with(5)


def test_call_agent_model_honors_retry_after_header_on_rate_limit(monkeypatch):
    """When Groq's 429 response includes a Retry-After header, that
    value should be used instead of our own guessed backoff schedule."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "groq_api_key", "test-key")
    monkeypatch.setattr("app.agent.client.RETRY_BACKOFF_SECONDS", 0)
    monkeypatch.setattr("app.agent.client.RATE_LIMIT_BACKOFF_SECONDS", 100)

    success_response = make_tool_call_response({"diagnosis": "recovered"})

    mock_client = MagicMock()
    mock_client.chat.completions.create.side_effect = [
        _rate_limit_error(headers={"Retry-After": "3"}),
        success_response,
    ]

    with patch("groq.Groq", return_value=mock_client), \
         patch("app.agent.client.time.sleep") as mock_sleep:
        call_agent_model("system", "user")

    mock_sleep.assert_called_once_with(3.0)


def test_call_agent_model_retries_on_server_error(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "groq_api_key", "test-key")
    monkeypatch.setattr("app.agent.client.RETRY_BACKOFF_SECONDS", 0)

    server_error = _status_error(503)
    success_response = make_tool_call_response({"diagnosis": "recovered"})

    mock_client = MagicMock()
    mock_client.chat.completions.create.side_effect = [server_error, success_response]

    with patch("groq.Groq", return_value=mock_client):
        result = call_agent_model("system", "user")

    assert result == {"diagnosis": "recovered"}
    assert mock_client.chat.completions.create.call_count == 2


def test_call_agent_model_gives_up_after_max_retries(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "groq_api_key", "test-key")
    monkeypatch.setattr("app.agent.client.RETRY_BACKOFF_SECONDS", 0)

    server_error = _status_error(500)

    mock_client = MagicMock()
    mock_client.chat.completions.create.side_effect = server_error

    with patch("groq.Groq", return_value=mock_client):
        with pytest.raises(AgentAPIError, match="failed after"):
            call_agent_model("system", "user")

    assert mock_client.chat.completions.create.call_count == MAX_RETRIES + 1


def test_call_agent_model_does_not_retry_on_client_error(monkeypatch):
    """A 4xx (e.g. bad request / auth) other than 429 should fail fast,
    not burn through retries."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "groq_api_key", "test-key")
    monkeypatch.setattr("app.agent.client.RETRY_BACKOFF_SECONDS", 0)

    auth_error = _status_error(401)

    mock_client = MagicMock()
    mock_client.chat.completions.create.side_effect = auth_error

    with patch("groq.Groq", return_value=mock_client):
        with pytest.raises(AgentAPIError):
            call_agent_model("system", "user")

    assert mock_client.chat.completions.create.call_count == 1


def test_call_agent_model_propagates_malformed_output_without_retry(monkeypatch):
    """If the model responds but doesn't call the tool, that's not a
    transport failure -- should surface immediately as MalformedOutputError,
    not be retried as though it were an API problem."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "groq_api_key", "test-key")
    monkeypatch.setattr("app.agent.client.RETRY_BACKOFF_SECONDS", 0)

    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = make_no_tool_call_response()

    with patch("groq.Groq", return_value=mock_client):
        with pytest.raises(MalformedOutputError):
            call_agent_model("system", "user")

    assert mock_client.chat.completions.create.call_count == 1


def test_call_agent_model_constructs_client_with_finite_timeout(monkeypatch):
    """The Groq client is constructed with an explicit finite timeout
    so a stalled connection fails after REQUEST_TIMEOUT_SECONDS instead
    of hanging indefinitely -- see client.py's module docstring for why
    this mattered so much with the previous (Mistral) provider."""
    from app.agent.client import REQUEST_TIMEOUT_SECONDS
    from app.core.config import settings

    monkeypatch.setattr(settings, "groq_api_key", "test-key")

    success_response = make_tool_call_response({"diagnosis": "ok"})
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = success_response

    with patch("groq.Groq", return_value=mock_client) as mock_ctor:
        call_agent_model("system", "user")

    assert mock_ctor.call_count == 1
    _, kwargs = mock_ctor.call_args
    assert kwargs.get("timeout") == REQUEST_TIMEOUT_SECONDS
    assert REQUEST_TIMEOUT_SECONDS is not None and REQUEST_TIMEOUT_SECONDS > 0


def test_max_tokens_fits_within_groq_free_tier_tpm_budget():
    """Regression test: Groq's free tier caps openai/gpt-oss-120b/-20b at
    a shared 8,000 tokens-PER-MINUTE budget across both input and
    output combined. This app's prompts already run ~3,500 tokens on
    their own (incident + retrieved evidence + allowed-actions list,
    see app/agent/prompt.py). An earlier version of this constant
    (8,000) reserved the ENTIRE per-minute budget as output alone --
    meaning even one call could nearly exhaust the organization's whole
    TPM allowance, so a second call in the same 60s window reliably
    429'd on tokens (confirmed live: 'Limit 8000, Used ~7000, Requested
    ~5000'). This must leave real headroom below 8,000 once combined
    with a realistic prompt size, not consume the whole budget alone."""
    from app.agent.client import MAX_TOKENS

    assert MAX_TOKENS <= 4000


def test_call_agent_model_disables_sdk_internal_retries(monkeypatch):
    """The Groq SDK has its own built-in retry mechanism (max_retries,
    default 2) -- this must be disabled (set to 0) so our own retry loop
    is the only one running, and its behavior stays predictable."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "groq_api_key", "test-key")

    success_response = make_tool_call_response({"diagnosis": "ok"})
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = success_response

    with patch("groq.Groq", return_value=mock_client) as mock_ctor:
        call_agent_model("system", "user")

    assert mock_ctor.call_args.kwargs.get("max_retries") == 0


def test_call_agent_model_retries_on_timeout_exception(monkeypatch):
    """A Groq APITimeoutError must be treated as retryable, same as any
    other bare transport-level exception."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "groq_api_key", "test-key")
    monkeypatch.setattr("app.agent.client.RETRY_BACKOFF_SECONDS", 0)

    from groq import APITimeoutError

    request = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
    timeout_error = APITimeoutError(request=request)
    success_response = make_tool_call_response({"diagnosis": "ok"})

    mock_client = MagicMock()
    mock_client.chat.completions.create.side_effect = [timeout_error, success_response]

    with patch("groq.Groq", return_value=mock_client):
        result = call_agent_model("system", "user")

    assert result == {"diagnosis": "ok"}
    assert mock_client.chat.completions.create.call_count == 2


def test_call_agent_model_retries_on_connection_error(monkeypatch):
    """A Groq APIConnectionError should still be treated as retryable,
    not crash the retry loop."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "groq_api_key", "test-key")
    monkeypatch.setattr("app.agent.client.RETRY_BACKOFF_SECONDS", 0)

    from groq import APIConnectionError

    request = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
    connection_error = APIConnectionError(request=request)
    success_response = make_tool_call_response({"diagnosis": "ok"})

    mock_client = MagicMock()
    mock_client.chat.completions.create.side_effect = [connection_error, success_response]

    with patch("groq.Groq", return_value=mock_client):
        result = call_agent_model("system", "user")

    assert result == {"diagnosis": "ok"}
    assert mock_client.chat.completions.create.call_count == 2


def test_call_agent_model_retries_on_bare_unexpected_exception(monkeypatch):
    """Any other bare exception not covered by Groq's typed error
    classes should still be treated as retryable, not crash the retry
    loop."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "groq_api_key", "test-key")
    monkeypatch.setattr("app.agent.client.RETRY_BACKOFF_SECONDS", 0)

    connection_error = ConnectionError("connection reset")
    success_response = make_tool_call_response({"diagnosis": "ok"})

    mock_client = MagicMock()
    mock_client.chat.completions.create.side_effect = [connection_error, success_response]

    with patch("groq.Groq", return_value=mock_client):
        result = call_agent_model("system", "user")

    assert result == {"diagnosis": "ok"}
    assert mock_client.chat.completions.create.call_count == 2
