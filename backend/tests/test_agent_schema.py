"""Tests for app/agent/schema.py — AgentInput/AgentOutput validation rules."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.agent.schema import AgentInput, AgentOutput


def test_agent_input_requires_incident():
    with pytest.raises(ValidationError):
        AgentInput()  # missing required `incident`


def test_agent_input_defaults():
    agent_input = AgentInput(incident={"incident_id": "x"})
    assert agent_input.structured_evidence == []
    assert agent_input.unstructured_evidence == []
    assert agent_input.transaction_context is None
    assert agent_input.merchant_policies == {}
    assert len(agent_input.allowed_actions) > 0  # defaults to full action universe


def test_agent_input_rejects_empty_allowed_actions():
    with pytest.raises(ValidationError):
        AgentInput(incident={"incident_id": "x"}, allowed_actions=[])


def _valid_output_kwargs(**overrides):
    defaults = dict(
        diagnosis="d",
        observations=[],
        inferences=[],
        evidence_ids=[],
        revenue_at_risk=100.0,
        recommended_action="ESCALATE",
        reason="r",
        confidence=0.5,
        stop_condition="s",
        escalation_required=True,
    )
    defaults.update(overrides)
    return defaults


def test_agent_output_valid_construction():
    output = AgentOutput(**_valid_output_kwargs())
    assert output.recommended_action == "ESCALATE"


def test_agent_output_missing_required_field_raises():
    kwargs = _valid_output_kwargs()
    del kwargs["diagnosis"]
    with pytest.raises(ValidationError):
        AgentOutput(**kwargs)


def test_agent_output_wrong_type_raises():
    kwargs = _valid_output_kwargs(escalation_required="yes")  # not a bool-coercible-safe value in strict sense
    # pydantic v2 by default coerces "yes"-like strings for bool leniently in some configs;
    # use an unambiguous bad type instead to be robust across pydantic settings:
    kwargs["escalation_required"] = {"not": "a bool"}
    with pytest.raises(ValidationError):
        AgentOutput(**kwargs)


def test_agent_output_confidence_is_clamped_not_rejected():
    output = AgentOutput(**_valid_output_kwargs(confidence=1.5))
    assert output.confidence == 1.0
    output2 = AgentOutput(**_valid_output_kwargs(confidence=-0.3))
    assert output2.confidence == 0.0


def test_agent_output_confidence_wrong_type_raises():
    kwargs = _valid_output_kwargs(confidence="high")
    with pytest.raises(ValidationError):
        AgentOutput(**kwargs)


def test_agent_output_json_serialization_round_trips():
    output = AgentOutput(**_valid_output_kwargs())
    dumped = output.model_dump_json_strict()
    assert '"recommended_action"' in dumped
    reloaded = AgentOutput.model_validate_json(dumped)
    assert reloaded == output
