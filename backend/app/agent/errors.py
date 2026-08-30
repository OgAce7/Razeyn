"""Exceptions for the investigation agent's error-handling paths.

Note that investigate.py mostly *catches* these internally and converts
them into a valid AgentOutput fallback response rather than letting them
propagate — see docs/agent.md for why ("the agent must return strict
structured JSON" applies to error paths too, not just the happy path).
They're still defined as real exception types (rather than just returning
error codes) so each failure mode is independently raisable/catchable/
testable, including by callers who want the exception behavior instead of
the fallback-response behavior.
"""


class AgentError(Exception):
    """Base class for all agent errors."""


class AgentAPIError(AgentError):
    """The Claude API call itself failed — network error, auth error,
    rate limit, timeout, or a non-2xx response after retries."""


class MalformedOutputError(AgentError):
    """The model's response could not be parsed into a valid AgentOutput
    (missing/incorrectly-typed required field, or no tool call at all)."""


class MissingEvidenceError(AgentError):
    """No evidence (structured or unstructured) was supplied for this
    incident — the agent should not be invoked at all in this case, since
    without evidence any diagnosis would necessarily be invented."""
