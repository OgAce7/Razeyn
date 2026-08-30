"""
Integration tests for app/agent/investigate.py — the public entrypoint.
All Claude API calls are mocked (app.agent.investigate.call_agent_model)
so these run with no network access and no API key, deterministically.

Covers the four required error-handling scenarios plus the happy path:
  1. API failure
  2. Malformed model output (missing field, wrong type, no tool call)
  3. Missing evidence
  4. Low-confidence diagnosis
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.agent.actions import ESCALATE
from app.agent.errors import AgentAPIError, MalformedOutputError
from app.agent.investigate import investigate_incident
from app.agent.schema import AgentInput

STRUCTURED_EVIDENCE = [
    {
        "evidence_id": "ev_stats",
        "evidence_type": "transaction_statistics",
        "source": "transactions.csv",
        "data": {"window_failure_rate": 0.4, "baseline_failure_rate": 0.04},
        "text": None,
        "relevance_score": 1.0,
        "timestamp": "2026-08-12T18:00:00+00:00",
    },
    {
        "evidence_id": "ev_revenue",
        "evidence_type": "revenue_impact",
        "source": "transactions.csv",
        "data": {"revenue_affected": 7402.39},
        "text": None,
        "relevance_score": 1.0,
        "timestamp": "2026-08-12T18:00:00+00:00",
    },
]

UNSTRUCTURED_EVIDENCE = [
    {
        "evidence_id": "ev_doc_1",
        "evidence_type": "incident_report",
        "source": "archive/doc1.md",
        "data": None,
        "text": "UPI failures concentrated on HDFC Bank routing, bank-side timeouts.",
        "relevance_score": 0.4,
        "timestamp": "2026-08-12T18:30:00+00:00",
    }
]

VALID_MOCK_RESPONSE = {
    "diagnosis": "UPI transactions through HDFC Bank are failing well above baseline.",
    "observations": ["Window failure rate 40% vs baseline 4%.", "Doc reports HDFC-specific timeouts."],
    "inferences": ["Pattern suggests a bank-side issue isolated to HDFC."],
    "evidence_ids": ["ev_stats", "ev_revenue", "ev_doc_1"],
    "revenue_at_risk": 7402.39,
    "recommended_action": "RETRY_ELIGIBLE_PAYMENTS",
    "reason": "Timeout-dominant failures isolated to one bank are good retry candidates.",
    "confidence": 0.8,
    "stop_condition": "If retry success stays below 30%, escalate instead.",
    "escalation_required": False,
}


def make_input(**overrides) -> AgentInput:
    defaults = dict(
        incident={"incident_id": "cand_test", "affected_segment": {"payment_method": "UPI"}},
        structured_evidence=STRUCTURED_EVIDENCE,
        unstructured_evidence=UNSTRUCTURED_EVIDENCE,
        allowed_actions=["RETRY_ELIGIBLE_PAYMENTS", "NOTIFY_MERCHANT", "ESCALATE", "WAIT_AND_REASSESS"],
    )
    defaults.update(overrides)
    return AgentInput(**defaults)


# --------------------------------------------------------------------------
# Happy path
# --------------------------------------------------------------------------

def test_happy_path_returns_ok_status_with_no_violations():
    agent_input = make_input()
    with patch("app.agent.investigate.call_agent_model", return_value=VALID_MOCK_RESPONSE):
        result = investigate_incident(agent_input)

    assert result.status == "ok"
    assert result.ok is True
    assert result.guardrail_violations == []
    assert result.output.recommended_action == "RETRY_ELIGIBLE_PAYMENTS"
    assert result.output.revenue_at_risk == 7402.39
    assert result.error_detail is None


def test_result_is_always_a_valid_agent_output_shape():
    agent_input = make_input()
    with patch("app.agent.investigate.call_agent_model", return_value=VALID_MOCK_RESPONSE):
        result = investigate_incident(agent_input)
    dumped = result.output.model_dump()
    for field in (
        "diagnosis", "observations", "inferences", "evidence_ids", "revenue_at_risk",
        "recommended_action", "reason", "confidence", "stop_condition", "escalation_required",
    ):
        assert field in dumped


# --------------------------------------------------------------------------
# 1. Missing evidence
# --------------------------------------------------------------------------

def test_missing_evidence_short_circuits_without_calling_model():
    agent_input = make_input(structured_evidence=[], unstructured_evidence=[])
    with patch("app.agent.investigate.call_agent_model") as mock_call:
        result = investigate_incident(agent_input)
    mock_call.assert_not_called()
    assert result.status == "no_evidence"
    assert result.output.recommended_action == ESCALATE
    assert result.output.escalation_required is True
    assert result.output.confidence == 0.0


def test_missing_evidence_still_reports_revenue_if_known_from_incident():
    agent_input = make_input(
        structured_evidence=[],
        unstructured_evidence=[],
        incident={"incident_id": "cand_test", "revenue_affected": 4321.0},
    )
    with patch("app.agent.investigate.call_agent_model"):
        result = investigate_incident(agent_input)
    assert result.status == "no_evidence"
    assert result.output.revenue_at_risk == 4321.0


# --------------------------------------------------------------------------
# 2. API failure
# --------------------------------------------------------------------------

def test_api_failure_returns_safe_escalate_result():
    agent_input = make_input()
    with patch(
        "app.agent.investigate.call_agent_model",
        side_effect=AgentAPIError("connection timed out"),
    ):
        result = investigate_incident(agent_input)

    assert result.status == "api_error"
    assert result.ok is False
    assert result.output.recommended_action == ESCALATE
    assert result.output.escalation_required is True
    assert result.output.confidence == 0.0
    assert "timed out" in result.error_detail

    # Revenue is still deterministically known from evidence even though
    # the model call itself failed.
    assert result.output.revenue_at_risk == 7402.39


def test_api_failure_does_not_raise():
    """The whole point of the fallback design: callers never need a
    try/except around investigate_incident for expected failure modes."""
    agent_input = make_input()
    with patch(
        "app.agent.investigate.call_agent_model",
        side_effect=AgentAPIError("rate limited"),
    ):
        result = investigate_incident(agent_input)  # should not raise
    assert result.status == "api_error"


# --------------------------------------------------------------------------
# 3. Malformed model output
# --------------------------------------------------------------------------

def test_missing_required_field_is_treated_as_malformed():
    bad_response = dict(VALID_MOCK_RESPONSE)
    del bad_response["confidence"]  # required field missing
    agent_input = make_input()
    with patch("app.agent.investigate.call_agent_model", return_value=bad_response):
        result = investigate_incident(agent_input)

    assert result.status == "malformed_output"
    assert result.output.recommended_action == ESCALATE
    assert result.output.escalation_required is True


def test_wrong_type_field_is_treated_as_malformed():
    bad_response = dict(VALID_MOCK_RESPONSE)
    bad_response["confidence"] = "very confident"  # not a number
    agent_input = make_input()
    with patch("app.agent.investigate.call_agent_model", return_value=bad_response):
        result = investigate_incident(agent_input)
    assert result.status == "malformed_output"


def test_evidence_ids_wrong_type_is_treated_as_malformed():
    bad_response = dict(VALID_MOCK_RESPONSE)
    bad_response["evidence_ids"] = "ev_stats"  # should be a list, not a string
    agent_input = make_input()
    with patch("app.agent.investigate.call_agent_model", return_value=bad_response):
        result = investigate_incident(agent_input)
    assert result.status == "malformed_output"


def test_no_tool_call_at_all_is_treated_as_malformed():
    """Simulates the model responding with plain text instead of calling
    the tool -- client.py raises MalformedOutputError in that case."""
    agent_input = make_input()
    with patch(
        "app.agent.investigate.call_agent_model",
        side_effect=MalformedOutputError("no tool call in response"),
    ):
        result = investigate_incident(agent_input)

    assert result.status == "malformed_output"
    assert result.output.recommended_action == ESCALATE
    assert result.output.escalation_required is True
    assert "no tool call" in result.error_detail


def test_malformed_output_does_not_raise():
    agent_input = make_input()
    with patch(
        "app.agent.investigate.call_agent_model",
        side_effect=MalformedOutputError("bad output"),
    ):
        result = investigate_incident(agent_input)  # should not raise
    assert result.status == "malformed_output"


def test_extra_unexpected_fields_in_response_do_not_break_parsing():
    """Pydantic ignores unknown extra fields by default -- a model adding
    an unrequested field shouldn't count as malformed."""
    response = dict(VALID_MOCK_RESPONSE)
    response["extra_commentary"] = "some additional text the model added"
    agent_input = make_input()
    with patch("app.agent.investigate.call_agent_model", return_value=response):
        result = investigate_incident(agent_input)
    assert result.status == "ok"


# --------------------------------------------------------------------------
# 4. Low-confidence diagnosis
# --------------------------------------------------------------------------

def test_low_confidence_response_forces_escalation_but_status_still_ok():
    low_confidence_response = dict(VALID_MOCK_RESPONSE)
    low_confidence_response["confidence"] = 0.15  # below hard floor
    agent_input = make_input()
    with patch("app.agent.investigate.call_agent_model", return_value=low_confidence_response):
        result = investigate_incident(agent_input)

    # The model call and parsing succeeded -- this isn't an error path --
    # but the guardrails still override the action.
    assert result.status == "ok"
    assert result.output.recommended_action == ESCALATE
    assert result.output.escalation_required is True
    assert result.guardrail_violations  # something was corrected


def test_moderate_confidence_flags_review_without_discarding_action():
    moderate_response = dict(VALID_MOCK_RESPONSE)
    moderate_response["confidence"] = 0.3  # between hard floor and soft threshold
    moderate_response["escalation_required"] = False
    agent_input = make_input()
    with patch("app.agent.investigate.call_agent_model", return_value=moderate_response):
        result = investigate_incident(agent_input)

    assert result.status == "ok"
    assert result.output.recommended_action == "RETRY_ELIGIBLE_PAYMENTS"  # kept
    assert result.output.escalation_required is True  # but flagged


# --------------------------------------------------------------------------
# Policy bypass / evidence invention guardrails, exercised end-to-end
# --------------------------------------------------------------------------

def test_disallowed_action_is_overridden_end_to_end():
    response = dict(VALID_MOCK_RESPONSE)
    response["recommended_action"] = "SEND_RECOVERY_LINK"
    agent_input = make_input(allowed_actions=["NOTIFY_MERCHANT", "ESCALATE"])
    with patch("app.agent.investigate.call_agent_model", return_value=response):
        result = investigate_incident(agent_input)
    assert result.output.recommended_action == ESCALATE
    assert any("allowed_actions" in v for v in result.guardrail_violations)


def test_invented_evidence_id_is_stripped_end_to_end():
    response = dict(VALID_MOCK_RESPONSE)
    response["evidence_ids"] = ["ev_stats", "invented_id_xyz"]
    agent_input = make_input()
    with patch("app.agent.investigate.call_agent_model", return_value=response):
        result = investigate_incident(agent_input)
    assert "invented_id_xyz" not in result.output.evidence_ids
    assert "ev_stats" in result.output.evidence_ids


def test_invented_revenue_is_corrected_end_to_end():
    response = dict(VALID_MOCK_RESPONSE)
    response["revenue_at_risk"] = 50000.0  # doesn't match the 7402.39 in evidence
    agent_input = make_input()
    with patch("app.agent.investigate.call_agent_model", return_value=response):
        result = investigate_incident(agent_input)
    assert result.output.revenue_at_risk == 7402.39
