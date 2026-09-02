"""
In-process application state for the API layer.

Deliberately NOT a database. Real SQLAlchemy persistence was scoped out
for this pass (see plan discussion) -- modeling the nested frozen
dataclasses in app/audit/schema.py and app/policies/ledger.py as
relational tables, and making the double-recovery guardrails
(ActionLedger.already_succeeded / retry_count / contact_count) correct
under real concurrent DB access, is a redesign of the safety-critical
part of this system, not something to rush in a couple of days without
requalifying those guarantees. This module is the explicit, single
source of truth for that decision: everything here lives in memory for
the lifetime of one server process and is lost on restart. That's an
acceptable, clearly-flagged trade-off for a buildathon demo.

Exactly ONE instance of AppState should exist per running server
process (see app/main.py, where it's built once at startup and attached
to `app.state.app_state`). This matters for correctness, not just
convenience: ActionLedger's retry/cooldown/contact-limit checks and
AuditStore's record history only mean anything if every request reads
and writes the SAME ledger/store instance. Constructing a fresh
ActionLedger per request (as run_batch_evaluation does for a one-shot
batch job) would silently disable every one of those protections.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from app.audit.store import AuditStore
from app.policies.engine import PolicyDecision
from app.policies.ledger import ActionLedger


@dataclass
class PendingDecision:
    """Everything needed to resolve an escalated incident via
    approve/reject, WITHOUT re-running evaluate_policy. Holding onto the
    original PolicyDecision object (not a re-derived one) is what makes
    approve/reject safe -- see app/api/incidents.py's module docstring
    for the full reasoning.
    """

    incident_id: str
    requested_action: str
    policy_decision: PolicyDecision
    incident: dict[str, Any]
    transactions: list[dict[str, Any]]
    audit_record_id: str  # the escalated AuditRecord this will be resolved from


class IncidentAlreadyResolvedError(Exception):
    """Raised when a decision is requested for an incident that has no
    pending escalation (already resolved, or never escalated)."""


@dataclass
class AppState:
    ledger: ActionLedger = field(default_factory=ActionLedger)
    audit_store: AuditStore = field(default_factory=AuditStore)
    pending: dict[str, PendingDecision] = field(default_factory=dict)
    active_dataset_label: str = "seeded synthetic dataset"

    # One lock per incident_id, created lazily, so concurrent
    # approve/reject calls for DIFFERENT incidents never block each
    # other, but two calls for the SAME incident_id are always
    # serialized -- this is what actually prevents a double-click or a
    # retried request from calling execute_action twice for one
    # pending decision.
    _locks: dict[str, asyncio.Lock] = field(default_factory=dict)

    def lock_for(self, incident_id: str) -> asyncio.Lock:
        lock = self._locks.get(incident_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[incident_id] = lock
        return lock

    def add_pending(self, pending: PendingDecision) -> None:
        self.pending[pending.incident_id] = pending

    def pop_pending(self, incident_id: str) -> PendingDecision:
        pending = self.pending.get(incident_id)
        if pending is None:
            raise IncidentAlreadyResolvedError(
                f"No pending decision for incident {incident_id!r} -- it was "
                "never escalated, or has already been approved/rejected."
            )
        del self.pending[incident_id]
        return pending

    def latest_records_by_incident(self):
        """One AuditRecord per candidate_incident_id -- the most recent
        one, by created_at. A resolved incident has TWO AuditRecords
        (the original escalated one, and the new one produced by
        approve/reject); callers that want "the current state of this
        incident" should use this, not audit_store.all().
        """
        latest: dict[str, Any] = {}
        for record in self.audit_store.all():
            key = record.detection.candidate_incident_id
            existing = latest.get(key)
            if existing is None or record.created_at > existing.created_at:
                latest[key] = record
        return list(latest.values())


_app_state: AppState | None = None


def get_app_state() -> AppState:
    global _app_state
    if _app_state is None:
        _app_state = AppState()
    return _app_state


def reset_app_state() -> AppState:
    """Test-only helper: force a fresh AppState (fresh ledger, empty
    store, no pending decisions)."""
    global _app_state
    _app_state = AppState()
    return _app_state
