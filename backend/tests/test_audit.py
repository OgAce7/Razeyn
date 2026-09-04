"""
Tests for app/audit/{schema,builder,store}.py.

Focus: the builder correctly glues already-produced pipeline objects into
one AuditRecord without recomputing anything, and the store round-trips
records through JSON without losing/mutating data (tuples stay tuples,
numbers stay numbers, ground_truth=None stays None).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from app.audit.builder import build_audit_record
from app.audit.schema import AuditRecord, reset_id_counter
from app.audit.store import AuditStore
from app.policies.engine import PolicyDecision
from app.policies.ledger import ActionRecord, reset_id_counter as reset_action_id_counter


# --------------------------------------------------------------------------
# Fixtures mirroring real pipeline-stage output shapes
# --------------------------------------------------------------------------


@dataclass
class _FakeAgentOutput:
    diagnosis: str = "UPI payments via HDFC Bank are failing due to bank-side timeouts."
    observations: list = field(default_factory=list)
    inferences: list = field(default_factory=list)
    evidence_ids: list = field(default_factory=lambda: ["ev_001", "ev_002"])
    revenue_at_risk: float = 7402.39
    recommended_action: str = "RETRY_ELIGIBLE_PAYMENTS"
    reason: str = "Transient bank timeout pattern, retry is plausible to succeed."
    confidence: float = 0.82
    stop_condition: str = ""
    escalation_required: bool = False


@dataclass
class _FakeAgentResult:
    output: Any
    status: str = "ok"
    guardrail_violations: list = field(default_factory=list)
    error_detail: str | None = None


def make_candidate_incident(**overrides) -> dict:
    base = {
        "incident_id": "cand_00001",
        "detection_timestamp": "2026-08-12T19:00:00+00:00",
        "affected_dimension": "institution",
        "affected_segment": {"institution": "HDFC Bank", "payment_method": "UPI"},
        "window_start": "2026-08-12T13:00:00+00:00",
        "window_end": "2026-08-12T18:00:00+00:00",
        "severity": "HIGH",
        "confidence_score": 0.91,
        "transaction_count": 20,
        "revenue_affected": 7402.39,
        "affected_transaction_ids": ["txn_001523", "txn_001517"],
        "supporting_statistics": {"z_score": 9.49},
    }
    base.update(overrides)
    return base


def make_ground_truth(**overrides) -> dict:
    base = {
        "incident_id": "inc_001",
        "is_true_incident": True,
        "revenue_exposed": 7402.39,
        "transaction_count": 20,
        "affected_transaction_ids": ["txn_001523", "txn_001517"],
        "start_time": "2026-08-12T13:00:00+00:00",
        "end_time": "2026-08-12T18:00:00+00:00",
        "expected_severity": "HIGH",
        "affected_segment": {"institution": "HDFC Bank", "payment_method": "UPI"},
    }
    base.update(overrides)
    return base


def make_evidence() -> dict:
    return {
        "structured_evidence": [
            {"evidence_id": "ev_001", "evidence_type": "structured", "source": "stats", "data": {}, "relevance_score": 0.9, "timestamp": "t"},
        ],
        "unstructured_evidence": [
            {"evidence_id": "ev_002", "evidence_type": "unstructured", "source": "doc", "text": "...", "relevance_score": 0.8, "timestamp": "t"},
        ],
    }


def make_policy_decision(**overrides) -> PolicyDecision:
    base = dict(
        approved=True,
        escalation_required=False,
        reason="Approved: transient failure reasons, within retry/cooldown limits.",
        policy_checks=[],
        eligible_transaction_ids=["txn_001523", "txn_001517"],
        expected_revenue_recovery=7402.39,
    )
    base.update(overrides)
    return PolicyDecision(**base)


def make_action_record(**overrides) -> ActionRecord:
    reset_action_id_counter()
    base = dict(
        action_id="act_00001",
        incident_id="cand_00001",
        transaction_ids=["txn_001523", "txn_001517"],
        requested_action="RETRY_ELIGIBLE_PAYMENTS",
        approved=True,
        reason="ok",
        timestamp="2026-08-12T19:05:00+00:00",
        expected_revenue_recovery=7402.39,
        actual_result={"outcome": "COMPLETED", "attempted": 2, "succeeded": 1, "failed": 1, "per_transaction": [], "customer_ids_contacted": []},
        policy_checks=[],
        escalation_required=False,
        execution_status="SIMULATED",
    )
    base.update(overrides)
    return ActionRecord(**base)


@pytest.fixture(autouse=True)
def _reset_audit_ids():
    reset_id_counter()
    yield
    reset_id_counter()


# --------------------------------------------------------------------------
# Builder tests
# --------------------------------------------------------------------------


def test_build_audit_record_copies_every_field_verbatim():
    candidate = make_candidate_incident()
    evidence = make_evidence()
    agent_result = _FakeAgentResult(output=_FakeAgentOutput())
    policy_decision = make_policy_decision()
    action_record = make_action_record()
    ground_truth = make_ground_truth()

    record = build_audit_record(
        candidate_incident=candidate,
        evidence=evidence,
        agent_result=agent_result,
        policy_decision=policy_decision,
        action_record=action_record,
        ground_truth=ground_truth,
    )

    assert record.detection.candidate_incident_id == "cand_00001"
    assert record.detection.revenue_affected == 7402.39
    assert record.detection.affected_segment == {"institution": "HDFC Bank", "payment_method": "UPI"}
    assert record.evidence.structured_evidence_ids == ("ev_001",)
    assert record.evidence.unstructured_evidence_ids == ("ev_002",)
    assert record.agent_decision.diagnosis == agent_result.output.diagnosis
    assert record.agent_decision.revenue_at_risk == 7402.39
    assert record.agent_decision.status == "ok"
    assert record.agent_decision.error_detail is None
    assert record.policy_decision.approved is True
    assert record.policy_decision.eligible_transaction_ids == ("txn_001523", "txn_001517")
    assert record.action_outcome.action_id == "act_00001"
    assert record.action_outcome.succeeded == 1
    assert record.action_outcome.attempted == 2
    assert record.ground_truth.is_true_incident is True
    assert record.ground_truth.revenue_exposed == 7402.39
    assert record.ground_truth.affected_segment == {"institution": "HDFC Bank", "payment_method": "UPI"}


def test_build_audit_record_preserves_api_error_detail():
    """Regression test: previously `error_detail` (the actual reason an
    incident fell back to ESCALATE, e.g. "MISTRAL_API_KEY is not
    configured") was computed on AgentResult but never copied into the
    audit record, making a missing/invalid API key indistinguishable
    from a genuine model-driven ESCALATE recommendation anywhere in the
    API or UI."""
    candidate = make_candidate_incident()
    evidence = make_evidence()
    agent_result = _FakeAgentResult(
        output=_FakeAgentOutput(recommended_action="ESCALATE", confidence=0.0),
        status="api_error",
        error_detail="MISTRAL_API_KEY is not configured",
    )
    policy_decision = make_policy_decision()
    action_record = make_action_record()

    record = build_audit_record(
        candidate_incident=candidate,
        evidence=evidence,
        agent_result=agent_result,
        policy_decision=policy_decision,
        action_record=action_record,
        ground_truth=None,
    )

    assert record.agent_decision.status == "api_error"
    assert record.agent_decision.error_detail == "MISTRAL_API_KEY is not configured"


def test_build_audit_record_ground_truth_optional():
    record = build_audit_record(
        candidate_incident=make_candidate_incident(),
        evidence=make_evidence(),
        agent_result=_FakeAgentResult(output=_FakeAgentOutput()),
        policy_decision=make_policy_decision(),
        action_record=make_action_record(),
        ground_truth=None,
    )
    assert record.ground_truth is None


def test_build_audit_record_assigns_sequential_ids_by_default():
    kwargs = dict(
        candidate_incident=make_candidate_incident(),
        evidence=make_evidence(),
        agent_result=_FakeAgentResult(output=_FakeAgentOutput()),
        policy_decision=make_policy_decision(),
        action_record=make_action_record(),
    )
    r1 = build_audit_record(**kwargs)
    r2 = build_audit_record(**kwargs)
    assert r1.record_id != r2.record_id


def test_audit_record_is_frozen():
    record = build_audit_record(
        candidate_incident=make_candidate_incident(),
        evidence=make_evidence(),
        agent_result=_FakeAgentResult(output=_FakeAgentOutput()),
        policy_decision=make_policy_decision(),
        action_record=make_action_record(),
    )
    with pytest.raises(Exception):
        record.record_id = "different"


# --------------------------------------------------------------------------
# Store tests
# --------------------------------------------------------------------------


def _build_sample_record(incident_id="cand_00001", with_gt=True):
    return build_audit_record(
        candidate_incident=make_candidate_incident(incident_id=incident_id),
        evidence=make_evidence(),
        agent_result=_FakeAgentResult(output=_FakeAgentOutput()),
        policy_decision=make_policy_decision(),
        action_record=make_action_record(incident_id=incident_id),
        ground_truth=make_ground_truth() if with_gt else None,
    )


def test_store_add_and_get():
    store = AuditStore()
    record = _build_sample_record()
    store.add(record)
    assert store.get(record.record_id) is record
    assert store.get("nonexistent") is None
    assert store.all() == [record]


def test_store_by_candidate_incident_id():
    store = AuditStore()
    r1 = _build_sample_record(incident_id="cand_A")
    r2 = _build_sample_record(incident_id="cand_B")
    store.add(r1)
    store.add(r2)
    assert store.by_candidate_incident_id("cand_A") == [r1]


def test_store_json_round_trip_preserves_data(tmp_path: Path):
    store = AuditStore()
    store.add(_build_sample_record(incident_id="cand_gt", with_gt=True))
    store.add(_build_sample_record(incident_id="cand_no_gt", with_gt=False))

    out_path = tmp_path / "audit.json"
    store.save_json(out_path)

    reloaded = AuditStore.load_json(out_path)
    assert len(reloaded.all()) == 2

    original_by_id = {r.record_id: r for r in store.all()}
    reloaded_by_id = {r.record_id: r for r in reloaded.all()}
    assert set(original_by_id) == set(reloaded_by_id)

    for record_id, original in original_by_id.items():
        restored = reloaded_by_id[record_id]
        assert restored.detection == original.detection
        assert restored.evidence == original.evidence
        assert restored.agent_decision == original.agent_decision
        assert restored.policy_decision == original.policy_decision
        assert restored.action_outcome == original.action_outcome
        assert restored.ground_truth == original.ground_truth


def test_store_json_round_trip_handles_none_ground_truth(tmp_path: Path):
    store = AuditStore()
    store.add(_build_sample_record(with_gt=False))
    out_path = tmp_path / "audit.json"
    store.save_json(out_path)
    reloaded = AuditStore.load_json(out_path)
    assert reloaded.all()[0].ground_truth is None


def test_saved_json_is_plain_json_readable_without_this_codebase(tmp_path: Path):
    """Reproducibility requirement: the audit trail must be inspectable as
    plain data, not require unpickling Python objects."""
    store = AuditStore()
    store.add(_build_sample_record())
    out_path = tmp_path / "audit.json"
    store.save_json(out_path)
    raw = json.loads(out_path.read_text())
    assert isinstance(raw, list)
    assert raw[0]["detection"]["candidate_incident_id"] == "cand_00001"
    assert raw[0]["ground_truth"]["is_true_incident"] is True
