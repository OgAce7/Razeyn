"""
Tests for app/policies/adapter.py and the execution-dispatch logic in
app/policies/executor.py, focused specifically on:
  - the adapter's fixed, bounded interface (no arbitrary operations)
  - determinism of the simulation
  - the executor never passing anything but real transaction amounts
    through to the adapter (never an AI-supplied figure)
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.policies.adapter import RecoveryActionAdapter, SimulatedAdapter
from app.policies.engine import evaluate_policy
from app.policies.executor import execute_action
from app.policies.ledger import ActionLedger, reset_id_counter

NOW = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)


def test_simulated_adapter_implements_the_full_fixed_interface():
    adapter = SimulatedAdapter()
    assert isinstance(adapter, RecoveryActionAdapter)
    # Exactly these four methods -- the fixed, bounded operation set.
    for method in ("retry_payment", "send_recovery_link", "offer_alternate_method", "notify_merchant"):
        assert hasattr(adapter, method)


def test_simulated_outcomes_are_deterministic():
    adapter = SimulatedAdapter()
    r1 = adapter.retry_payment("txn_abc", 500.0)
    r2 = adapter.retry_payment("txn_abc", 500.0)
    assert r1.success == r2.success
    assert r1.raw == r2.raw


def test_simulated_outcomes_differ_by_transaction_id():
    """Not a strict guarantee for any two arbitrary ids, but across many
    ids we should see both outcomes given a ~55% success rate -- confirms
    the simulation isn't just always-true or always-false."""
    adapter = SimulatedAdapter()
    outcomes = {adapter.retry_payment(f"txn_{i}", 100.0).success for i in range(50)}
    assert outcomes == {True, False}


def test_simulation_is_clearly_marked_as_simulated():
    adapter = SimulatedAdapter()
    result = adapter.retry_payment("txn_x", 100.0)
    assert result.raw["mode"] == "simulated"
    assert "[SIMULATED]" in result.detail


def test_notify_merchant_always_succeeds():
    adapter = SimulatedAdapter()
    for i in range(20):
        result = adapter.notify_merchant(f"cand_{i}", "test message")
        assert result.success is True


# --------------------------------------------------------------------------
# Executor never lets amounts flow from anywhere but the transaction records
# --------------------------------------------------------------------------

class _RecordingAdapter(RecoveryActionAdapter):
    """Spy adapter that records exactly what it was called with, so tests
    can assert on the values the executor actually passed through."""

    def __init__(self):
        self.retry_calls = []
        self.link_calls = []
        self.alt_calls = []
        self.notify_calls = []

    def retry_payment(self, transaction_id, amount):
        self.retry_calls.append((transaction_id, amount))
        from app.policies.adapter import SimResult
        return SimResult(True, "ok", {"mode": "test", "outcome": "SUCCESS"})

    def send_recovery_link(self, transaction_id, customer_id, amount):
        self.link_calls.append((transaction_id, customer_id, amount))
        from app.policies.adapter import SimResult
        return SimResult(True, "ok", {"mode": "test", "outcome": "DELIVERED"})

    def offer_alternate_method(self, transaction_id, customer_id, amount):
        self.alt_calls.append((transaction_id, customer_id, amount))
        from app.policies.adapter import SimResult
        return SimResult(True, "ok", {"mode": "test", "outcome": "DELIVERED"})

    def notify_merchant(self, incident_id, message):
        self.notify_calls.append((incident_id, message))
        from app.policies.adapter import SimResult
        return SimResult(True, "ok", {"mode": "test", "outcome": "DELIVERED"})


def test_executor_passes_only_real_transaction_amounts_to_adapter():
    reset_id_counter()
    ledger = ActionLedger()
    incident = {"incident_id": "cand_x", "severity": "HIGH"}
    txns = [
        {"transaction_id": "txn_1", "amount": 314.15, "customer_id": "cust_1", "status": "FAILED"},
        {"transaction_id": "txn_2", "amount": 271.83, "customer_id": "cust_2", "status": "FAILED"},
    ]
    spy = _RecordingAdapter()

    decision = evaluate_policy(
        "RETRY_ELIGIBLE_PAYMENTS", incident, txns, confidence=0.9,
        revenue_at_risk=99999999.0,  # a wildly different "invented" AI figure
        ledger=ledger, now=NOW,
    )
    execute_action("RETRY_ELIGIBLE_PAYMENTS", decision, incident, txns, ledger, adapter=spy, now=NOW)

    called_amounts = {amount for _txn_id, amount in spy.retry_calls}
    assert called_amounts == {314.15, 271.83}  # exactly the real transaction amounts
    assert 99999999.0 not in called_amounts


def test_executor_dispatch_is_a_fixed_mapping_not_dynamic_lookup():
    """The dispatch in executor.py is a hardcoded if/elif over known
    action names -- confirm an action name that happens to match an
    unrelated adapter/Python attribute name cannot be used to invoke
    anything unexpected. (There is no getattr(adapter, action) path at
    all; this test documents and pins that invariant.)"""
    reset_id_counter()
    ledger = ActionLedger()
    incident = {"incident_id": "cand_x", "severity": "HIGH"}
    txns = [{"transaction_id": "txn_1", "amount": 100.0, "customer_id": "cust_1", "status": "FAILED"}]
    spy = _RecordingAdapter()

    # Try a string that looks like it could resolve to a private/dunder
    # attribute if dispatch were ever implemented via getattr(...).
    decision = evaluate_policy("__class__", incident, txns, 0.9, 100.0, ledger, now=NOW)
    assert decision.approved is False  # rejected at the action_supported check
    record = execute_action("__class__", decision, incident, txns, ledger, adapter=spy, now=NOW)
    assert record.execution_status == "NOT_EXECUTED_REJECTED"
    assert spy.retry_calls == spy.link_calls == spy.alt_calls == spy.notify_calls == []


def test_send_recovery_link_uses_real_customer_id_from_transactions():
    reset_id_counter()
    ledger = ActionLedger()
    incident = {"incident_id": "cand_x", "severity": "HIGH"}
    txns = [{"transaction_id": "txn_1", "amount": 100.0, "customer_id": "cust_real", "status": "FAILED"}]
    spy = _RecordingAdapter()

    decision = evaluate_policy("SEND_RECOVERY_LINK", incident, txns, 0.9, 100.0, ledger, now=NOW)
    execute_action("SEND_RECOVERY_LINK", decision, incident, txns, ledger, adapter=spy, now=NOW)

    assert spy.link_calls == [("txn_1", "cust_real", 100.0)]


def test_notify_merchant_calls_adapter_exactly_once_regardless_of_transaction_count():
    reset_id_counter()
    ledger = ActionLedger()
    incident = {"incident_id": "cand_x", "severity": "HIGH"}
    txns = [
        {"transaction_id": f"txn_{i}", "amount": 100.0, "customer_id": f"cust_{i}", "status": "FAILED"}
        for i in range(5)
    ]
    spy = _RecordingAdapter()

    decision = evaluate_policy("NOTIFY_MERCHANT", incident, txns, 0.9, 500.0, ledger, now=NOW)
    execute_action("NOTIFY_MERCHANT", decision, incident, txns, ledger, adapter=spy, now=NOW)

    assert len(spy.notify_calls) == 1
    assert spy.retry_calls == spy.link_calls == spy.alt_calls == []
