"""
Tests for app/agent/client.py directly (below the investigate_incident
orchestration layer). Mocks the mistralai.client.Mistral client itself so
no network access or API key is required, while still exercising the
real retry/error-classification/tool-extraction logic in client.py.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httpx
import pytest

from app.agent.client import MAX_RETRIES, TOOL_NAME, _extract_tool_input, call_agent_model
from app.agent.errors import AgentAPIError, MalformedOutputError


def make_tool_call_response(arguments, tool_name: str = TOOL_NAME):
    """Build a fake Mistral ChatCompletionResponse-shaped object. Uses
    SimpleNamespace rather than the real pydantic models so tests don't
    depend on constructing every required field of Mistral's generated
    model classes -- client.py only ever reads .choices[0].message.tool_calls,
    so that's all we need to fake.
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

    monkeypatch.setattr(settings, "mistral_api_key", "")
    with pytest.raises(AgentAPIError, match="not configured"):
        call_agent_model("system", "user")


def test_call_agent_model_returns_parsed_tool_input_on_success(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "mistral_api_key", "test-key")

    mock_client = MagicMock()
    mock_client.chat.complete.return_value = make_tool_call_response(
        {"diagnosis": "ok", "confidence": 0.7}
    )

    with patch("mistralai.client.Mistral", return_value=mock_client):
        result = call_agent_model("system prompt", "user prompt")

    assert result == {"diagnosis": "ok", "confidence": 0.7}
    mock_client.chat.complete.assert_called_once()
    call_kwargs = mock_client.chat.complete.call_args.kwargs
    assert call_kwargs["messages"][0] == {"role": "system", "content": "system prompt"}
    assert call_kwargs["messages"][1] == {"role": "user", "content": "user prompt"}
    # Forced tool-use: named function, not just "any tool".
    assert call_kwargs["tool_choice"].function.name == TOOL_NAME


def test_call_agent_model_sends_tool_in_mistral_function_shape(monkeypatch):
    """TOOL_SCHEMA (app/agent/prompt.py) is defined in Anthropic's
    {name, description, input_schema} shape -- confirm client.py adapts
    it into Mistral's {type: function, function: {name, description,
    parameters}} wrapper rather than sending it as-is."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "mistral_api_key", "test-key")

    mock_client = MagicMock()
    mock_client.chat.complete.return_value = make_tool_call_response({"diagnosis": "ok"})

    with patch("mistralai.client.Mistral", return_value=mock_client):
        call_agent_model("system", "user")

    sent_tools = mock_client.chat.complete.call_args.kwargs["tools"]
    assert len(sent_tools) == 1
    assert sent_tools[0]["type"] == "function"
    assert sent_tools[0]["function"]["name"] == TOOL_NAME
    assert "parameters" in sent_tools[0]["function"]


def test_call_agent_model_uses_configured_model_name(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "mistral_api_key", "test-key")
    monkeypatch.setattr(settings, "mistral_agent_model", "mistral-medium-latest")

    mock_client = MagicMock()
    mock_client.chat.complete.return_value = make_tool_call_response({"diagnosis": "ok"})

    with patch("mistralai.client.Mistral", return_value=mock_client):
        call_agent_model("system", "user")

    assert mock_client.chat.complete.call_args.kwargs["model"] == "mistral-medium-latest"


# --------------------------------------------------------------------------
# call_agent_model — API failure classification + retry behavior
# --------------------------------------------------------------------------

def _fake_httpx_response(status_code: int):
    request = httpx.Request("POST", "https://api.mistral.ai/v1/chat/completions")
    return httpx.Response(status_code, request=request)


def _sdk_error(status_code: int):
    from mistralai.client.errors import SDKError

    return SDKError(f"error {status_code}", raw_response=_fake_httpx_response(status_code))


def test_call_agent_model_retries_on_rate_limit_then_succeeds(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "mistral_api_key", "test-key")
    monkeypatch.setattr("app.agent.client.RETRY_BACKOFF_SECONDS", 0)

    rate_limit_error = _sdk_error(429)
    success_response = make_tool_call_response({"diagnosis": "recovered", "confidence": 0.6})

    mock_client = MagicMock()
    mock_client.chat.complete.side_effect = [rate_limit_error, success_response]

    with patch("mistralai.client.Mistral", return_value=mock_client):
        result = call_agent_model("system", "user")

    assert result == {"diagnosis": "recovered", "confidence": 0.6}
    assert mock_client.chat.complete.call_count == 2


def test_call_agent_model_retries_on_server_error(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "mistral_api_key", "test-key")
    monkeypatch.setattr("app.agent.client.RETRY_BACKOFF_SECONDS", 0)

    server_error = _sdk_error(503)
    success_response = make_tool_call_response({"diagnosis": "recovered"})

    mock_client = MagicMock()
    mock_client.chat.complete.side_effect = [server_error, success_response]

    with patch("mistralai.client.Mistral", return_value=mock_client):
        result = call_agent_model("system", "user")

    assert result == {"diagnosis": "recovered"}
    assert mock_client.chat.complete.call_count == 2


def test_call_agent_model_gives_up_after_max_retries(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "mistral_api_key", "test-key")
    monkeypatch.setattr("app.agent.client.RETRY_BACKOFF_SECONDS", 0)

    server_error = _sdk_error(500)

    mock_client = MagicMock()
    mock_client.chat.complete.side_effect = server_error

    with patch("mistralai.client.Mistral", return_value=mock_client):
        with pytest.raises(AgentAPIError, match="failed after"):
            call_agent_model("system", "user")

    assert mock_client.chat.complete.call_count == MAX_RETRIES + 1


def test_call_agent_model_does_not_retry_on_client_error(monkeypatch):
    """A 4xx (e.g. bad request / auth) other than 429 should fail fast,
    not burn through retries."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "mistral_api_key", "test-key")
    monkeypatch.setattr("app.agent.client.RETRY_BACKOFF_SECONDS", 0)

    auth_error = _sdk_error(401)

    mock_client = MagicMock()
    mock_client.chat.complete.side_effect = auth_error

    with patch("mistralai.client.Mistral", return_value=mock_client):
        with pytest.raises(AgentAPIError):
            call_agent_model("system", "user")

    assert mock_client.chat.complete.call_count == 1  # no retries


def test_call_agent_model_propagates_malformed_output_without_retry(monkeypatch):
    """If the model responds but doesn't call the tool, that's not a
    transport failure -- should surface immediately as MalformedOutputError,
    not be retried as though it were an API problem."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "mistral_api_key", "test-key")
    monkeypatch.setattr("app.agent.client.RETRY_BACKOFF_SECONDS", 0)

    mock_client = MagicMock()
    mock_client.chat.complete.return_value = make_no_tool_call_response()

    with patch("mistralai.client.Mistral", return_value=mock_client):
        with pytest.raises(MalformedOutputError):
            call_agent_model("system", "user")

    assert mock_client.chat.complete.call_count == 1  # no retries


def test_call_agent_model_retries_on_bare_connection_error(monkeypatch):
    """A raw transport exception not wrapped in SDKError (e.g. a
    connection-level httpx failure) should still be treated as
    retryable, not crash the retry loop."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "mistral_api_key", "test-key")
    monkeypatch.setattr("app.agent.client.RETRY_BACKOFF_SECONDS", 0)

    connection_error = ConnectionError("connection reset")
    success_response = make_tool_call_response({"diagnosis": "ok"})

    mock_client = MagicMock()
    mock_client.chat.complete.side_effect = [connection_error, success_response]

    with patch("mistralai.client.Mistral", return_value=mock_client):
        result = call_agent_model("system", "user")

    assert result == {"diagnosis": "ok"}
    assert mock_client.chat.complete.call_count == 2
