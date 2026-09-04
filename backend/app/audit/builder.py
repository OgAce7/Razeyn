"""
Builds one `AuditRecord` from the objects each pipeline stage already
produced. This is pure glue -- no new computation, no LLM calls, no
policy logic. If a number looks wrong here, the bug is in the upstream
module that produced it, not here.

Typical call site (after running detection -> retrieval -> agent ->
policy -> executor for one incident):

    from app.audit.builder import build_audit_record

    record = build_audit_record(
        candidate_incident=candidate,       # dict, from detection
        evidence=evidence,                   # dict, from retrieve_evidence()
        agent_result=agent_result,           # AgentResult, from investigate_incident()
        policy_decision=policy_decision,     # PolicyDecision, from evaluate_policy()
        action_record=action_record,         # ActionRecord, from execute_action()
        ground_truth=ground_truth_incident,  # dict or None, from incidents.json
    )
"""

from __future__ import annotations

from typing import Any

from app.audit.schema import (
    ActionOutcomeRef,
    AgentDecisionRef,
    AuditRecord,
    DetectionRef,
    EvidenceRef,
    GroundTruthRef,
    PolicyDecisionRef,
    next_record_id,
    now_iso,
)


def _detection_ref(candidate_incident: dict[str, Any]) -> DetectionRef:
    stats = candidate_incident.get("supporting_statistics") or {}
    return DetectionRef(
        candidate_incident_id=candidate_incident["incident_id"],
        detection_timestamp=candidate_incident.get("detection_timestamp", ""),
        affected_dimension=candidate_incident.get("affected_dimension", ""),
        affected_segment=dict(candidate_incident.get("affected_segment") or {}),
        window_start=candidate_incident.get("window_start", ""),
        window_end=candidate_incident.get("window_end", ""),
        severity=candidate_incident.get("severity", ""),
        confidence_score=float(candidate_incident.get("confidence_score", 0.0)),
        transaction_count=int(candidate_incident.get("transaction_count", 0)),
        revenue_affected=float(candidate_incident.get("revenue_affected", 0.0)),
        z_score=stats.get("z_score"),
    )


def _evidence_ref(evidence: dict[str, Any] | None) -> EvidenceRef:
    evidence = evidence or {}
    structured = evidence.get("structured_evidence") or []
    unstructured = evidence.get("unstructured_evidence") or []
    return EvidenceRef(
        structured_evidence_ids=tuple(
            item["evidence_id"] for item in structured if item.get("evidence_id")
        ),
        unstructured_evidence_ids=tuple(
            item["evidence_id"] for item in unstructured if item.get("evidence_id")
        ),
    )


def _agent_decision_ref(agent_result: Any) -> AgentDecisionRef:
    """`agent_result` is an `AgentResult` (app/agent/investigate.py):
    has `.output` (AgentOutput), `.status`, `.guardrail_violations`."""
    output = agent_result.output
    return AgentDecisionRef(
        diagnosis=output.diagnosis,
        evidence_ids=tuple(output.evidence_ids),
        revenue_at_risk=float(output.revenue_at_risk),
        recommended_action=output.recommended_action,
        confidence=float(output.confidence),
        escalation_required=bool(output.escalation_required),
        status=agent_result.status,
        guardrail_violations=tuple(agent_result.guardrail_violations),
        error_detail=getattr(agent_result, "error_detail", None),
    )


def _policy_decision_ref(policy_decision: Any) -> PolicyDecisionRef:
    """`policy_decision` is a `PolicyDecision` (app/policies/engine.py)."""
    return PolicyDecisionRef(
        approved=bool(policy_decision.approved),
        escalation_required=bool(policy_decision.escalation_required),
        reason=policy_decision.reason,
        eligible_transaction_ids=tuple(policy_decision.eligible_transaction_ids),
        expected_revenue_recovery=float(policy_decision.expected_revenue_recovery),
        policy_checks=tuple(policy_decision.checks_as_dicts()),
    )


def _action_outcome_ref(action_record: Any) -> ActionOutcomeRef:
    """`action_record` is an `ActionRecord` (app/policies/ledger.py)."""
    result = action_record.actual_result or {}
    return ActionOutcomeRef(
        action_id=action_record.action_id,
        requested_action=action_record.requested_action,
        execution_status=action_record.execution_status,
        transaction_ids=tuple(action_record.transaction_ids),
        attempted=int(result.get("attempted", 0)),
        succeeded=int(result.get("succeeded", 0)),
        failed=int(result.get("failed", 0)),
        timestamp=action_record.timestamp,
    )


def _ground_truth_ref(ground_truth: dict[str, Any] | None) -> GroundTruthRef | None:
    if ground_truth is None:
        return None
    return GroundTruthRef(
        incident_id=ground_truth["incident_id"],
        is_true_incident=bool(ground_truth["is_true_incident"]),
        revenue_exposed=float(ground_truth.get("revenue_exposed", 0.0)),
        transaction_count=int(ground_truth.get("transaction_count", 0)),
        affected_transaction_ids=tuple(ground_truth.get("affected_transaction_ids") or []),
        start_time=ground_truth.get("start_time", ""),
        end_time=ground_truth.get("end_time", ""),
        expected_severity=ground_truth.get("expected_severity", ""),
        affected_segment=dict(ground_truth.get("affected_segment") or {}),
    )


def build_audit_record(
    candidate_incident: dict[str, Any],
    evidence: dict[str, Any] | None,
    agent_result: Any,
    policy_decision: Any,
    action_record: Any,
    ground_truth: dict[str, Any] | None = None,
    record_id: str | None = None,
    created_at: str | None = None,
) -> AuditRecord:
    """Assemble one `AuditRecord` from the outputs already produced by
    detection, retrieval, the agent, the policy engine, and the executor.

    All arguments except `ground_truth` are required -- an audit record
    for an incident that was detected but never reached e.g. the policy
    stage isn't representable by this function (build a partial record
    manually if that's ever needed; it hasn't come up because every
    caller in this codebase runs the full pipeline per incident).
    """
    return AuditRecord(
        record_id=record_id or next_record_id(),
        created_at=created_at or now_iso(),
        detection=_detection_ref(candidate_incident),
        evidence=_evidence_ref(evidence),
        agent_decision=_agent_decision_ref(agent_result),
        policy_decision=_policy_decision_ref(policy_decision),
        action_outcome=_action_outcome_ref(action_record),
        ground_truth=_ground_truth_ref(ground_truth),
    )
