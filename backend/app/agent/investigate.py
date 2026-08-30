"""
Investigation agent orchestration -- the public entrypoint.

    from app.agent.investigate import investigate_incident
    result = investigate_incident(agent_input)  # -> AgentResult

`investigate_incident` ALWAYS returns a valid AgentResult wrapping a
valid AgentOutput, whatever goes wrong. This is deliberate: the brief
requires "robust error handling" for API failure, malformed output,
missing evidence, and low-confidence diagnosis -- and the most useful
form that takes for a caller is "you always get well-formed structured
JSON back, with a clear indication of what happened," not "you get an
AgentOutput on the happy path and have to write four different
try/except blocks yourself for everything else." Every fallback path
below still runs through the same deterministic guardrails
(app/agent/guardrails.py) as the model-generated path.

Failure modes handled:
  - Missing evidence: no model call is made at all (see module docstring
    in errors.py -- without evidence, any diagnosis would be invented by
    construction, so this is caught before the LLM is ever involved).
  - API failure (network/auth/rate-limit/5xx): caught, safe fallback
    returned, original error preserved on the result for logging.
  - Malformed model output (no tool call, schema-invalid tool input):
    caught, safe fallback returned, same as above.
  - Low-confidence diagnosis: not a failure to catch -- handled by the
    guardrails' confidence thresholds after a successful, well-formed
    model response (see guardrails.py).
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import ValidationError

from app.agent.actions import ESCALATE
from app.agent.client import call_agent_model
from app.agent.errors import AgentAPIError, MalformedOutputError, MissingEvidenceError
from app.agent.guardrails import GuardrailResult, _deterministic_revenue_at_risk, enforce_guardrails
from app.agent.prompt import build_prompts
from app.agent.schema import AgentInput, AgentOutput


@dataclass
class AgentResult:
    output: AgentOutput
    status: str  # "ok" | "no_evidence" | "api_error" | "malformed_output"
    guardrail_violations: list[str]
    error_detail: str | None = None

    @property
    def ok(self) -> bool:
        return self.status == "ok"


def _fallback_output(
    diagnosis: str,
    stop_condition: str,
    agent_input: AgentInput,
) -> AgentOutput:
    """A safe, always-valid AgentOutput for any path that couldn't
    complete a real investigation. Revenue is still computed
    deterministically from evidence when possible (see guardrails'
    `_deterministic_revenue_at_risk`) -- a failed/skipped model call
    doesn't mean the caller has to lose an otherwise-known number."""
    revenue, _found = _deterministic_revenue_at_risk(agent_input)
    return AgentOutput(
        diagnosis=diagnosis,
        observations=[],
        inferences=[],
        evidence_ids=[],
        revenue_at_risk=revenue,
        recommended_action=ESCALATE,
        reason="Automated investigation could not be completed; escalating for human review.",
        confidence=0.0,
        stop_condition=stop_condition,
        escalation_required=True,
    )


def investigate_incident(agent_input: AgentInput, model: str | None = None) -> AgentResult:
    """Run one incident investigation. Never raises for expected failure
    modes (missing evidence, API failure, malformed output) -- always
    returns a valid AgentResult. Genuinely unexpected errors (e.g. a bug
    in this code) still propagate, deliberately, rather than being
    silently swallowed into a generic fallback."""

    # -- Missing evidence: never call the model with nothing to work from --
    if not agent_input.structured_evidence and not agent_input.unstructured_evidence:
        output = _fallback_output(
            diagnosis="No evidence was supplied for this incident.",
            stop_condition="Do not act until structured and/or unstructured evidence is available.",
            agent_input=agent_input,
        )
        result = enforce_guardrails(output, agent_input)
        return AgentResult(
            output=result.output,
            status="no_evidence",
            guardrail_violations=result.violations,
            error_detail=str(MissingEvidenceError("no evidence supplied")),
        )

    system_prompt, user_prompt = build_prompts(agent_input)

    # -- API call --
    try:
        raw = call_agent_model(system_prompt, user_prompt, model=model)
    except AgentAPIError as e:
        output = _fallback_output(
            diagnosis="The investigation could not be completed due to an API failure.",
            stop_condition="Do not act until the investigation can be successfully re-run.",
            agent_input=agent_input,
        )
        result = enforce_guardrails(output, agent_input)
        return AgentResult(
            output=result.output,
            status="api_error",
            guardrail_violations=result.violations,
            error_detail=str(e),
        )
    except MalformedOutputError as e:
        return _malformed_output_result(agent_input, str(e))

    # -- Schema validation --
    try:
        parsed = AgentOutput(**raw)
    except (ValidationError, TypeError) as e:
        return _malformed_output_result(agent_input, str(e))

    # -- Deterministic guardrails (always applied) --
    result = enforce_guardrails(parsed, agent_input)
    return AgentResult(output=result.output, status="ok", guardrail_violations=result.violations)


def _malformed_output_result(agent_input: AgentInput, detail: str) -> AgentResult:
    output = _fallback_output(
        diagnosis="The model's response could not be parsed into a valid result.",
        stop_condition="Do not act until the investigation can be successfully re-run.",
        agent_input=agent_input,
    )
    result = enforce_guardrails(output, agent_input)
    return AgentResult(
        output=result.output,
        status="malformed_output",
        guardrail_violations=result.violations,
        error_detail=detail,
    )
