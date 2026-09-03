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

import pandas as pd

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
class DatasetInfo:
    """Metadata about one dataset the pipeline has been run against --
    either the seeded synthetic dataset or an uploaded CSV. Used for
    display/selection in the UI (GET /api/datasets). The actual
    transaction rows and candidate incidents for the ACTIVE dataset are
    held separately on AppState (active_transactions_df /
    active_candidates_by_id) -- see those fields' docstring for why.
    """

    dataset_id: str
    label: str
    kind: str  # "seeded" | "uploaded"
    row_count: int
    candidate_count: int
    uploaded_at: str | None = None
    original_filename: str | None = None


@dataclass
class AppState:
    ledger: ActionLedger = field(default_factory=ActionLedger)
    audit_store: AuditStore = field(default_factory=AuditStore)
    pending: dict[str, PendingDecision] = field(default_factory=dict)
    active_dataset_label: str = "seeded synthetic dataset"

    # Populated alongside each AuditRecord as it's built (see
    # app/api/pipeline.py and app/api/incidents.py) -- keyed by
    # AuditRecord.record_id. This is what GET /api/evaluation/report
    # (app/api/evaluation.py) needs to compute revenue metrics, and it's
    # captured at the one point in the pipeline where the exact
    # per-transaction outcome is still available (the ActionRecord,
    # before it's compressed into an AuditRecord) -- see
    # compute_exact_revenue_recovered in app/evaluation/metrics.py.
    # AppState never needs to retain the full transactions dataset to
    # get this number: it's computed once, at creation time, and cached
    # here as a plain float from then on.
    revenue_recovered_by_record: dict[str, float] = field(default_factory=dict)

    # One BaselineOutcome per candidate incident (see
    # app/evaluation/baseline.py) -- what a naive fixed-rule retry
    # policy would have recovered for the same incident, computed
    # alongside the real pipeline run using the same window transactions
    # (also available in-loop, not retained afterward). Powers the
    # "vs fixed-rule baseline" comparison in the evaluation report.
    baseline_outcomes: list = field(default_factory=list)

    # The active dataset's transactions and candidate incidents, kept
    # in memory for the lifetime of this dataset being active -- needed
    # to serve GET /api/evidence/{incident_id} on demand (evidence
    # retrieval needs the full transactions DataFrame plus the specific
    # candidate dict; see app/retrieval/bundle.retrieve_evidence_for_incident).
    #
    # This WAS deliberately left out of AppState earlier (see
    # DatasetInfo's docstring, written when this field didn't exist) on
    # the reasoning that holding a full dataset in memory should be
    # avoided. That reasoning didn't weigh the actual cost: this
    # project's datasets are capped at 50k rows by the upload validator
    # (app/data/validate_upload.py), which is a few MB at most -- trivial
    # to hold for one active dataset, and holding it is what actually
    # allows evidence to be re-computed on demand instead of only once
    # at pipeline-run time. Still fully discarded on the next
    # swap_dataset() call, so at most one dataset's worth is ever held.
    active_transactions_df: pd.DataFrame | None = None
    active_candidates_by_id: dict[str, dict[str, Any]] = field(default_factory=dict)

    # The dataset currently backing ledger/audit_store/pending above.
    active_dataset: DatasetInfo | None = None

    # Every dataset run so far this process, most recent first, INCLUDING
    # the currently active one -- lets the UI list "seeded" plus any
    # uploads and switch back to one that was already run without
    # re-uploading or re-running detection. Keyed by dataset_id.
    dataset_history: dict[str, DatasetInfo] = field(default_factory=dict)

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

    def swap_dataset(self, info: DatasetInfo) -> None:
        """Replace the active ledger/audit_store/pending with a fresh,
        empty set for a newly-run dataset, and make `info` the active
        one. This is a full replacement, not a merge -- per the product
        decision that only one dataset is "live" in the dashboard at a
        time (the seeded set, or the most recently selected upload),
        never both at once. Existing pending escalations from the
        previous dataset are discarded along with it: their underlying
        transactions/candidates no longer correspond to what's active,
        so there is nothing safe to resolve them against.

        Per-incident locks are intentionally NOT cleared -- they're
        keyed by incident_id and harmless to keep around; clearing them
        while a decision might be in flight for the outgoing dataset
        would remove the very serialization that protects it.
        """
        self.ledger = ActionLedger()
        self.audit_store = AuditStore()
        self.pending = {}
        self.revenue_recovered_by_record = {}
        self.baseline_outcomes = []
        self.active_transactions_df = None
        self.active_candidates_by_id = {}
        self.active_dataset = info
        self.active_dataset_label = info.label
        self.dataset_history[info.dataset_id] = info


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
