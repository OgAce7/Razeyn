"""
Unit tests for app/agent/guardrails.py — the deterministic enforcement
layer. These operate directly on AgentOutput/AgentInput objects, with no
mocking needed (no LLM call happens here at all).
"""

from __future__ import annotations

from app.agent.actions import ESCALATE
from app.agent.guardrails import (
    HARD_ESCALATE_CONFIDENCE,
    SOFT_REVIEW_CONFIDENCE,
    _deterministic_revenue_at_risk,
    enforce_guardrails,
)
from app.agent.schema import AgentInput, AgentOutput

STRUCTURED_EVIDENCE = [
    {
        "evidence_id": "ev_stats",
        "evidence_type": "transaction_statistics",
        "source": "transactions.csv",
        "data": {"window_failure_rate": 0.4},
        "text": None,
        "relevance_score": 1.0,
        "timestamp": None,
    },
    {
        "evidence_id": "ev_revenue",
        "evidence_type": "revenue_impact",
        "source": "transactions.csv",
        "data": {"revenue_affected": 5000.0},
        "text": None,
        "relevance_score": 1.0,
        "timestamp": None,
    },
]

UNSTRUCTURED_EVIDENCE = [
    {
        "evidence_id": "ev_doc_1",
        "evidence_type": "incident_report",
        "source": "archive/doc1.md",
        "data": None,
        "text": "Some incident report text.",
        "relevance_score": 0.5,
        "timestamp": None,
    }
]


def make_input(**overrides) -> AgentInput:
    defaults = dict(
        incident={"incident_id": "cand_test"},
        structured_evidence=STRUCTURED_EVIDENCE,
        unstructured_evidence=UNSTRUCTURED_EVIDENCE,
        allowed_actions=["RETRY_ELIGIBLE_PAYMENTS", "NOTIFY_MERCHANT", "ESCALATE"],
    )
    defaults.update(overrides)
    return AgentInput(**defaults)


def make_output(**overrides) -> AgentOutput:
    defaults = dict(
        diagnosis="test diagnosis",
        observations=["obs1"],
        inferences=["inf1"],
        evidence_ids=["ev_stats"],
        revenue_at_risk=5000.0,
        recommended_action="RETRY_ELIGIBLE_PAYMENTS",
        reason="test reason",
        confidence=0.8,
        stop_condition="test stop condition",
        escalation_required=False,
    )
    defaults.update(overrides)
    return AgentOutput(**defaults)


# --------------------------------------------------------------------------
# Deterministic revenue extraction
# --------------------------------------------------------------------------

def test_revenue_prefers_structured_evidence_item():
    agent_input = make_input()
    value, found = _deterministic_revenue_at_risk(agent_input)
    assert found is True
    assert value == 5000.0


def test_revenue_falls_back_to_incident_field():
    agent_input = make_input(
        structured_evidence=[STRUCTURED_EVIDENCE[0]],  # no revenue_impact item
        incident={"incident_id": "cand_test", "revenue_affected": 1234.56},
    )
    value, found = _deterministic_revenue_at_risk(agent_input)
    assert found is True
    assert value == 1234.56


def test_revenue_not_found_when_absent_everywhere():
    agent_input = make_input(
        structured_evidence=[STRUCTURED_EVIDENCE[0]],
        incident={"incident_id": "cand_test"},
    )
    value, found = _deterministic_revenue_at_risk(agent_input)
    assert found is False
    assert value == 0.0


# --------------------------------------------------------------------------
# Evidence ID filtering (anti-invention)
# --------------------------------------------------------------------------

def test_valid_evidence_ids_pass_through_unchanged():
    agent_input = make_input()
    output = make_output(evidence_ids=["ev_stats", "ev_revenue", "ev_doc_1"])
    result = enforce_guardrails(output, agent_input)
    assert set(result.output.evidence_ids) == {"ev_stats", "ev_revenue", "ev_doc_1"}
    assert not any("evidence_id" in v for v in result.violations)


def test_invented_evidence_id_is_dropped_and_flagged():
    agent_input = make_input()
    output = make_output(evidence_ids=["ev_stats", "totally_made_up_id"])
    result = enforce_guardrails(output, agent_input)
    assert result.output.evidence_ids == ["ev_stats"]
    assert any("totally_made_up_id" in v for v in result.violations)


def test_all_invented_evidence_ids_results_in_empty_list():
    agent_input = make_input()
    output = make_output(evidence_ids=["fake1", "fake2"])
    result = enforce_guardrails(output, agent_input)
    assert result.output.evidence_ids == []
    assert result.violations


# --------------------------------------------------------------------------
# Revenue overwrite (anti-invention)
# --------------------------------------------------------------------------

def test_matching_revenue_causes_no_violation():
    agent_input = make_input()
    output = make_output(revenue_at_risk=5000.0)
    result = enforce_guardrails(output, agent_input)
    assert result.output.revenue_at_risk == 5000.0
    assert not any("revenue_at_risk" in v for v in result.violations)


def test_invented_revenue_is_overwritten_with_deterministic_value():
    agent_input = make_input()
    output = make_output(revenue_at_risk=999999.0)
    result = enforce_guardrails(output, agent_input)
    assert result.output.revenue_at_risk == 5000.0  # not the model's number
    assert any("999999" in v for v in result.violations)


def test_revenue_when_none_in_evidence_is_zeroed_and_flagged():
    agent_input = make_input(
        structured_evidence=[STRUCTURED_EVIDENCE[0]],  # no revenue_impact item
        incident={"incident_id": "cand_test"},  # no revenue_affected either
    )
    output = make_output(revenue_at_risk=12345.0)
    result = enforce_guardrails(output, agent_input)
    assert result.output.revenue_at_risk == 0.0
    assert any("invented" in v.lower() for v in result.violations)


def test_revenue_zero_with_no_evidence_available_is_not_flagged():
    agent_input = make_input(
        structured_evidence=[STRUCTURED_EVIDENCE[0]],
        incident={"incident_id": "cand_test"},
    )
    output = make_output(revenue_at_risk=0.0)
    result = enforce_guardrails(output, agent_input)
    assert result.output.revenue_at_risk == 0.0
    assert not any("revenue_at_risk" in v for v in result.violations)


# --------------------------------------------------------------------------
# Action allow-list enforcement (anti policy-bypass)
# --------------------------------------------------------------------------

def test_action_within_allowed_list_passes_through():
    agent_input = make_input(allowed_actions=["RETRY_ELIGIBLE_PAYMENTS", "ESCALATE"])
    output = make_output(recommended_action="RETRY_ELIGIBLE_PAYMENTS")
    result = enforce_guardrails(output, agent_input)
    assert result.output.recommended_action == "RETRY_ELIGIBLE_PAYMENTS"
    assert not any("allowed_actions" in v for v in result.violations)


def test_action_outside_allowed_list_forced_to_escalate():
    agent_input = make_input(allowed_actions=["NOTIFY_MERCHANT", "ESCALATE"])
    output = make_output(recommended_action="RETRY_ELIGIBLE_PAYMENTS", confidence=0.9)
    result = enforce_guardrails(output, agent_input)
    assert result.output.recommended_action == ESCALATE
    assert result.output.escalation_required is True
    assert any("not in the allowed_actions" in v for v in result.violations)


# --------------------------------------------------------------------------
# Confidence thresholds (low-confidence handling)
# --------------------------------------------------------------------------

def test_high_confidence_action_untouched():
    agent_input = make_input()
    output = make_output(confidence=0.9, recommended_action="RETRY_ELIGIBLE_PAYMENTS")
    result = enforce_guardrails(output, agent_input)
    assert result.output.recommended_action == "RETRY_ELIGIBLE_PAYMENTS"
    assert result.output.escalation_required is False


def test_confidence_below_hard_floor_forces_escalate_action():
    agent_input = make_input()
    output = make_output(
        confidence=HARD_ESCALATE_CONFIDENCE - 0.01, recommended_action="RETRY_ELIGIBLE_PAYMENTS"
    )
    result = enforce_guardrails(output, agent_input)
    assert result.output.recommended_action == ESCALATE
    assert result.output.escalation_required is True
    assert any("hard escalation floor" in v for v in result.violations)


def test_confidence_between_thresholds_forces_review_but_keeps_action():
    agent_input = make_input()
    mid_confidence = (HARD_ESCALATE_CONFIDENCE + SOFT_REVIEW_CONFIDENCE) / 2
    output = make_output(
        confidence=mid_confidence,
        recommended_action="RETRY_ELIGIBLE_PAYMENTS",
        escalation_required=False,
    )
    result = enforce_guardrails(output, agent_input)
    assert result.output.recommended_action == "RETRY_ELIGIBLE_PAYMENTS"  # not overridden
    assert result.output.escalation_required is True  # but flagged for review
    assert any("review threshold" in v for v in result.violations)


def test_confidence_above_soft_threshold_not_flagged():
    agent_input = make_input()
    output = make_output(
        confidence=SOFT_REVIEW_CONFIDENCE + 0.1,
        recommended_action="RETRY_ELIGIBLE_PAYMENTS",
        escalation_required=False,
    )
    result = enforce_guardrails(output, agent_input)
    assert result.output.escalation_required is False
    assert not any("threshold" in v for v in result.violations)


# --------------------------------------------------------------------------
# Internal consistency
# --------------------------------------------------------------------------

def test_escalate_action_always_sets_escalation_required():
    agent_input = make_input()
    output = make_output(
        recommended_action="ESCALATE", escalation_required=False, confidence=0.9
    )
    result = enforce_guardrails(output, agent_input)
    assert result.output.escalation_required is True


def test_clean_result_has_no_violations():
    agent_input = make_input()
    output = make_output()
    result = enforce_guardrails(output, agent_input)
    assert result.clean
    assert result.violations == []
