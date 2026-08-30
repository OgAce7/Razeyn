"""
Tests for app/agent/client.py directly (below the investigate_incident
orchestration layer). Mocks the anthropic.Anthropic client itself so no
network access or API key is required, while still exercising the real
retry/error-classification/tool-extraction logic in client.py.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import anthropic
import httpx
import pytest

from app.agent.client import MAX_RETRIES, _extract_tool_input, call_agent_model
from app.agent.errors import AgentAPIError, MalformedOutputError


def make_tool_use_response(tool_input: dict, tool_name: str = "submit_investigation"):
    block = SimpleNamespace(type="tool_use", name=tool_name, input=tool_input)
    return SimpleNamespace(content=[block])


def make_text_only_response(text: str = "I cannot comply with tool use right now."):
    block = SimpleNamespace(type="text", text=text)
    return SimpleNamespace(content=[block])


# --------------------------------------------------------------------------
# _extract_tool_input
# --------------------------------------------------------------------------

def test_extract_tool_input_returns_dict_on_matching_tool_call():
    response = make_tool_use_response({"diagnosis": "d", "confidence": 0.5})
    result = _extract_tool_input(response)
    assert result == {"diagnosis": "d", "confidence": 0.5}


def test_extract_tool_input_raises_when_no_tool_call_present():
    response = make_text_only_response()
    with pytest.raises(MalformedOutputError):
        _extract_tool_input(response)


def test_extract_tool_input_raises_when_different_tool_called():
    response = make_tool_use_response({"foo": "bar"}, tool_name="some_other_tool")
    with pytest.raises(MalformedOutputError):
        _extract_tool_input(response)


# --------------------------------------------------------------------------
# call_agent_model — success and configuration errors
# --------------------------------------------------------------------------

def test_call_agent_model_raises_when_no_api_key(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "anthropic_api_key", "")
    with pytest.raises(AgentAPIError, match="not configured"):
        call_agent_model("system", "user")


def test_call_agent_model_returns_parsed_tool_input_on_success(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "anthropic_api_key", "sk-test-key")

    mock_client = MagicMock()
    mock_client.messages.create.return_value = make_tool_use_response(
        {"diagnosis": "ok", "confidence": 0.7}
    )

    with patch("anthropic.Anthropic", return_value=mock_client):
        result = call_agent_model("system prompt", "user prompt")

    assert result == {"diagnosis": "ok", "confidence": 0.7}
    mock_client.messages.create.assert_called_once()
    call_kwargs = mock_client.messages.create.call_args.kwargs
    assert call_kwargs["system"] == "system prompt"
    assert call_kwargs["tool_choice"] == {"type": "tool", "name": "submit_investigation"}


# --------------------------------------------------------------------------
# call_agent_model — API failure classification + retry behavior
# --------------------------------------------------------------------------

def _fake_httpx_request():
    return httpx.Request("POST", "https://api.anthropic.com/v1/messages")


def _fake_httpx_response(status_code: int):
    return httpx.Response(status_code, request=_fake_httpx_request())


def test_call_agent_model_retries_on_rate_limit_then_succeeds(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "anthropic_api_key", "sk-test-key")
    monkeypatch.setattr("app.agent.client.RETRY_BACKOFF_SECONDS", 0)  # don't actually sleep in tests

    rate_limit_error = anthropic.RateLimitError(
        "rate limited", response=_fake_httpx_response(429), body=None
    )
    success_response = make_tool_use_response({"diagnosis": "recovered", "confidence": 0.6})

    mock_client = MagicMock()
    mock_client.messages.create.side_effect = [rate_limit_error, success_response]

    with patch("anthropic.Anthropic", return_value=mock_client):
        result = call_agent_model("system", "user")

    assert result == {"diagnosis": "recovered", "confidence": 0.6}
    assert mock_client.messages.create.call_count == 2


def test_call_agent_model_gives_up_after_max_retries(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "anthropic_api_key", "sk-test-key")
    monkeypatch.setattr("app.agent.client.RETRY_BACKOFF_SECONDS", 0)

    connection_error = anthropic.APIConnectionError(request=_fake_httpx_request())

    mock_client = MagicMock()
    mock_client.messages.create.side_effect = connection_error

    with patch("anthropic.Anthropic", return_value=mock_client):
        with pytest.raises(AgentAPIError, match="failed after"):
            call_agent_model("system", "user")

    assert mock_client.messages.create.call_count == MAX_RETRIES + 1


def test_call_agent_model_does_not_retry_on_client_error(monkeypatch):
    """A 4xx (e.g. bad request / auth) should fail fast, not burn through retries."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "anthropic_api_key", "sk-test-key")
    monkeypatch.setattr("app.agent.client.RETRY_BACKOFF_SECONDS", 0)

    auth_error = anthropic.AuthenticationError(
        "invalid api key", response=_fake_httpx_response(401), body=None
    )

    mock_client = MagicMock()
    mock_client.messages.create.side_effect = auth_error

    with patch("anthropic.Anthropic", return_value=mock_client):
        with pytest.raises(AgentAPIError):
            call_agent_model("system", "user")

    assert mock_client.messages.create.call_count == 1  # no retries


def test_call_agent_model_propagates_malformed_output_without_retry(monkeypatch):
    """If the model responds but doesn't call the tool, that's not a
    transport failure -- should surface immediately as MalformedOutputError,
    not be retried as though it were an API problem."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "anthropic_api_key", "sk-test-key")
    monkeypatch.setattr("app.agent.client.RETRY_BACKOFF_SECONDS", 0)

    mock_client = MagicMock()
    mock_client.messages.create.return_value = make_text_only_response()

    with patch("anthropic.Anthropic", return_value=mock_client):
        with pytest.raises(MalformedOutputError):
            call_agent_model("system", "user")

    assert mock_client.messages.create.call_count == 1  # no retries
