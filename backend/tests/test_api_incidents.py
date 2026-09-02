"""
Tests for the approve/reject API endpoints (app/api/incidents.py).

These deliberately do NOT depend on the LLM or on which of the real
synthetic-dataset candidates ends up escalated -- each test builds a
minimal AppState with a hand-crafted PendingDecision covering the two
distinct shapes the endpoint must handle correctly:

  1. A bare ESCALATE recommendation (no underlying financial action) --
     approving it must be a no-op, never dispatch through execute_action's
     adapter-calling branch.
  2. An actionable recommendation (RETRY_ELIGIBLE_PAYMENTS) that got
     escalation_required=True from the policy engine (e.g. merchant
     approval required) -- approving it must actually execute via the
     SAME execute_action() the rest of the system uses, and reuse the
     ORIGINAL PolicyDecision's eligible_transaction_ids rather than
     re-deriving them.

Plus the double-recovery-adjacent safety properties: single-use pending
state (second decision call 409s), unknown incident 404s, invalid
decision value 422s, and -- most importantly -- that approving an
actionable action never results in two ActionRecords/executions for the
same transactions.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.agent.actions import ESCALATE, RETRY_ELIGIBLE_PAYMENTS
from app.api import incidents as incidents_module
from app.api.state import AppState, PendingDecision
from app.audit.builder import build_audit_record
from app.policies.adapter import RecoveryActionAdapter
from app.policies.engine import PolicyDecision
from app.policies.executor import EXECUTION_NOT_EXECUTED_ESCALATED, execute_action
from app.policies.ledger import ActionLedger, reset_id_counter


@dataclass
class _StubAgentOutput:
    diagnosis: str = "Stub diagnosis for test."
    evidence_ids: list = field(default_factory=list)
    revenue_at_risk: float = 500.0
    recommended_action: str = ESCALATE
    confidence: float = 0.9
    escalation_required: bool = True


@dataclass
class _StubAgentResult:
    output: _StubAgentOutput
    status: str = "ok"
    guardrail_violations: list = field(default_factory=list)


class _AlwaysSucceedAdapter(RecoveryActionAdapter):
    """Deterministic adapter for tests -- every action succeeds, so a
    double-execution bug (if one slipped through) would show up as
    `succeeded` jumping from 1 to 2 across two calls, not masked by the
    ~55% random failure rate of SimulatedAdapter.
    """

    def retry_payment(self, transaction_id, amount):
        from app.policies.adapter import SimResult
        return SimResult(True, "[TEST] retried", {"transaction_id": transaction_id, "outcome": "SUCCESS", "amount": amount})

    def send_recovery_link(self, transaction_id, customer_id, amount):
        from app.policies.adapter import SimResult
        return SimResult(True, "[TEST] link sent", {"transaction_id": transaction_id, "outcome": "SUCCESS", "amount": amount})

    def offer_alternate_method(self, transaction_id, customer_id, amount):
        from app.policies.adapter import SimResult
        return SimResult(True, "[TEST] offered", {"transaction_id": transaction_id, "outcome": "SUCCESS", "amount": amount})

    def notify_merchant(self, incident_id, message):
        from app.policies.adapter import SimResult
        return SimResult(True, "[TEST] notified", {"incident_id": incident_id, "outcome": "SUCCESS"})


def _make_app(state: AppState) -> FastAPI:
    app = FastAPI()
    app.state.app_state = state
    app.include_router(incidents_module.router)
    return app


def _escalate_pending_state(incident_id="cand_test_escalate"):
    """A pending decision for a bare ESCALATE recommendation -- e.g. the
    agent had low confidence or an API error and recommended handing off
    to a human, with no transactions/action to execute at all.
    """
    reset_id_counter()
    ledger = ActionLedger()
    incident = {"incident_id": incident_id, "severity": "HIGH", "observation": "test incident"}
    policy_decision = PolicyDecision(
        approved=True,
        escalation_required=True,
        reason="ESCALATE recommendation accepted; routed to human review.",
        policy_checks=[],
    )
    action_record = execute_action(
        requested_action=ESCALATE,
        decision=policy_decision,
        incident=incident,
        transactions=[],
        ledger=ledger,
    )
    assert action_record.execution_status == EXECUTION_NOT_EXECUTED_ESCALATED

    agent_result = _StubAgentResult(output=_StubAgentOutput(recommended_action=ESCALATE))
    audit_record = build_audit_record(
        candidate_incident=incident,
        evidence={"structured_evidence": [], "unstructured_evidence": []},
        agent_result=agent_result,
        policy_decision=policy_decision,
        action_record=action_record,
    )

    state = AppState(ledger=ledger)
    state.audit_store.add(audit_record)
    state.add_pending(
        PendingDecision(
            incident_id=incident_id,
            requested_action=ESCALATE,
            policy_decision=policy_decision,
            incident=incident,
            transactions=[],
            audit_record_id=audit_record.record_id,
        )
    )
    return state, incident_id


def _actionable_pending_state(incident_id="cand_test_retry"):
    """A pending decision for an actionable action (RETRY_ELIGIBLE_PAYMENTS)
    that the policy engine approved but flagged escalation_required=True
    (e.g. merchant-approval-required amount) -- approving this SHOULD
    result in a real execution against the two eligible transactions.
    """
    reset_id_counter()
    ledger = ActionLedger()
    incident = {"incident_id": incident_id, "severity": "MEDIUM", "observation": "test incident"}
    transactions = [
        {"transaction_id": "txn_a", "amount": 500.0, "customer_id": "cust_1", "status": "FAILED"},
        {"transaction_id": "txn_b", "amount": 600.0, "customer_id": "cust_2", "status": "FAILED"},
    ]
    policy_decision = PolicyDecision(
        approved=True,
        escalation_required=True,  # e.g. merchant approval required
        reason="Approved, pending merchant sign-off before execution.",
        policy_checks=[],
        eligible_transaction_ids=["txn_a", "txn_b"],
        expected_revenue_recovery=1100.0,
    )
    action_record = execute_action(
        requested_action=RETRY_ELIGIBLE_PAYMENTS,
        decision=policy_decision,
        incident=incident,
        transactions=transactions,
        ledger=ledger,
    )
    assert action_record.execution_status == EXECUTION_NOT_EXECUTED_ESCALATED
    assert ledger.retry_count("txn_a") == 0  # nothing executed yet

    agent_result = _StubAgentResult(
        output=_StubAgentOutput(recommended_action=RETRY_ELIGIBLE_PAYMENTS, revenue_at_risk=1100.0)
    )
    audit_record = build_audit_record(
        candidate_incident=incident,
        evidence={"structured_evidence": [], "unstructured_evidence": []},
        agent_result=agent_result,
        policy_decision=policy_decision,
        action_record=action_record,
    )

    state = AppState(ledger=ledger)
    state.audit_store.add(audit_record)
    state.add_pending(
        PendingDecision(
            incident_id=incident_id,
            requested_action=RETRY_ELIGIBLE_PAYMENTS,
            policy_decision=policy_decision,
            incident=incident,
            transactions=transactions,
            audit_record_id=audit_record.record_id,
        )
    )
    return state, incident_id


# ---------------------------------------------------------------------------
# GET /api/evaluation/audit-trail
# ---------------------------------------------------------------------------

def test_audit_trail_returns_latest_record_per_incident():
    state, incident_id = _escalate_pending_state()
    client = TestClient(_make_app(state))

    resp = client.get("/api/evaluation/audit-trail")
    assert resp.status_code == 200
    ids = [r["detection"]["candidate_incident_id"] for r in resp.json()]
    assert ids.count(incident_id) == 1


# ---------------------------------------------------------------------------
# Approve: bare ESCALATE recommendation -> no-op, never dispatches to adapter
# ---------------------------------------------------------------------------

def test_approve_bare_escalate_is_a_noop_not_an_execution():
    state, incident_id = _escalate_pending_state()
    client = TestClient(_make_app(state))

    resp = client.post(f"/api/incidents/{incident_id}/decision", json={"decision": "approve"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["action_outcome"]["execution_status"] == "NOT_EXECUTED_STOPPED"
    assert body["action_outcome"]["attempted"] == 0
    assert body["policy_decision"]["escalation_required"] is False
    assert incident_id not in state.pending


def test_reject_bare_escalate():
    state, incident_id = _escalate_pending_state()
    client = TestClient(_make_app(state))

    resp = client.post(f"/api/incidents/{incident_id}/decision", json={"decision": "reject"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["action_outcome"]["execution_status"] == "NOT_EXECUTED_REJECTED"
    assert body["policy_decision"]["approved"] is False


# ---------------------------------------------------------------------------
# Approve: actionable action -> real execution via execute_action, using the
# ORIGINAL eligible_transaction_ids (not re-derived)
# ---------------------------------------------------------------------------

def test_approve_actionable_action_executes_against_original_eligible_transactions():
    state, incident_id = _actionable_pending_state()
    client = TestClient(_make_app(state))
    # Swap in a deterministic adapter isn't wired through the endpoint
    # today (execute_action defaults to SimulatedAdapter when none is
    # passed) -- this test intentionally exercises that default path,
    # since that's exactly what the endpoint does. Determinism is
    # achieved instead via the fixed transaction ids' hash-based outcome
    # (see test_policy_executor.py's own determinism test for the same
    # pattern), so re-run stability isn't a concern here.

    resp = client.post(f"/api/incidents/{incident_id}/decision", json={"decision": "approve"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["action_outcome"]["execution_status"] == "SIMULATED"
    assert body["action_outcome"]["transaction_ids"] == ["txn_a", "txn_b"]
    assert body["action_outcome"]["attempted"] == 2
    assert body["policy_decision"]["eligible_transaction_ids"] == ["txn_a", "txn_b"]
    assert body["policy_decision"]["expected_revenue_recovery"] == 1100.0

    # The ledger actually recorded exactly one executed retry per txn --
    # this is the core double-recovery guardrail check.
    assert state.ledger.retry_count("txn_a") == 1
    assert state.ledger.retry_count("txn_b") == 1


# ---------------------------------------------------------------------------
# Double-recovery guardrails
# ---------------------------------------------------------------------------

def test_second_decision_call_returns_409_and_does_not_execute_again():
    state, incident_id = _actionable_pending_state()
    client = TestClient(_make_app(state))

    first = client.post(f"/api/incidents/{incident_id}/decision", json={"decision": "approve"})
    assert first.status_code == 200
    assert state.ledger.retry_count("txn_a") == 1

    second = client.post(f"/api/incidents/{incident_id}/decision", json={"decision": "approve"})
    assert second.status_code == 409
    assert "current_state" in second.json()["detail"]

    # Crucially: the ledger was NOT touched again. If the double-recovery
    # bug were reintroduced, this would be 2.
    assert state.ledger.retry_count("txn_a") == 1
    assert state.ledger.retry_count("txn_b") == 1


def test_reject_then_approve_also_409s():
    """Once resolved (by either decision), the pending entry is gone --
    a caller can't reject then immediately approve to force a second
    execution."""
    state, incident_id = _actionable_pending_state()
    client = TestClient(_make_app(state))

    first = client.post(f"/api/incidents/{incident_id}/decision", json={"decision": "reject"})
    assert first.status_code == 200
    assert first.json()["action_outcome"]["execution_status"] == "NOT_EXECUTED_REJECTED"

    second = client.post(f"/api/incidents/{incident_id}/decision", json={"decision": "approve"})
    assert second.status_code == 409
    # Still rejected -- never executed.
    assert state.ledger.retry_count("txn_a") == 0


def test_unknown_incident_returns_404():
    state, _ = _escalate_pending_state()
    client = TestClient(_make_app(state))

    resp = client.post("/api/incidents/does_not_exist/decision", json={"decision": "approve"})
    assert resp.status_code == 404


def test_invalid_decision_value_returns_422():
    state, incident_id = _escalate_pending_state()
    client = TestClient(_make_app(state))

    resp = client.post(f"/api/incidents/{incident_id}/decision", json={"decision": "maybe"})
    assert resp.status_code == 422


def test_get_single_incident_reports_pending_flag():
    state, incident_id = _actionable_pending_state()
    client = TestClient(_make_app(state))

    before = client.get(f"/api/incidents/{incident_id}")
    assert before.status_code == 200
    assert before.json()["pending_decision"] is True

    client.post(f"/api/incidents/{incident_id}/decision", json={"decision": "approve"})

    after = client.get(f"/api/incidents/{incident_id}")
    assert after.status_code == 200
    assert after.json()["pending_decision"] is False


def test_get_unknown_single_incident_404s():
    state, _ = _escalate_pending_state()
    client = TestClient(_make_app(state))
    resp = client.get("/api/incidents/does_not_exist")
    assert resp.status_code == 404
