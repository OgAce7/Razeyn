"""
Baseline recovery strategy -- a simple, deterministic rule the AI agent
must be compared against, so "the agent recovered $X" means something
("...versus $Y a naive fixed rule would have recovered without any AI,
any evidence, or any diagnosis").

Rule (`FIXED_RETRY_RULE`): for every transaction in a detected incident's
window, retry it if and only if:
  1. status == FAILED, and
  2. failure_reason is in a fixed "plausibly transient" set
     (BANK_TIMEOUT, NETWORK_ERROR, GATEWAY_ERROR) -- no bank-specific,
     segment-specific, or evidence-based reasoning, and
  3. amount is within a fixed eligible range (same bounds as the policy
     engine's default `min_eligible_amount` / `max_eligible_amount`, so
     the comparison isn't stacked against the baseline by giving the
     agent a wider action space than the baseline is allowed).

This mirrors exactly one of the seven policies the real system enforces
(transient-reason retry) and ignores the rest (no cooldown, no
per-customer contact limit, no confidence/severity gating, no evidence,
no diagnosis) -- intentionally, since the point of a baseline is that
it's what you'd get *without* any of that.

Outcome simulation reuses the SAME `SimulatedAdapter` the real executor
uses (`app/policies/adapter.py`), so a transaction's simulated
success/failure is identical whether it's retried by the baseline or by
the agent+policy pipeline -- the comparison isolates "which transactions
got selected for retry," not "which simulator was kinder."
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.policies.adapter import RecoveryActionAdapter, SimulatedAdapter

# Same bounds as PolicyConfig defaults (app/policies/config.py) -- kept as
# independent constants here (not imported) so the baseline's definition
# doesn't silently drift if the real policy's defaults are ever tuned;
# a baseline is only a fair comparison if it stays fixed.
BASELINE_MIN_ELIGIBLE_AMOUNT = 10.0
BASELINE_MAX_ELIGIBLE_AMOUNT = 5000.0
BASELINE_TRANSIENT_REASONS = frozenset({"BANK_TIMEOUT", "NETWORK_ERROR", "GATEWAY_ERROR"})


@dataclass(frozen=True)
class BaselineOutcome:
    incident_id: str
    eligible_transaction_ids: tuple[str, ...]
    attempted: int
    succeeded: int
    failed: int
    revenue_recovered: float
    revenue_attempted: float
    per_transaction: tuple[dict[str, Any], ...]


def select_baseline_eligible(transactions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Pure selection logic, no simulation -- returns the transaction dicts
    the fixed rule would retry. Split out from `run_baseline` so tests can
    check selection and outcome independently."""
    return [
        t
        for t in transactions
        if t.get("status") == "FAILED"
        and t.get("failure_reason") in BASELINE_TRANSIENT_REASONS
        and BASELINE_MIN_ELIGIBLE_AMOUNT <= float(t.get("amount", 0.0)) <= BASELINE_MAX_ELIGIBLE_AMOUNT
    ]


def run_baseline(
    incident_id: str,
    transactions: list[dict[str, Any]],
    adapter: RecoveryActionAdapter | None = None,
) -> BaselineOutcome:
    """Apply the fixed retry rule to `transactions` (the incident-window
    transactions -- caller decides scoping, same as the real pipeline) and
    simulate the outcome via `adapter` (defaults to the same
    `SimulatedAdapter` the real executor uses).

    Deterministic: `SimulatedAdapter.retry_payment` is a seeded hash of
    `transaction_id`, not `random` (see app/policies/adapter.py), so
    calling this twice on the same input produces identical results.
    """
    adapter = adapter or SimulatedAdapter()
    eligible = select_baseline_eligible(transactions)

    per_txn = []
    succeeded = 0
    revenue_recovered = 0.0
    revenue_attempted = 0.0

    for txn in eligible:
        amount = float(txn["amount"])
        result = adapter.retry_payment(txn["transaction_id"], amount)
        revenue_attempted += amount
        per_txn.append({"transaction_id": txn["transaction_id"], "amount": amount, **result.raw})
        if result.success:
            succeeded += 1
            revenue_recovered += amount

    return BaselineOutcome(
        incident_id=incident_id,
        eligible_transaction_ids=tuple(t["transaction_id"] for t in eligible),
        attempted=len(eligible),
        succeeded=succeeded,
        failed=len(eligible) - succeeded,
        revenue_recovered=round(revenue_recovered, 2),
        revenue_attempted=round(revenue_attempted, 2),
        per_transaction=tuple(per_txn),
    )
