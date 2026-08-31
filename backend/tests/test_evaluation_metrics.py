"""
Tests for app/evaluation/metrics.py.

Strategy: build small, hand-crafted AuditRecords (via the real
build_audit_record, so we're testing against the actual schema, not a
parallel fake one) covering every branch each metric function has, and
assert exact numeric results -- these are pure functions of their input
so every assertion here is an exact expected value, not a range/approx.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from app.audit.builder import build_audit_record
from app.audit.schema import reset_id_counter
from app.evaluation.baseline import BaselineOutcome
from app.evaluation.metrics import (
    compute_action_metrics,
    compute_detection_metrics,
    compute_diagnosis_metrics,
    compute_exact_revenue_recovered,
    compute_revenue_metrics,
    compute_safety_metrics,
    evaluate_batch,
)
from app.policies.engine import PolicyCheckResult, PolicyDecision
from app.policies.ledger import ActionRecord, reset_id_counter as reset_action_ids


@dataclass
class _AgentOutput:
    diagnosis: str = "diag"
    observations: list = field(default_factory=list)
    inferences: list = field(default_factory=list)
    evidence_ids: list = field(default_factory=lambda: ["ev_1"])
    revenue_at_risk: float = 1000.0
    recommended_action: str = "RETRY_ELIGIBLE_PAYMENTS"
    reason: str = "reason"
    confidence: float = 0.8
    stop_condition: str = ""
    escalation_required: bool = False


@dataclass
class _AgentResult:
    output: Any
    status: str = "ok"
    guardrail_violations: list = field(default_factory=list)
    error_detail: str | None = None


def make_candidate(**overrides) -> dict:
    base = {
        "incident_id": "cand_1",
        "detection_timestamp": "2026-08-12T19:00:00+00:00",
        "affected_dimension": "institution",
        "affected_segment": {"institution": "HDFC Bank"},
        "window_start": "2026-08-12T13:00:00+00:00",
        "window_end": "2026-08-12T18:00:00+00:00",
        "severity": "HIGH",
        "confidence_score": 0.9,
        "transaction_count": 2,
        "revenue_affected": 1000.0,
        "affected_transaction_ids": ["txn_1", "txn_2"],
        "supporting_statistics": {"z_score": 5.0},
    }
    base.update(overrides)
    return base


def make_gt(**overrides) -> dict:
    base = {
        "incident_id": "inc_1",
        "is_true_incident": True,
        "revenue_exposed": 1000.0,
        "transaction_count": 2,
        "affected_transaction_ids": ["txn_1", "txn_2"],
        "start_time": "2026-08-12T13:00:00+00:00",
        "end_time": "2026-08-12T18:00:00+00:00",
        "expected_severity": "HIGH",
        "affected_segment": {"institution": "HDFC Bank"},
    }
    base.update(overrides)
    return base


def make_policy_decision(**overrides) -> PolicyDecision:
    base = dict(
        approved=True,
        escalation_required=False,
        reason="ok",
        policy_checks=[],
        eligible_transaction_ids=["txn_1", "txn_2"],
        expected_revenue_recovery=1000.0,
    )
    base.update(overrides)
    return PolicyDecision(**base)


def make_action_record(**overrides) -> ActionRecord:
    reset_action_ids()
    base = dict(
        action_id="act_1",
        incident_id="cand_1",
        transaction_ids=["txn_1", "txn_2"],
        requested_action="RETRY_ELIGIBLE_PAYMENTS",
        approved=True,
        reason="ok",
        timestamp="2026-08-12T19:05:00+00:00",
        expected_revenue_recovery=1000.0,
        actual_result={
            "outcome": "COMPLETED",
            "attempted": 2,
            "succeeded": 1,
            "failed": 1,
            "per_transaction": [
                {"transaction_id": "txn_1", "amount": 600.0, "outcome": "SUCCESS"},
                {"transaction_id": "txn_2", "amount": 400.0, "outcome": "FAILED"},
            ],
            "customer_ids_contacted": [],
        },
        policy_checks=[],
        escalation_required=False,
        execution_status="SIMULATED",
    )
    base.update(overrides)
    return ActionRecord(**base)


def make_record(
    candidate=None,
    gt="default",
    agent_output=None,
    policy_decision=None,
    action_record=None,
    evidence=None,
):
    candidate = candidate or make_candidate()
    gt_dict = make_gt() if gt == "default" else gt
    return build_audit_record(
        candidate_incident=candidate,
        evidence=evidence or {"structured_evidence": [{"evidence_id": "ev_1"}], "unstructured_evidence": []},
        agent_result=_AgentResult(output=agent_output or _AgentOutput()),
        policy_decision=policy_decision or make_policy_decision(),
        action_record=action_record or make_action_record(),
        ground_truth=gt_dict,
    )


@pytest.fixture(autouse=True)
def _reset_ids():
    reset_id_counter()
    yield


# --------------------------------------------------------------------------
# Detection metrics
# --------------------------------------------------------------------------


def test_detection_counts_true_and_false_positives():
    records = [
        make_record(gt=make_gt(is_true_incident=True)),
        make_record(gt=make_gt(is_true_incident=False)),
        make_record(gt=make_gt(is_true_incident=True)),
    ]
    m = compute_detection_metrics(records)
    assert m.incidents_detected == 3
    assert m.evaluated_count == 3
    assert m.true_positive_count == 2
    assert m.false_positive_count == 1
    assert m.precision == pytest.approx(2 / 3)


def test_detection_precision_none_when_no_ground_truth():
    records = [make_record(gt=None)]
    m = compute_detection_metrics(records)
    assert m.evaluated_count == 0
    assert m.precision is None
    assert m.true_positive_count == 0
    assert m.false_positive_count == 0


def test_detection_latency_computed_from_window_end_and_start():
    candidate = make_candidate(detection_timestamp="2026-08-12T19:00:00+00:00")
    gt = make_gt(start_time="2026-08-12T13:00:00+00:00", end_time="2026-08-12T18:00:00+00:00")
    records = [make_record(candidate=candidate, gt=gt)]
    m = compute_detection_metrics(records)
    # 19:00 - 18:00 = 1 hour = 3600s ; 19:00 - 13:00 = 6 hours = 21600s
    assert m.mean_detection_latency_seconds == pytest.approx(3600.0)
    assert m.mean_detection_latency_seconds_from_window_start == pytest.approx(21600.0)


def test_detection_latency_none_when_timestamps_unparseable():
    candidate = make_candidate(detection_timestamp="")
    gt = make_gt(start_time="", end_time="")
    records = [make_record(candidate=candidate, gt=gt)]
    m = compute_detection_metrics(records)
    assert m.mean_detection_latency_seconds is None
    assert m.mean_detection_latency_seconds_from_window_start is None


def test_detection_metrics_empty_input():
    m = compute_detection_metrics([])
    assert m.incidents_detected == 0
    assert m.precision is None


# --------------------------------------------------------------------------
# Diagnosis metrics
# --------------------------------------------------------------------------


def test_diagnosis_segment_match_exact_dict_equality():
    matching = make_record(
        candidate=make_candidate(affected_segment={"institution": "HDFC Bank"}),
        gt=make_gt(affected_segment={"institution": "HDFC Bank"}),
    )
    mismatching = make_record(
        candidate=make_candidate(affected_segment={"institution": "ICICI Bank"}),
        gt=make_gt(affected_segment={"institution": "HDFC Bank"}),
    )
    m = compute_diagnosis_metrics([matching, mismatching])
    assert m.segment_match_count == 1
    assert m.segment_match_rate == pytest.approx(0.5)


def test_diagnosis_evidence_supported_rate():
    with_evidence = make_record(agent_output=_AgentOutput(evidence_ids=["ev_1"]))
    without_evidence = make_record(agent_output=_AgentOutput(evidence_ids=[]))
    m = compute_diagnosis_metrics([with_evidence, without_evidence])
    assert m.evaluated_count == 2
    assert m.evidence_supported_count == 1
    assert m.evidence_supported_rate == pytest.approx(0.5)


def test_diagnosis_excludes_non_ok_status_from_evaluated_count():
    ok_record = make_record(agent_output=_AgentOutput(evidence_ids=["ev_1"]))
    fallback_record = make_record(
        candidate=make_candidate(incident_id="cand_2"),
    )
    # Build a fallback-status record manually
    fallback_record = build_audit_record(
        candidate_incident=make_candidate(incident_id="cand_2"),
        evidence={},
        agent_result=_AgentResult(output=_AgentOutput(evidence_ids=[]), status="no_evidence"),
        policy_decision=make_policy_decision(),
        action_record=make_action_record(incident_id="cand_2"),
        ground_truth=make_gt(),
    )
    m = compute_diagnosis_metrics([ok_record, fallback_record])
    assert m.evaluated_count == 1  # only the "ok" one counts
    assert m.evidence_supported_count == 1


def test_diagnosis_metrics_no_ground_truth_gives_none_rates():
    records = [make_record(gt=None)]
    m = compute_diagnosis_metrics(records)
    assert m.segment_match_rate is None
    assert m.evaluated_count == 0


# --------------------------------------------------------------------------
# Revenue metrics
# --------------------------------------------------------------------------


def test_compute_exact_revenue_recovered_sums_only_success_entries():
    ar = make_action_record()
    assert compute_exact_revenue_recovered(ar) == 600.0  # txn_1 succeeded, txn_2 failed


def test_compute_exact_revenue_recovered_zero_when_no_successes():
    ar = make_action_record(
        actual_result={
            "outcome": "COMPLETED",
            "attempted": 1,
            "succeeded": 0,
            "failed": 1,
            "per_transaction": [{"transaction_id": "txn_1", "amount": 100.0, "outcome": "FAILED"}],
            "customer_ids_contacted": [],
        }
    )
    assert compute_exact_revenue_recovered(ar) == 0.0


def test_compute_exact_revenue_recovered_handles_notify_merchant_shape():
    """NOTIFY_MERCHANT's per_transaction entry has no 'amount' key at
    all -- must not raise, must contribute 0."""
    ar = make_action_record(
        requested_action="NOTIFY_MERCHANT",
        actual_result={
            "outcome": "COMPLETED",
            "attempted": 1,
            "succeeded": 1,
            "failed": 0,
            "per_transaction": [{"incident_id": "cand_1", "outcome": "DELIVERED"}],
            "customer_ids_contacted": [],
        },
    )
    assert compute_exact_revenue_recovered(ar) == 0.0


def test_revenue_metrics_sums_exposed_at_risk_and_recovered():
    r1 = make_record(candidate=make_candidate(incident_id="cand_1"), gt=make_gt(incident_id="inc_1", revenue_exposed=1000.0))
    r2 = make_record(
        candidate=make_candidate(incident_id="cand_2"),
        gt=make_gt(incident_id="inc_2", revenue_exposed=2000.0),
        agent_output=_AgentOutput(revenue_at_risk=1500.0),
        action_record=make_action_record(incident_id="cand_2"),
    )
    records = [r1, r2]
    revenue_map = {r.record_id: compute_exact_revenue_recovered(make_action_record()) for r in records}

    m = compute_revenue_metrics(records, revenue_map)
    assert m.total_revenue_exposed == pytest.approx(3000.0)
    assert m.total_revenue_at_risk == pytest.approx(1000.0 + 1500.0)
    assert m.total_revenue_recovered == pytest.approx(600.0 * 2)
    assert m.recovery_rate == pytest.approx((600.0 * 2) / 2500.0)


def test_revenue_metrics_recovery_rate_none_when_no_risk():
    r1 = make_record(agent_output=_AgentOutput(revenue_at_risk=0.0))
    m = compute_revenue_metrics([r1], {r1.record_id: 0.0})
    assert m.recovery_rate is None


def test_revenue_metrics_baseline_comparison():
    r1 = make_record()
    revenue_map = {r1.record_id: 600.0}
    baseline = [BaselineOutcome("cand_1", ("txn_2",), 1, 0, 1, 0.0, 400.0, ())]
    m = compute_revenue_metrics([r1], revenue_map, baseline_outcomes=baseline)
    assert m.baseline_revenue_recovered == 0.0
    assert m.recovery_uplift_vs_baseline == 600.0
    # baseline recovered 0 -> pct is undefined (None), not division-by-zero crash
    assert m.recovery_uplift_vs_baseline_pct is None


def test_revenue_metrics_baseline_uplift_percentage():
    r1 = make_record()
    revenue_map = {r1.record_id: 900.0}
    baseline = [BaselineOutcome("cand_1", ("txn_1",), 1, 1, 0, 600.0, 600.0, ())]
    m = compute_revenue_metrics([r1], revenue_map, baseline_outcomes=baseline)
    assert m.baseline_revenue_recovered == 600.0
    assert m.recovery_uplift_vs_baseline == pytest.approx(300.0)
    assert m.recovery_uplift_vs_baseline_pct == pytest.approx(50.0)


def test_revenue_metrics_no_baseline_gives_none_fields():
    r1 = make_record()
    m = compute_revenue_metrics([r1], {r1.record_id: 0.0}, baseline_outcomes=None)
    assert m.baseline_revenue_recovered is None
    assert m.recovery_uplift_vs_baseline is None
    assert m.recovery_uplift_vs_baseline_pct is None


def test_revenue_metrics_can_be_negative_uplift_without_clamping():
    r1 = make_record()
    revenue_map = {r1.record_id: 100.0}
    baseline = [BaselineOutcome("cand_1", ("txn_1",), 1, 1, 0, 500.0, 500.0, ())]
    m = compute_revenue_metrics([r1], revenue_map, baseline_outcomes=baseline)
    assert m.recovery_uplift_vs_baseline == pytest.approx(-400.0)


# --------------------------------------------------------------------------
# Action metrics
# --------------------------------------------------------------------------


def test_action_metrics_counts_approved_rejected_successful():
    approved = make_record(policy_decision=make_policy_decision(approved=True))
    rejected = make_record(
        candidate=make_candidate(incident_id="cand_2"),
        policy_decision=make_policy_decision(approved=False, eligible_transaction_ids=[]),
        action_record=make_action_record(
            incident_id="cand_2",
            approved=False,
            transaction_ids=[],
            execution_status="NOT_EXECUTED_REJECTED",
            actual_result={"outcome": "NOT_EXECUTED", "detail": "policy rejected"},
        ),
    )
    m = compute_action_metrics([approved, rejected])
    assert m.actions_approved == 1
    assert m.actions_rejected == 1
    assert m.actions_attempted == 1  # only the approved+SIMULATED one
    assert m.actions_successful == 1  # from the "approved" record's succeeded=1


def test_action_metrics_stopped_and_escalated():
    stopped = make_record(
        agent_output=_AgentOutput(recommended_action="STOP"),
        action_record=make_action_record(
            requested_action="STOP",
            transaction_ids=[],
            expected_revenue_recovery=0.0,
            actual_result={"outcome": "NO_ACTION", "detail": "stopped"},
            execution_status="NOT_EXECUTED_STOPPED",
        ),
    )
    escalated = make_record(
        candidate=make_candidate(incident_id="cand_2"),
        agent_output=_AgentOutput(recommended_action="ESCALATE", escalation_required=True),
        policy_decision=make_policy_decision(escalation_required=True),
        action_record=make_action_record(
            incident_id="cand_2",
            requested_action="ESCALATE",
            actual_result={"outcome": "PENDING_HUMAN_REVIEW", "detail": "escalated"},
            execution_status="NOT_EXECUTED_ESCALATED",
            escalation_required=True,
        ),
    )
    m = compute_action_metrics([stopped, escalated])
    assert m.actions_stopped == 1
    assert m.actions_escalated == 1
    assert m.actions_attempted == 0


def test_action_metrics_success_rate_of_attempted():
    r = make_record()  # attempted=2, succeeded=1
    m = compute_action_metrics([r])
    assert m.success_rate_of_attempted == pytest.approx(0.5)


def test_action_metrics_empty_input():
    m = compute_action_metrics([])
    assert m.actions_attempted == 0
    assert m.success_rate_of_attempted is None


# --------------------------------------------------------------------------
# Safety metrics
# --------------------------------------------------------------------------


def test_safety_counts_failed_policy_checks():
    checks = [
        PolicyCheckResult(name="within_amount_bounds", passed=True, detail="ok"),
        PolicyCheckResult(name="cooldown", passed=False, detail="in cooldown"),
    ]
    r = make_record(policy_decision=make_policy_decision(policy_checks=checks))
    m = compute_safety_metrics([r])
    assert m.policy_violations_prevented == 1


def test_safety_counts_guardrail_corrections():
    r = build_audit_record(
        candidate_incident=make_candidate(),
        evidence={},
        agent_result=_AgentResult(
            output=_AgentOutput(),
            guardrail_violations=["revenue_overwritten", "invented_evidence_id_dropped"],
        ),
        policy_decision=make_policy_decision(),
        action_record=make_action_record(),
        ground_truth=make_gt(),
    )
    m = compute_safety_metrics([r])
    assert m.guardrail_corrections == 2


def test_safety_unnecessary_intervention_on_false_positive():
    fp_actioned = make_record(
        gt=make_gt(is_true_incident=False),
        agent_output=_AgentOutput(recommended_action="RETRY_ELIGIBLE_PAYMENTS", revenue_at_risk=500.0),
        policy_decision=make_policy_decision(approved=True),
    )
    m = compute_safety_metrics([fp_actioned])
    assert m.unnecessary_interventions == 1
    assert m.false_positive_cost == pytest.approx(500.0)


def test_safety_false_positive_correctly_stopped_is_not_unnecessary():
    fp_stopped = make_record(
        gt=make_gt(is_true_incident=False),
        agent_output=_AgentOutput(recommended_action="STOP"),
        policy_decision=make_policy_decision(approved=True),
    )
    m = compute_safety_metrics([fp_stopped])
    assert m.unnecessary_interventions == 0
    assert m.false_positive_cost == 0.0


def test_safety_false_positive_but_rejected_by_policy_is_not_unnecessary():
    fp_rejected = make_record(
        gt=make_gt(is_true_incident=False),
        agent_output=_AgentOutput(recommended_action="RETRY_ELIGIBLE_PAYMENTS"),
        policy_decision=make_policy_decision(approved=False),
    )
    m = compute_safety_metrics([fp_rejected])
    assert m.unnecessary_interventions == 0


def test_safety_true_incident_actioned_is_not_unnecessary():
    tp_actioned = make_record(gt=make_gt(is_true_incident=True))
    m = compute_safety_metrics([tp_actioned])
    assert m.unnecessary_interventions == 0
    assert m.false_positive_cost == 0.0


def test_safety_metrics_empty_input():
    m = compute_safety_metrics([])
    assert m.policy_violations_prevented == 0
    assert m.guardrail_corrections == 0
    assert m.unnecessary_interventions == 0
    assert m.false_positive_cost == 0.0


# --------------------------------------------------------------------------
# Composite report
# --------------------------------------------------------------------------


def test_evaluate_batch_is_deterministic_and_composes_all_groups():
    r1 = make_record()
    revenue_map = {r1.record_id: 600.0}
    baseline = [BaselineOutcome("cand_1", ("txn_2",), 1, 0, 1, 0.0, 400.0, ())]

    report1 = evaluate_batch([r1], revenue_map, baseline, generated_at="2026-09-01T00:00:00+00:00")
    report2 = evaluate_batch([r1], revenue_map, baseline, generated_at="2026-09-01T00:00:00+00:00")

    assert report1 == report2
    assert report1.record_count == 1
    assert report1.detection.incidents_detected == 1
    assert report1.revenue.total_revenue_recovered == pytest.approx(600.0)
    assert report1.actions.actions_successful == 1
    assert report1.safety.policy_violations_prevented == 0


def test_evaluate_batch_reproducible_over_repeated_calls_without_fixed_timestamp():
    """Even without pinning generated_at, every OTHER field must be
    identical across repeated calls on the same input -- only
    generated_at is allowed to vary."""
    r1 = make_record()
    revenue_map = {r1.record_id: 600.0}
    report1 = evaluate_batch([r1], revenue_map)
    report2 = evaluate_batch([r1], revenue_map)
    assert report1.detection == report2.detection
    assert report1.diagnosis == report2.diagnosis
    assert report1.revenue == report2.revenue
    assert report1.actions == report2.actions
    assert report1.safety == report2.safety
