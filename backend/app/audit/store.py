"""
Storage for `AuditRecord`s.

Same philosophy as `app/policies/ledger.py`: an in-memory, injectable
store, not a database. This project's brief explicitly asks for
"reproducible from stored transaction/action/outcome data" -- an
in-memory list satisfies that within one process/session, and
`AuditStore.save_json` / `load_json` give durable persistence for
between-run reproducibility (e.g. an evaluation batch run that writes
its audit trail to disk, then a separate `python -m app.evaluation.run`
invocation reads it back) without pulling in a real database. A future
`app/models/AuditLog` SQLAlchemy table (still not built, per
docs/architecture.md) can be a thin wrapper around the same
`AuditRecord.to_dict()` shape.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from app.audit.schema import (
    ActionOutcomeRef,
    AgentDecisionRef,
    AuditRecord,
    DetectionRef,
    EvidenceRef,
    GroundTruthRef,
    PolicyDecisionRef,
)


@dataclass
class AuditStore:
    records: list[AuditRecord] = field(default_factory=list)

    def add(self, record: AuditRecord) -> None:
        self.records.append(record)

    def all(self) -> list[AuditRecord]:
        return list(self.records)

    def get(self, record_id: str) -> AuditRecord | None:
        for r in self.records:
            if r.record_id == record_id:
                return r
        return None

    def by_candidate_incident_id(self, candidate_incident_id: str) -> list[AuditRecord]:
        return [r for r in self.records if r.detection.candidate_incident_id == candidate_incident_id]

    def save_json(self, path: Path | str) -> None:
        path = Path(path)
        payload = [r.to_dict() for r in self.records]
        path.write_text(json.dumps(payload, indent=2, default=str))

    @classmethod
    def load_json(cls, path: Path | str) -> "AuditStore":
        path = Path(path)
        raw = json.loads(path.read_text())
        store = cls()
        for entry in raw:
            store.add(_record_from_dict(entry))
        return store


def _record_from_dict(entry: dict) -> AuditRecord:
    """Reconstruct an AuditRecord (and its nested frozen dataclasses)
    from a plain dict, e.g. one read back from `save_json`. Round-trips
    tuples correctly even though JSON only has lists."""
    gt = entry.get("ground_truth")
    return AuditRecord(
        record_id=entry["record_id"],
        created_at=entry["created_at"],
        detection=DetectionRef(**entry["detection"]),
        evidence=EvidenceRef(
            structured_evidence_ids=tuple(entry["evidence"]["structured_evidence_ids"]),
            unstructured_evidence_ids=tuple(entry["evidence"]["unstructured_evidence_ids"]),
        ),
        agent_decision=AgentDecisionRef(
            diagnosis=entry["agent_decision"]["diagnosis"],
            evidence_ids=tuple(entry["agent_decision"]["evidence_ids"]),
            revenue_at_risk=entry["agent_decision"]["revenue_at_risk"],
            recommended_action=entry["agent_decision"]["recommended_action"],
            confidence=entry["agent_decision"]["confidence"],
            escalation_required=entry["agent_decision"]["escalation_required"],
            status=entry["agent_decision"]["status"],
            guardrail_violations=tuple(entry["agent_decision"]["guardrail_violations"]),
        ),
        policy_decision=PolicyDecisionRef(
            approved=entry["policy_decision"]["approved"],
            escalation_required=entry["policy_decision"]["escalation_required"],
            reason=entry["policy_decision"]["reason"],
            eligible_transaction_ids=tuple(entry["policy_decision"]["eligible_transaction_ids"]),
            expected_revenue_recovery=entry["policy_decision"]["expected_revenue_recovery"],
            policy_checks=tuple(entry["policy_decision"]["policy_checks"]),
        ),
        action_outcome=ActionOutcomeRef(
            action_id=entry["action_outcome"]["action_id"],
            requested_action=entry["action_outcome"]["requested_action"],
            execution_status=entry["action_outcome"]["execution_status"],
            transaction_ids=tuple(entry["action_outcome"]["transaction_ids"]),
            attempted=entry["action_outcome"]["attempted"],
            succeeded=entry["action_outcome"]["succeeded"],
            failed=entry["action_outcome"]["failed"],
            timestamp=entry["action_outcome"]["timestamp"],
        ),
        ground_truth=GroundTruthRef(
            incident_id=gt["incident_id"],
            is_true_incident=gt["is_true_incident"],
            revenue_exposed=gt["revenue_exposed"],
            transaction_count=gt["transaction_count"],
            affected_transaction_ids=tuple(gt["affected_transaction_ids"]),
            start_time=gt["start_time"],
            end_time=gt["end_time"],
            expected_severity=gt["expected_severity"],
            affected_segment=dict(gt.get("affected_segment") or {}),
        )
        if gt is not None
        else None,
    )
