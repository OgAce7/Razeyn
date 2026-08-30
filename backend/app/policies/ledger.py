"""
Action records and the ledger that stores them.

`ActionRecord` is the audit-style record required by the brief for every
action decision (executed, rejected, escalated, or stopped). `ActionLedger`
is what the policy engine queries to answer "how many times has this
transaction already been retried," "when was the last action on this
transaction," and "how many times has this customer been contacted for
this incident" -- the state that makes retry limits, cooldowns, and
contact limits actually enforceable across repeated calls.

This is an in-memory, injectable ledger (a plain Python list under the
hood) -- deliberately not wired to a database. Persistent storage is
`app/audit/`'s job (not yet built); this module only needs the ledger to
be queryable within a process/test, and callers can pass their own
ledger instance (or persist `ActionRecord`s elsewhere and rebuild a
ledger from them) once that piece exists.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

_id_counter = itertools.count(1)


def _next_action_id() -> str:
    return f"act_{next(_id_counter):05d}"


def reset_id_counter() -> None:
    """Reset the action-id counter. Mainly useful for deterministic tests."""
    global _id_counter
    _id_counter = itertools.count(1)


@dataclass
class PolicyCheckResult:
    name: str
    passed: bool
    detail: str

    def to_dict(self) -> dict:
        return {"name": self.name, "passed": self.passed, "detail": self.detail}


@dataclass
class ActionRecord:
    """The full audit-style record for one policy decision + (attempted)
    execution. Every field required by the brief is present."""

    action_id: str
    incident_id: str
    transaction_ids: list[str]
    requested_action: str
    approved: bool
    reason: str
    timestamp: str
    expected_revenue_recovery: float
    actual_result: dict[str, Any]
    policy_checks: list[dict]
    escalation_required: bool
    execution_status: str  # see executor.py for the fixed set of values

    def to_dict(self) -> dict:
        return {
            "action_id": self.action_id,
            "incident_id": self.incident_id,
            "transaction_ids": self.transaction_ids,
            "requested_action": self.requested_action,
            "approved": self.approved,
            "reason": self.reason,
            "timestamp": self.timestamp,
            "expected_revenue_recovery": self.expected_revenue_recovery,
            "actual_result": self.actual_result,
            "policy_checks": self.policy_checks,
            "escalation_required": self.escalation_required,
            "execution_status": self.execution_status,
        }


def new_action_id() -> str:
    return _next_action_id()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ActionLedger:
    """In-memory store of ActionRecords, with the specific lookups the
    policy engine needs. A fresh instance has no history -- construct one
    per incident-response session, or pre-populate `records` from
    persisted data once that exists."""

    records: list[ActionRecord] = field(default_factory=list)

    def record(self, action_record: ActionRecord) -> None:
        self.records.append(action_record)

    def _executed_records_for_transaction(self, transaction_id: str) -> list[ActionRecord]:
        return [
            r
            for r in self.records
            if transaction_id in r.transaction_ids and r.execution_status in ("EXECUTED", "SIMULATED")
        ]

    def retry_count(self, transaction_id: str, action: str = "RETRY_ELIGIBLE_PAYMENTS") -> int:
        return sum(
            1
            for r in self._executed_records_for_transaction(transaction_id)
            if r.requested_action == action
        )

    def total_retries_for_incident(self, incident_id: str, action: str = "RETRY_ELIGIBLE_PAYMENTS") -> int:
        return sum(
            len(r.transaction_ids)
            for r in self.records
            if r.incident_id == incident_id
            and r.requested_action == action
            and r.execution_status in ("EXECUTED", "SIMULATED")
        )

    def last_action_time(self, transaction_id: str) -> datetime | None:
        times = [
            datetime.fromisoformat(r.timestamp)
            for r in self._executed_records_for_transaction(transaction_id)
        ]
        return max(times) if times else None

    def contact_count(self, customer_id: str, incident_id: str, contact_actions: frozenset) -> int:
        count = 0
        for r in self.records:
            if r.incident_id != incident_id or r.execution_status not in ("EXECUTED", "SIMULATED"):
                continue
            if r.requested_action not in contact_actions:
                continue
            count += r.actual_result.get("customer_ids_contacted", []).count(customer_id)
        return count
