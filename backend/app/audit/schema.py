"""
Audit-trail record schema.

An `AuditRecord` is the single, immutable, append-only record of one
incident's full lifecycle: detection -> evidence retrieval -> AI decision
-> policy decision -> action -> outcome. It does not recompute or
reinterpret anything -- every field is copied verbatim from the object
that module already produced (candidate incident dict, evidence bundle,
`AgentOutput`, `PolicyDecision`, `ActionRecord`). This module's only job
is to glue those already-produced objects into one row per incident so
the evaluation layer (`app/evaluation/`) has a single source to compute
metrics from, and so a human/audit reader can reconstruct exactly what
happened and why for any incident without cross-referencing five files.

Nothing here talks to the AI model, the policy engine, or the executor --
those all already ran by the time `build_audit_record` is called. This is
purely a recording step, same spirit as `app/policies/ledger.py` but
spanning the *entire* pipeline rather than just the policy/action stage.

Design choices worth calling out:
  - `AuditRecord` is a frozen dataclass -- once built, a record cannot be
    mutated in place. Reruns produce new records, not edits to old ones.
  - Every monetary field is copied from a field that upstream code
    documents as deterministic (see docstrings on each field below and
    `docs/agent.md` / `docs/policy_engine.md` for where each number
    originates). This module never computes a dollar figure itself.
  - ground_truth is optional and *only* populated when the caller has it
    (i.e. running against the synthetic dataset with known incidents).
    In a live deployment there is no ground truth; the schema still
    works, just with that field left None, and detection-accuracy
    metrics that require it are skipped rather than faked.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any


@dataclass(frozen=True)
class GroundTruthRef:
    """Ground-truth incident info, when available (synthetic/eval runs only).

    Copied verbatim from `app/data/synthetic/incidents.json` entries (see
    `app/data/schema.py` for the source schema) -- never computed here.
    """

    incident_id: str
    is_true_incident: bool
    revenue_exposed: float
    transaction_count: int
    affected_transaction_ids: tuple[str, ...]
    start_time: str
    end_time: str
    expected_severity: str
    affected_segment: dict[str, str] = field(default_factory=dict)
    """The filter ground truth used to inject this incident (e.g.
    {"payment_method": "UPI", "institution": "HDFC Bank"}) -- see
    app/data/schema.py's incidents.json schema. Used by
    app/evaluation/metrics.py to check whether the detector converged on
    the correct affected segment, independent of revenue/count accuracy.
    Empty dict for the benign-fluctuation ground-truth case, which has no
    injected segment."""


@dataclass(frozen=True)
class DetectionRef:
    """What the detection engine (app/detection/) produced for this incident.

    `revenue_affected` here is the detector's own observed figure (see
    docs/detection.md: "the actual observed FAILED-transaction revenue
    inside the flagged window") -- distinct from ground truth's
    `revenue_exposed`, which the metrics layer compares deliberately.
    """

    candidate_incident_id: str
    detection_timestamp: str
    affected_dimension: str
    affected_segment: dict[str, str]
    window_start: str
    window_end: str
    severity: str
    confidence_score: float
    transaction_count: int
    revenue_affected: float
    z_score: float | None = None


@dataclass(frozen=True)
class EvidenceRef:
    """Which evidence the agent was actually shown (ids + counts only --
    the evaluation layer needs to know *what was available*, not the full
    text/data payload, to check evidence-supported diagnosis)."""

    structured_evidence_ids: tuple[str, ...]
    unstructured_evidence_ids: tuple[str, ...]


@dataclass(frozen=True)
class AgentDecisionRef:
    """Copied field-for-field from `AgentOutput` (app/agent/schema.py),
    post-guardrails (app/agent/guardrails.py) -- i.e. this is what the
    caller actually acted on, not the model's raw pre-guardrail output.
    `revenue_at_risk` is guaranteed deterministic by that point (see
    docs/agent.md: "revenue is always overwritten, never just validated").
    """

    diagnosis: str
    evidence_ids: tuple[str, ...]
    revenue_at_risk: float
    recommended_action: str
    confidence: float
    escalation_required: bool
    status: str  # AgentResult.status: "ok" | "no_evidence" | "api_error" | "malformed_output"
    guardrail_violations: tuple[str, ...] = field(default_factory=tuple)
    error_detail: str | None = None


@dataclass(frozen=True)
class PolicyDecisionRef:
    """Copied from `PolicyDecision` (app/policies/engine.py)."""

    approved: bool
    escalation_required: bool
    reason: str
    eligible_transaction_ids: tuple[str, ...]
    expected_revenue_recovery: float
    policy_checks: tuple[dict, ...]


@dataclass(frozen=True)
class ActionOutcomeRef:
    """Copied from `ActionRecord` (app/policies/ledger.py) /
    `execute_action`'s return value (app/policies/executor.py)."""

    action_id: str
    requested_action: str
    execution_status: str
    transaction_ids: tuple[str, ...]
    attempted: int
    succeeded: int
    failed: int
    timestamp: str


@dataclass(frozen=True)
class AuditRecord:
    """One row: the full chain for one incident, end to end.

    `record_id` and `created_at` identify this specific audit entry (an
    incident can in principle be re-investigated, producing a second
    record with a new `record_id` but the same `detection.candidate_incident_id`).
    """

    record_id: str
    created_at: str
    detection: DetectionRef
    evidence: EvidenceRef
    agent_decision: AgentDecisionRef
    policy_decision: PolicyDecisionRef
    action_outcome: ActionOutcomeRef
    ground_truth: GroundTruthRef | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, default=str)


_id_counter = 0


def next_record_id() -> str:
    global _id_counter
    _id_counter += 1
    return f"audit_{_id_counter:05d}"


def reset_id_counter() -> None:
    """Reset the audit-record id counter. For deterministic tests only."""
    global _id_counter
    _id_counter = 0


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
