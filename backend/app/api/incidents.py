"""
Live incident endpoints: the real audit trail, and approve/reject for
escalated incidents.

APPROVE/REJECT AND THE DOUBLE-RECOVERY BUG
-------------------------------------------
The bug class this endpoint must not reintroduce: an action executes
against real (simulated) transactions, and something -- a retried HTTP
request, a double-click before the UI disables the button, a page
refresh that re-submits -- causes execute_action to run a SECOND time
for the same pending decision, potentially recovering (simulating
recovery of) the same revenue twice.

Three separate protections here, each independently sufficient:

1. **No second call to evaluate_policy.** Approving an incident does NOT
   re-run policy evaluation against the current ledger state. It reuses
   the exact `PolicyDecision` object (same eligible_transaction_ids,
   same expected_revenue_recovery) produced back when this incident was
   first investigated and escalated. Re-deriving eligibility at
   approve-time against a ledger that other incidents may have written
   to since would risk a different -- and possibly overlapping --
   eligible-transaction set. Only `escalation_required` is flipped from
   True to False on a fresh dataclasses.replace() copy; nothing else
   about the decision changes.

2. **Pending state is single-use.** `AppState.pop_pending()` removes the
   PendingDecision from `state.pending` as part of resolving it. A
   second request for the same incident_id after that point finds
   nothing pending and gets 409 Conflict with the already-resolved
   outcome, never a second call to execute_action.

3. **Per-incident lock.** `state.lock_for(incident_id)` serializes
   concurrent requests for the same incident_id, so two near-simultaneous
   requests (e.g. an accidental double-click that fires before the UI's
   optimistic disable takes effect) can't both pass the "is there a
   pending decision" check before either has removed it -- the second
   one always waits for the first to finish and then hits protection #2.

Executing the action itself is still done by calling `execute_action`
from app/policies/executor.py -- the exact function every other
execution path in this project uses. This file adds zero new execution
logic; it only decides WHICH PolicyDecision to hand that function.
"""

from __future__ import annotations

import dataclasses
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.agent.actions import ESCALATE
from app.audit.builder import build_audit_record
from app.policies.executor import execute_action
from app.policies.ledger import ActionRecord, new_action_id, now_iso

from app.api.state import AppState, IncidentAlreadyResolvedError

router = APIRouter(prefix="/api", tags=["incidents"])


def _get_state(request: Request) -> AppState:
    return request.app.state.app_state


@router.get("/evaluation/audit-trail")
def get_audit_trail(request: Request):
    state = _get_state(request)
    return [record.to_dict() for record in state.latest_records_by_incident()]


@router.get("/incidents/{incident_id}")
def get_incident(incident_id: str, request: Request):
    state = _get_state(request)
    records = [
        r for r in state.latest_records_by_incident()
        if r.detection.candidate_incident_id == incident_id
    ]
    if not records:
        raise HTTPException(status_code=404, detail=f"No incident found with id {incident_id!r}")
    record = records[0]
    return {
        **record.to_dict(),
        "pending_decision": incident_id in state.pending,
    }


class DecisionRequest(BaseModel):
    decision: Literal["approve", "reject"]


@router.post("/incidents/{incident_id}/decision")
async def decide_incident(incident_id: str, body: DecisionRequest, request: Request):
    state = _get_state(request)

    async with state.lock_for(incident_id):
        try:
            pending = state.pop_pending(incident_id)
        except IncidentAlreadyResolvedError:
            existing = [
                r for r in state.latest_records_by_incident()
                if r.detection.candidate_incident_id == incident_id
            ]
            if existing:
                # Already resolved (or never escalated in the first
                # place) -- tell the caller the current state rather
                # than silently no-op'ing or, worse, executing again.
                raise HTTPException(
                    status_code=409,
                    detail={
                        "message": f"Incident {incident_id!r} has no pending decision.",
                        "current_state": existing[0].to_dict(),
                    },
                )
            raise HTTPException(status_code=404, detail=f"No incident found with id {incident_id!r}")

        if body.decision == "approve" and pending.requested_action == ESCALATE:
            # A bare ESCALATE recommendation (as opposed to an actionable
            # action like RETRY_ELIGIBLE_PAYMENTS that got
            # escalation_required=True for merchant-approval/confidence
            # reasons) has no underlying financial action at all --
            # engine.py's ESCALATE pass-through always returns
            # escalation_required=True by design (see policies/engine.py
            # section 2). "Approving" it means the human has finished
            # reviewing and there's nothing further to execute; it must
            # NOT be treated like flipping escalation_required=False on
            # an actionable decision, or execute_action falls through to
            # its adapter-calling branch for an action with no adapter
            # mapping. Resolve it the same way STOP is resolved: approved,
            # no escalation outstanding, nothing executed.
            resolved_decision = dataclasses.replace(
                pending.policy_decision,
                escalation_required=False,
                reason=pending.policy_decision.reason + " Reviewed and closed by human reviewer; no action taken.",
            )
        elif body.decision == "approve":
            # Reuse the ORIGINAL PolicyDecision verbatim except for
            # escalation_required -- see module docstring, protection #1.
            resolved_decision = dataclasses.replace(
                pending.policy_decision,
                escalation_required=False,
                reason=pending.policy_decision.reason + " Approved by human reviewer.",
            )
        else:
            resolved_decision = dataclasses.replace(
                pending.policy_decision,
                approved=False,
                escalation_required=False,
                reason="Rejected by human reviewer.",
            )

        if body.decision == "approve" and pending.requested_action == ESCALATE:
            # Deliberately NOT calling execute_action here -- see the
            # comment above on why a bare ESCALATE has no adapter
            # mapping to dispatch to. This mirrors execute_action's own
            # _record_no_op helper (used for STOP/WAIT_AND_REASSESS) so
            # the resulting ActionRecord has the same shape as every
            # other no-op outcome in this system; it still goes through
            # `ledger.record()`, the same single write path everything
            # else uses, so future ledger queries (retry counts,
            # cooldowns, contact limits) see it consistently.
            action_record = ActionRecord(
                action_id=new_action_id(),
                incident_id=pending.incident_id,
                transaction_ids=[],
                requested_action=pending.requested_action,
                approved=True,
                reason=resolved_decision.reason,
                timestamp=now_iso(),
                expected_revenue_recovery=0.0,
                actual_result={"outcome": "NO_ACTION", "detail": "Escalation reviewed and closed; no recovery action was recommended."},
                policy_checks=resolved_decision.checks_as_dicts(),
                escalation_required=False,
                execution_status="NOT_EXECUTED_STOPPED",
            )
            state.ledger.record(action_record)
        else:
            action_record = execute_action(
                requested_action=pending.requested_action,
                decision=resolved_decision,
                incident=pending.incident,
                transactions=pending.transactions,
                ledger=state.ledger,
            )

        # Find the original escalated AuditRecord so the new record can
        # carry the same detection/evidence/agent_decision context
        # forward -- only the policy_decision and action_outcome change.
        original = next(
            (r for r in state.audit_store.all() if r.record_id == pending.audit_record_id),
            None,
        )
        if original is None:  # pragma: no cover - defensive, should be unreachable
            raise HTTPException(status_code=500, detail="Original audit record not found.")

        new_record = build_audit_record(
            candidate_incident=pending.incident,
            evidence={
                "structured_evidence": [{"evidence_id": eid} for eid in original.evidence.structured_evidence_ids],
                "unstructured_evidence": [{"evidence_id": eid} for eid in original.evidence.unstructured_evidence_ids],
            },
            agent_result=_AgentResultShim(original.agent_decision),
            policy_decision=resolved_decision,
            action_record=action_record,
            ground_truth=None,
        )
        state.audit_store.add(new_record)

        return new_record.to_dict()


class _AgentResultShim:
    """build_audit_record expects an AgentResult (output + status +
    guardrail_violations); the original AgentOutput itself isn't kept
    around by AppState (only the compressed AgentDecisionRef is), and
    re-running the agent would be both wasteful and a second source of
    the exact re-derivation risk described in the module docstring.
    This shim exposes the same three attributes build_audit_record
    reads, sourced from the already-recorded AgentDecisionRef, so the
    new AuditRecord's agent_decision section is identical to the
    original investigation's -- nothing about the AI's diagnosis
    changes when a human approves or rejects it.
    """

    def __init__(self, agent_decision_ref):
        self.output = _AgentOutputShim(agent_decision_ref)
        self.status = agent_decision_ref.status
        self.guardrail_violations = list(agent_decision_ref.guardrail_violations)


class _AgentOutputShim:
    def __init__(self, ref):
        self.diagnosis = ref.diagnosis
        self.evidence_ids = list(ref.evidence_ids)
        self.revenue_at_risk = ref.revenue_at_risk
        self.recommended_action = ref.recommended_action
        self.confidence = ref.confidence
        self.escalation_required = ref.escalation_required
