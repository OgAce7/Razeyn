"""
The bounded action executor.

Takes a PolicyDecision (already approved or rejected by engine.py) and:
  - if rejected, escalated, or a no-op (STOP/WAIT_AND_REASSESS) -> records
    an ActionRecord with no adapter call at all.
  - if approved AND not requiring escalation/merchant approval -> calls
    the adapter (adapter.py) for each eligible transaction, using ONLY
    amounts/customer_ids taken from the `transactions` list -- never any
    value from the AI's output -- and records the aggregate result.

This is the ONLY place in the whole project that calls
RecoveryActionAdapter methods. The dispatch from action name -> adapter
method is a fixed, hardcoded if/elif chain over the four known
actionable actions -- there is no dynamic getattr(adapter, action_name)
or similar, so an action string can never be used to invoke an arbitrary
method on the adapter even if the earlier validation layers were somehow
bypassed.
"""

from __future__ import annotations

from app.policies.adapter import RecoveryActionAdapter, SimulatedAdapter
from app.policies.engine import PolicyDecision
from app.policies.ledger import ActionLedger, ActionRecord, new_action_id, now_iso

EXECUTION_EXECUTED = "EXECUTED"
EXECUTION_SIMULATED = "SIMULATED"
EXECUTION_NOT_EXECUTED_REJECTED = "NOT_EXECUTED_REJECTED"
EXECUTION_NOT_EXECUTED_ESCALATED = "NOT_EXECUTED_ESCALATED"
EXECUTION_NOT_EXECUTED_STOPPED = "NOT_EXECUTED_STOPPED"
EXECUTION_NOT_EXECUTED_WAIT = "NOT_EXECUTED_WAIT"

_DEFAULT_ADAPTER = SimulatedAdapter()


def execute_action(
    requested_action: str,
    decision: PolicyDecision,
    incident: dict,
    transactions: list,
    ledger: ActionLedger,
    adapter: RecoveryActionAdapter | None = None,
    now=None,
) -> ActionRecord:
    """Execute (or simulate, or refuse to execute) one policy decision and
    record the outcome. Always returns an ActionRecord and always appends
    it to `ledger` -- callers get one consistent object back regardless
    of which branch was taken.

    `now`: injectable timestamp (datetime) for deterministic testing --
    MUST be the same clock passed to `evaluate_policy` for that call, or
    cooldown/retry-timing math computed from the ledger will be
    inconsistent. Defaults to real UTC now."""
    adapter = adapter or _DEFAULT_ADAPTER
    timestamp = now.isoformat() if now is not None else now_iso()
    incident_id = incident.get("incident_id", "")
    txn_by_id = {t["transaction_id"]: t for t in transactions}

    # -- Not approved at all: rejected, nothing runs ----------------------------
    if not decision.approved:
        record = ActionRecord(
            action_id=new_action_id(),
            incident_id=incident_id,
            transaction_ids=[],
            requested_action=requested_action,
            approved=False,
            reason=decision.reason,
            timestamp=timestamp,
            expected_revenue_recovery=decision.expected_revenue_recovery,
            actual_result={"outcome": "NOT_EXECUTED", "detail": decision.reason},
            policy_checks=decision.checks_as_dicts(),
            escalation_required=decision.escalation_required,
            execution_status=EXECUTION_NOT_EXECUTED_REJECTED,
        )
        ledger.record(record)
        return record

    # -- Approved but no-op pass-through (STOP / WAIT_AND_REASSESS) --------------
    if requested_action == "STOP":
        return _record_no_op(requested_action, decision, incident_id, ledger, EXECUTION_NOT_EXECUTED_STOPPED, "Incident marked resolved/no-action; nothing executed.", timestamp)
    if requested_action == "WAIT_AND_REASSESS":
        return _record_no_op(requested_action, decision, incident_id, ledger, EXECUTION_NOT_EXECUTED_WAIT, "No action taken; scheduled for reassessment.", timestamp)

    # -- Approved but requires human/merchant sign-off before anything runs -----
    if decision.escalation_required:
        record = ActionRecord(
            action_id=new_action_id(),
            incident_id=incident_id,
            transaction_ids=decision.eligible_transaction_ids,
            requested_action=requested_action,
            approved=True,
            reason=decision.reason,
            timestamp=timestamp,
            expected_revenue_recovery=decision.expected_revenue_recovery,
            actual_result={"outcome": "PENDING_HUMAN_REVIEW", "detail": "Approved by policy but withheld pending escalation/merchant approval."},
            policy_checks=decision.checks_as_dicts(),
            escalation_required=True,
            execution_status=EXECUTION_NOT_EXECUTED_ESCALATED,
        )
        ledger.record(record)
        return record

    # -- Approved, no escalation needed: actually run the (simulated) action -----
    per_txn_results = []
    customer_ids_contacted = []
    success_count = 0

    for txn_id in decision.eligible_transaction_ids:
        txn = txn_by_id.get(txn_id)
        if txn is None:  # pragma: no cover - defensive, eligible ids always come from `transactions`
            continue
        amount = txn["amount"]  # <- the ONLY source of this value; never from the AI's output
        customer_id = txn.get("customer_id")

        if requested_action == "RETRY_ELIGIBLE_PAYMENTS":
            result = adapter.retry_payment(txn_id, amount)
        elif requested_action == "SEND_RECOVERY_LINK":
            result = adapter.send_recovery_link(txn_id, customer_id, amount)
            if result.success:
                customer_ids_contacted.append(customer_id)
        elif requested_action == "OFFER_ALTERNATE_METHOD":
            result = adapter.offer_alternate_method(txn_id, customer_id, amount)
            if result.success:
                customer_ids_contacted.append(customer_id)
        else:  # pragma: no cover - NOTIFY_MERCHANT handled separately below; nothing else reaches here
            continue

        per_txn_results.append({"transaction_id": txn_id, **result.raw})
        if result.success:
            success_count += 1

    if requested_action == "NOTIFY_MERCHANT":
        result = adapter.notify_merchant(incident_id, incident.get("observation", "Incident detected."))
        per_txn_results = [result.raw]
        success_count = 1 if result.success else 0

    total = len(decision.eligible_transaction_ids) if requested_action != "NOTIFY_MERCHANT" else 1
    actual_result = {
        "outcome": "COMPLETED",
        "attempted": total,
        "succeeded": success_count,
        "failed": total - success_count,
        "per_transaction": per_txn_results,
        "customer_ids_contacted": customer_ids_contacted,
    }

    record = ActionRecord(
        action_id=new_action_id(),
        incident_id=incident_id,
        transaction_ids=decision.eligible_transaction_ids,
        requested_action=requested_action,
        approved=True,
        reason=decision.reason,
        timestamp=timestamp,
        expected_revenue_recovery=decision.expected_revenue_recovery,
        actual_result=actual_result,
        policy_checks=decision.checks_as_dicts(),
        escalation_required=False,
        execution_status=EXECUTION_SIMULATED,
    )
    ledger.record(record)
    return record


def _record_no_op(requested_action, decision, incident_id, ledger, execution_status, detail, timestamp):
    record = ActionRecord(
        action_id=new_action_id(),
        incident_id=incident_id,
        transaction_ids=[],
        requested_action=requested_action,
        approved=True,
        reason=decision.reason,
        timestamp=timestamp,
        expected_revenue_recovery=0.0,
        actual_result={"outcome": "NO_ACTION", "detail": detail},
        policy_checks=decision.checks_as_dicts(),
        escalation_required=decision.escalation_required,
        execution_status=execution_status,
    )
    ledger.record(record)
    return record
