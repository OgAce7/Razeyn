"""
Regression tests for app.api.pipeline.run_pipeline_for_dataset's
inter-agent-call spacing (Settings.agent_call_interval_seconds).

Context: seeding/upload calls investigate_incident() once per candidate
incident in a tight loop with no delay between them. On a free-tier
Mistral account with a strict requests-per-second limit, this reliably
triggers 429s across most/all incidents even with per-call retry/backoff
(see app/agent/client.py) -- the *next* incident's first attempt lands
while the limit is still hot. agent_call_interval_seconds lets an
operator on such a tier add spacing between calls; default 0 keeps
existing (paid-tier / test) behavior unchanged.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.api.pipeline import run_pipeline_for_dataset
from app.api.state import AppState
from app.data.loader import load_transactions

SYNTHETIC_CSV = Path("app/data/synthetic/transactions.csv")
pytestmark = pytest.mark.skipif(
    not SYNTHETIC_CSV.exists(),
    reason="synthetic dataset not generated -- run app.data.generate then app.detection.run",
)


def _two_candidates_and_df():
    df = load_transactions().head(50)
    candidates = [
        {
            "incident_id": "cand_a",
            "severity": "MEDIUM",
            "affected_dimension": "payment_method",
            "affected_segment": {"payment_method": df.iloc[0]["payment_method"]},
            "window_start": "2020-01-01T00:00:00+00:00",
            "window_end": "2030-01-01T00:00:00+00:00",
            "revenue_affected": 100.0,
        },
        {
            "incident_id": "cand_b",
            "severity": "MEDIUM",
            "affected_dimension": "payment_method",
            "affected_segment": {"payment_method": df.iloc[0]["payment_method"]},
            "window_start": "2020-01-01T00:00:00+00:00",
            "window_end": "2030-01-01T00:00:00+00:00",
            "revenue_affected": 200.0,
        },
    ]
    return candidates, df


def _mock_agent_result():
    from app.agent.actions import ESCALATE
    from app.agent.schema import AgentOutput

    output = AgentOutput(
        diagnosis="x",
        observations=[],
        inferences=[],
        evidence_ids=[],
        revenue_at_risk=0.0,
        recommended_action=ESCALATE,
        reason="x",
        confidence=0.0,
        stop_condition="x",
        escalation_required=True,
    )
    from app.agent.investigate import AgentResult

    return AgentResult(output=output, status="ok", guardrail_violations=[])


def test_no_delay_between_calls_by_default(monkeypatch):
    """Default agent_call_interval_seconds=0 must not introduce any
    sleep -- existing (paid-tier, and every test in this suite)
    behavior is unaffected."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "agent_call_interval_seconds", 0.0)
    candidates, df = _two_candidates_and_df()
    state = AppState()

    with patch("app.api.pipeline.investigate_incident", return_value=_mock_agent_result()), \
         patch("app.api.pipeline.retrieve_evidence_for_incident", return_value={}), \
         patch("app.api.pipeline.evaluate_policy") as mock_policy, \
         patch("app.api.pipeline.execute_action") as mock_execute, \
         patch("app.api.pipeline.time.sleep") as mock_sleep:
        mock_policy.return_value = MagicMock(
            approved=True, escalation_required=True, eligible_transaction_ids=[],
            expected_revenue_recovery=0.0, checks_as_dicts=lambda: [],
        )
        mock_execute.return_value = MagicMock(
            action_id="a1", requested_action="ESCALATE", execution_status="NOT_EXECUTED_ESCALATED",
            transaction_ids=[], actual_result={"attempted": 0, "succeeded": 0}, timestamp="t",
        )
        run_pipeline_for_dataset(state, df, candidate_incidents=candidates)

    mock_sleep.assert_not_called()


def test_delay_between_calls_when_configured(monkeypatch):
    """A positive agent_call_interval_seconds must sleep between
    successive candidates (but not before the first one)."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "agent_call_interval_seconds", 2.5)
    candidates, df = _two_candidates_and_df()
    state = AppState()

    with patch("app.api.pipeline.investigate_incident", return_value=_mock_agent_result()), \
         patch("app.api.pipeline.retrieve_evidence_for_incident", return_value={}), \
         patch("app.api.pipeline.evaluate_policy") as mock_policy, \
         patch("app.api.pipeline.execute_action") as mock_execute, \
         patch("app.api.pipeline.time.sleep") as mock_sleep:
        mock_policy.return_value = MagicMock(
            approved=True, escalation_required=True, eligible_transaction_ids=[],
            expected_revenue_recovery=0.0, checks_as_dicts=lambda: [],
        )
        mock_execute.return_value = MagicMock(
            action_id="a1", requested_action="ESCALATE", execution_status="NOT_EXECUTED_ESCALATED",
            transaction_ids=[], actual_result={"attempted": 0, "succeeded": 0}, timestamp="t",
        )
        run_pipeline_for_dataset(state, df, candidate_incidents=candidates)

    # 2 candidates -> exactly 1 gap between them, not before the first.
    mock_sleep.assert_called_once_with(2.5)
