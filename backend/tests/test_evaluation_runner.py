"""
Tests for app/evaluation/runner.py and app/evaluation/report.py.

The agent is stubbed via `investigate_fn` -- per the brief, this task
must not implement or modify the AI agent, and tests must not depend on
a live LLM provider API call. Everything downstream of the stub (policy
engine, executor, audit builder, metrics) is the REAL code.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import pytest

from app.audit.schema import reset_id_counter
from app.evaluation.metrics import evaluate_batch
from app.evaluation.report import render_markdown_report
from app.evaluation.runner import (
    baseline_outcomes_list,
    revenue_recovered_map,
    run_batch_evaluation,
)
from app.policies.ledger import reset_id_counter as reset_action_ids

NOW = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)


@dataclass
class _StubAgentOutput:
    diagnosis: str = "Bank timeout pattern for HDFC UPI."
    observations: list = field(default_factory=list)
    inferences: list = field(default_factory=list)
    evidence_ids: list = field(default_factory=lambda: ["ev_1"])
    revenue_at_risk: float = 1000.0
    recommended_action: str = "RETRY_ELIGIBLE_PAYMENTS"
    reason: str = "Transient failures, retry plausible."
    confidence: float = 0.85
    stop_condition: str = ""
    escalation_required: bool = False


@dataclass
class _StubAgentResult:
    output: Any
    status: str = "ok"
    guardrail_violations: list = field(default_factory=list)
    error_detail: str | None = None


def _make_stub_investigate(output_overrides=None):
    output_overrides = output_overrides or {}

    def _investigate(agent_input):
        return _StubAgentResult(output=_StubAgentOutput(**output_overrides))

    return _investigate


def make_candidate(**overrides) -> dict:
    base = {
        "incident_id": "cand_1",
        "detection_timestamp": "2026-08-20T12:00:00+00:00",
        "affected_dimension": "institution",
        "affected_segment": {"institution": "HDFC Bank"},
        "window_start": "2026-08-20T06:00:00+00:00",
        "window_end": "2026-08-20T11:00:00+00:00",
        "severity": "HIGH",
        "confidence_score": 0.9,
        "transaction_count": 2,
        "revenue_affected": 1000.0,
        "affected_transaction_ids": ["txn_1", "txn_2"],
        "supporting_statistics": {"z_score": 5.0},
    }
    base.update(overrides)
    return base


def make_txn(**overrides) -> dict:
    base = {
        "transaction_id": "txn_1",
        "amount": 600.0,
        "status": "FAILED",
        "failure_reason": "BANK_TIMEOUT",
        "customer_id": "cust_1",
    }
    base.update(overrides)
    return base


@pytest.fixture(autouse=True)
def _reset_ids():
    reset_id_counter()
    reset_action_ids()
    yield


def test_run_batch_evaluation_produces_one_audit_record_per_incident():
    candidates = [make_candidate(incident_id="cand_1"), make_candidate(incident_id="cand_2")]
    transactions_by_id = {
        "txn_1": make_txn(transaction_id="txn_1", amount=600.0),
        "txn_2": make_txn(transaction_id="txn_2", amount=400.0),
    }
    evidence = {
        "cand_1": {"structured_evidence": [{"evidence_id": "ev_1"}], "unstructured_evidence": []},
        "cand_2": {"structured_evidence": [{"evidence_id": "ev_1"}], "unstructured_evidence": []},
    }
    store, results = run_batch_evaluation(
        candidate_incidents=candidates,
        transactions_by_id=transactions_by_id,
        evidence_by_incident_id=evidence,
        investigate_fn=_make_stub_investigate(),
        now=NOW,
    )
    assert len(store.all()) == 2
    assert len(results) == 2
    assert {r.audit_record.detection.candidate_incident_id for r in results} == {"cand_1", "cand_2"}


def test_run_batch_evaluation_wires_ground_truth_when_provided():
    candidates = [make_candidate(incident_id="cand_1")]
    transactions_by_id = {"txn_1": make_txn(transaction_id="txn_1")}
    evidence = {"cand_1": {"structured_evidence": [], "unstructured_evidence": []}}
    ground_truth = {
        "cand_1": {
            "incident_id": "inc_1",
            "is_true_incident": True,
            "revenue_exposed": 1000.0,
            "transaction_count": 2,
            "affected_transaction_ids": ["txn_1", "txn_2"],
            "start_time": "2026-08-20T06:00:00+00:00",
            "end_time": "2026-08-20T11:00:00+00:00",
            "expected_severity": "HIGH",
            "affected_segment": {"institution": "HDFC Bank"},
        }
    }
    store, results = run_batch_evaluation(
        candidate_incidents=candidates,
        transactions_by_id=transactions_by_id,
        evidence_by_incident_id=evidence,
        ground_truth_by_incident_id=ground_truth,
        investigate_fn=_make_stub_investigate(),
        now=NOW,
    )
    record = store.all()[0]
    assert record.ground_truth is not None
    assert record.ground_truth.is_true_incident is True


def test_run_batch_evaluation_runs_baseline_comparison_by_default():
    candidates = [make_candidate(incident_id="cand_1")]
    transactions_by_id = {
        "txn_1": make_txn(transaction_id="txn_1", amount=600.0),
        "txn_2": make_txn(transaction_id="txn_2", amount=400.0),
    }
    evidence = {"cand_1": {"structured_evidence": [], "unstructured_evidence": []}}
    store, results = run_batch_evaluation(
        candidate_incidents=candidates,
        transactions_by_id=transactions_by_id,
        evidence_by_incident_id=evidence,
        investigate_fn=_make_stub_investigate(),
        now=NOW,
    )
    assert results[0].baseline_outcome is not None
    assert results[0].baseline_outcome.incident_id == "cand_1"


def test_run_batch_evaluation_can_skip_baseline():
    candidates = [make_candidate(incident_id="cand_1")]
    transactions_by_id = {"txn_1": make_txn(transaction_id="txn_1")}
    evidence = {"cand_1": {"structured_evidence": [], "unstructured_evidence": []}}
    store, results = run_batch_evaluation(
        candidate_incidents=candidates,
        transactions_by_id=transactions_by_id,
        evidence_by_incident_id=evidence,
        investigate_fn=_make_stub_investigate(),
        run_baseline_comparison=False,
        now=NOW,
    )
    assert results[0].baseline_outcome is None


def test_run_batch_evaluation_revenue_recovered_matches_exact_computation():
    """A STOP recommendation from the stub agent results in zero revenue
    recovered (no-op executor path) -- confirms the wiring reads real
    executor output, not a placeholder."""
    candidates = [make_candidate(incident_id="cand_1")]
    transactions_by_id = {"txn_1": make_txn(transaction_id="txn_1", amount=600.0)}
    evidence = {"cand_1": {"structured_evidence": [], "unstructured_evidence": []}}
    store, results = run_batch_evaluation(
        candidate_incidents=candidates,
        transactions_by_id=transactions_by_id,
        evidence_by_incident_id=evidence,
        investigate_fn=_make_stub_investigate({"recommended_action": "STOP"}),
        now=NOW,
    )
    assert results[0].revenue_recovered == 0.0
    assert store.all()[0].action_outcome.execution_status == "NOT_EXECUTED_STOPPED"


def test_revenue_recovered_map_and_baseline_outcomes_list_helpers():
    candidates = [make_candidate(incident_id="cand_1")]
    transactions_by_id = {"txn_1": make_txn(transaction_id="txn_1", amount=600.0)}
    evidence = {"cand_1": {"structured_evidence": [], "unstructured_evidence": []}}
    store, results = run_batch_evaluation(
        candidate_incidents=candidates,
        transactions_by_id=transactions_by_id,
        evidence_by_incident_id=evidence,
        investigate_fn=_make_stub_investigate(),
        now=NOW,
    )
    rmap = revenue_recovered_map(results)
    blist = baseline_outcomes_list(results)
    assert set(rmap.keys()) == {r.audit_record.record_id for r in results}
    assert len(blist) == len(results)


def test_end_to_end_batch_feeds_evaluate_batch_and_report_render():
    """Full glue test: runner -> evaluate_batch -> render_markdown_report,
    all real code except the stubbed agent call."""
    candidates = [make_candidate(incident_id="cand_1")]
    transactions_by_id = {
        "txn_1": make_txn(transaction_id="txn_1", amount=600.0),
        "txn_2": make_txn(transaction_id="txn_2", amount=400.0, failure_reason="RISK_DECLINE"),
    }
    evidence = {"cand_1": {"structured_evidence": [{"evidence_id": "ev_1"}], "unstructured_evidence": []}}
    ground_truth = {
        "cand_1": {
            "incident_id": "inc_1",
            "is_true_incident": True,
            "revenue_exposed": 1000.0,
            "transaction_count": 2,
            "affected_transaction_ids": ["txn_1", "txn_2"],
            "start_time": "2026-08-20T06:00:00+00:00",
            "end_time": "2026-08-20T11:00:00+00:00",
            "expected_severity": "HIGH",
            "affected_segment": {"institution": "HDFC Bank"},
        }
    }
    store, results = run_batch_evaluation(
        candidate_incidents=candidates,
        transactions_by_id=transactions_by_id,
        evidence_by_incident_id=evidence,
        ground_truth_by_incident_id=ground_truth,
        investigate_fn=_make_stub_investigate(),
        now=NOW,
    )
    report = evaluate_batch(
        store.all(),
        revenue_recovered_map(results),
        baseline_outcomes_list(results),
        generated_at="2026-09-01T00:00:00+00:00",
    )
    assert report.record_count == 1
    assert report.detection.true_positive_count == 1

    text = render_markdown_report(report)
    assert "# Revenue Incident Responder -- Evaluation Report" in text
    assert "## Detection" in text
    assert "## Diagnosis" in text
    assert "## Revenue" in text
    assert "## Actions" in text
    assert "## Safety" in text


def test_report_renders_without_baseline():
    candidates = [make_candidate(incident_id="cand_1")]
    transactions_by_id = {"txn_1": make_txn(transaction_id="txn_1")}
    evidence = {"cand_1": {"structured_evidence": [], "unstructured_evidence": []}}
    store, results = run_batch_evaluation(
        candidate_incidents=candidates,
        transactions_by_id=transactions_by_id,
        evidence_by_incident_id=evidence,
        investigate_fn=_make_stub_investigate(),
        run_baseline_comparison=False,
        now=NOW,
    )
    report = evaluate_batch(store.all(), revenue_recovered_map(results), None)
    text = render_markdown_report(report)
    assert "Baseline comparison: not run" in text


def test_report_output_is_deterministic_text():
    candidates = [make_candidate(incident_id="cand_1")]
    transactions_by_id = {"txn_1": make_txn(transaction_id="txn_1", amount=600.0)}
    evidence = {"cand_1": {"structured_evidence": [], "unstructured_evidence": []}}
    store, results = run_batch_evaluation(
        candidate_incidents=candidates,
        transactions_by_id=transactions_by_id,
        evidence_by_incident_id=evidence,
        investigate_fn=_make_stub_investigate(),
        now=NOW,
    )
    report = evaluate_batch(
        store.all(), revenue_recovered_map(results), baseline_outcomes_list(results),
        generated_at="2026-09-01T00:00:00+00:00",
    )
    text1 = render_markdown_report(report)
    text2 = render_markdown_report(report)
    assert text1 == text2
