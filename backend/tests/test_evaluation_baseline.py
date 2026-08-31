"""
Tests for app/evaluation/baseline.py -- the fixed-rule baseline used to
compare against the AI agent's recovered revenue.
"""

from __future__ import annotations

from app.evaluation.baseline import (
    BASELINE_MAX_ELIGIBLE_AMOUNT,
    BASELINE_MIN_ELIGIBLE_AMOUNT,
    select_baseline_eligible,
    run_baseline,
)
from app.policies.adapter import RecoveryActionAdapter, SimResult, SimulatedAdapter


def make_txn(**overrides) -> dict:
    base = {
        "transaction_id": "txn_1",
        "amount": 500.0,
        "status": "FAILED",
        "failure_reason": "BANK_TIMEOUT",
        "customer_id": "cust_1",
    }
    base.update(overrides)
    return base


# --------------------------------------------------------------------------
# Selection logic
# --------------------------------------------------------------------------


def test_selects_failed_transient_reason_in_amount_range():
    txns = [make_txn(transaction_id="txn_1")]
    eligible = select_baseline_eligible(txns)
    assert len(eligible) == 1
    assert eligible[0]["transaction_id"] == "txn_1"


def test_excludes_successful_transactions():
    txns = [make_txn(transaction_id="txn_1", status="SUCCESS")]
    assert select_baseline_eligible(txns) == []


def test_excludes_non_transient_failure_reasons():
    txns = [make_txn(transaction_id="txn_1", failure_reason="INSUFFICIENT_FUNDS")]
    assert select_baseline_eligible(txns) == []
    txns2 = [make_txn(transaction_id="txn_2", failure_reason="RISK_DECLINE")]
    assert select_baseline_eligible(txns2) == []
    txns3 = [make_txn(transaction_id="txn_3", failure_reason="INVALID_OTP")]
    assert select_baseline_eligible(txns3) == []


def test_includes_all_three_transient_reasons():
    for reason in ("BANK_TIMEOUT", "NETWORK_ERROR", "GATEWAY_ERROR"):
        txns = [make_txn(transaction_id=f"txn_{reason}", failure_reason=reason)]
        assert len(select_baseline_eligible(txns)) == 1


def test_excludes_amount_below_minimum():
    txns = [make_txn(transaction_id="txn_1", amount=BASELINE_MIN_ELIGIBLE_AMOUNT - 1)]
    assert select_baseline_eligible(txns) == []


def test_excludes_amount_above_maximum():
    txns = [make_txn(transaction_id="txn_1", amount=BASELINE_MAX_ELIGIBLE_AMOUNT + 1)]
    assert select_baseline_eligible(txns) == []


def test_includes_amount_at_exact_boundaries():
    txns = [
        make_txn(transaction_id="txn_min", amount=BASELINE_MIN_ELIGIBLE_AMOUNT),
        make_txn(transaction_id="txn_max", amount=BASELINE_MAX_ELIGIBLE_AMOUNT),
    ]
    eligible = select_baseline_eligible(txns)
    assert {t["transaction_id"] for t in eligible} == {"txn_min", "txn_max"}


def test_mixed_batch_selects_only_eligible_subset():
    txns = [
        make_txn(transaction_id="txn_1"),  # eligible
        make_txn(transaction_id="txn_2", status="SUCCESS"),  # not failed
        make_txn(transaction_id="txn_3", failure_reason="RISK_DECLINE"),  # not transient
        make_txn(transaction_id="txn_4", amount=99999.0),  # too large
    ]
    eligible = select_baseline_eligible(txns)
    assert {t["transaction_id"] for t in eligible} == {"txn_1"}


# --------------------------------------------------------------------------
# Outcome simulation / determinism
# --------------------------------------------------------------------------


def test_run_baseline_is_deterministic_across_calls():
    txns = [make_txn(transaction_id=f"txn_{i}") for i in range(10)]
    r1 = run_baseline("cand_1", txns)
    r2 = run_baseline("cand_1", txns)
    assert r1 == r2


def test_run_baseline_uses_same_simulated_adapter_semantics_as_executor():
    """The baseline and the real executor must use the SAME adapter
    seeding convention (retry:{transaction_id}) so the comparison isn't
    biased by different simulators."""
    txns = [make_txn(transaction_id="txn_compare")]
    baseline_result = run_baseline("cand_1", txns)
    adapter = SimulatedAdapter()
    direct_result = adapter.retry_payment("txn_compare", 500.0)
    assert baseline_result.succeeded == (1 if direct_result.success else 0)


def test_run_baseline_revenue_recovered_only_counts_successes():
    class _AlwaysFailAdapter(RecoveryActionAdapter):
        def retry_payment(self, transaction_id, amount):
            return SimResult(False, "fail", {"outcome": "FAILED"})

        def send_recovery_link(self, transaction_id, customer_id, amount):
            raise NotImplementedError

        def offer_alternate_method(self, transaction_id, customer_id, amount):
            raise NotImplementedError

        def notify_merchant(self, incident_id, message):
            raise NotImplementedError

    txns = [make_txn(transaction_id="txn_1", amount=500.0)]
    result = run_baseline("cand_1", txns, adapter=_AlwaysFailAdapter())
    assert result.attempted == 1
    assert result.succeeded == 0
    assert result.revenue_recovered == 0.0
    assert result.revenue_attempted == 500.0


def test_run_baseline_revenue_recovered_sums_only_successful_amounts():
    class _AlwaysSucceedAdapter(RecoveryActionAdapter):
        def retry_payment(self, transaction_id, amount):
            return SimResult(True, "ok", {"outcome": "SUCCESS"})

        def send_recovery_link(self, transaction_id, customer_id, amount):
            raise NotImplementedError

        def offer_alternate_method(self, transaction_id, customer_id, amount):
            raise NotImplementedError

        def notify_merchant(self, incident_id, message):
            raise NotImplementedError

    txns = [
        make_txn(transaction_id="txn_1", amount=100.0),
        make_txn(transaction_id="txn_2", amount=250.0),
    ]
    result = run_baseline("cand_1", txns, adapter=_AlwaysSucceedAdapter())
    assert result.succeeded == 2
    assert result.revenue_recovered == 350.0


def test_run_baseline_empty_transactions_returns_zeroed_outcome():
    result = run_baseline("cand_1", [])
    assert result.attempted == 0
    assert result.succeeded == 0
    assert result.revenue_recovered == 0.0
    assert result.eligible_transaction_ids == ()
