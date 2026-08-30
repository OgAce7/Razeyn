"""
Recovery action adapter -- the boundary between "a policy decision was
approved" and "something actually happened."

Two things this file guarantees by construction:

1. **No arbitrary API operations.** The adapter interface exposes exactly
   four fixed methods, one per actionable recovery action
   (RETRY_ELIGIBLE_PAYMENTS, SEND_RECOVERY_LINK, OFFER_ALTERNATE_METHOD,
   NOTIFY_MERCHANT). There is no generic `execute(operation_name, **kwargs)`
   method that a caller (or, transitively, a compromised/hallucinating AI
   response) could point at an arbitrary operation string. The executor
   (executor.py) dispatches to one of these four methods via a fixed,
   hardcoded mapping -- not by dynamically resolving a method name from
   input data.

2. **No arbitrary monetary values.** Every method's `amount` parameter is
   typed and comes from the caller (executor.py, which in turn only ever
   passes amounts read from the `transactions` list given to the policy
   engine -- real transaction records, never a number out of the AI's
   output). Nothing here accepts a free-form "amount to charge/refund."

This project has no network access to Razorpay's test-mode API from its
sandboxed dev/test environment (razorpay.com is not in the allowlisted
domains), so `SimulatedAdapter` is the only implementation provided --
per the brief's fallback instruction, this is a deterministic simulation
layer with clearly documented behavior, not a live integration. Swapping
in a real adapter later (e.g. `RazorpayTestModeAdapter`) means
implementing this same `RecoveryActionAdapter` interface; nothing else in
the policy/executor layer would need to change.
"""

from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class SimResult:
    success: bool
    detail: str
    raw: dict


class RecoveryActionAdapter(ABC):
    """Fixed, minimal interface. Every recovery action executable by this
    system maps to exactly one of these methods -- see executor.py."""

    @abstractmethod
    def retry_payment(self, transaction_id: str, amount: float) -> SimResult: ...

    @abstractmethod
    def send_recovery_link(self, transaction_id: str, customer_id: str, amount: float) -> SimResult: ...

    @abstractmethod
    def offer_alternate_method(self, transaction_id: str, customer_id: str, amount: float) -> SimResult: ...

    @abstractmethod
    def notify_merchant(self, incident_id: str, message: str) -> SimResult: ...


def _deterministic_outcome(seed_string: str, success_rate_pct: int) -> bool:
    """Deterministic, seeded pseudo-outcome: hash the seed string to a
    stable 0-99 value and compare against the configured success rate.
    Same input always produces the same simulated outcome -- this is a
    SIMULATION, not a random/real result, and is documented as such
    everywhere it's used."""
    digest = hashlib.sha256(seed_string.encode("utf-8")).hexdigest()
    bucket = int(digest[:8], 16) % 100
    return bucket < success_rate_pct


class SimulatedAdapter(RecoveryActionAdapter):
    """Deterministic test-mode simulation. No network calls, no real
    payment/messaging system involved. Documented assumptions:

    - RETRY_ELIGIBLE_PAYMENTS: simulated success rate 55% per transaction
      (a plausible retry-recovery rate for transient failures, roughly in
      line with the synthetic corpus's own recovery-outcome documents --
      see app/retrieval/corpus/unstructured_evidence.json -- NOT derived
      from them programmatically, just chosen to be a similar order of
      magnitude for a believable demo).
    - SEND_RECOVERY_LINK / OFFER_ALTERNATE_METHOD: simulates only the
      DELIVERY step (was the link/prompt successfully sent to the
      customer), not whether the customer goes on to complete payment --
      modeling subsequent customer behavior over time is out of scope for
      this layer. Simulated delivery success rate: 97% (an SMS/email/
      in-app delivery failure rate, not a payment outcome).
    - NOTIFY_MERCHANT: always succeeds (an internal notification channel
      is assumed reliable; this action carries no financial risk).

    Every outcome is deterministic given the same transaction_id, so
    repeated test runs are fully reproducible.
    """

    RETRY_SUCCESS_RATE_PCT = 55
    DELIVERY_SUCCESS_RATE_PCT = 97

    def retry_payment(self, transaction_id: str, amount: float) -> SimResult:
        success = _deterministic_outcome(f"retry:{transaction_id}", self.RETRY_SUCCESS_RATE_PCT)
        return SimResult(
            success=success,
            detail=(
                f"[SIMULATED] Retry {'succeeded' if success else 'failed'} for {transaction_id} "
                f"(amount {amount})."
            ),
            raw={
                "mode": "simulated",
                "transaction_id": transaction_id,
                "amount": amount,
                "outcome": "SUCCESS" if success else "FAILED",
            },
        )

    def send_recovery_link(self, transaction_id: str, customer_id: str, amount: float) -> SimResult:
        success = _deterministic_outcome(f"link:{transaction_id}", self.DELIVERY_SUCCESS_RATE_PCT)
        return SimResult(
            success=success,
            detail=(
                f"[SIMULATED] Recovery link {'delivered' if success else 'delivery failed'} "
                f"for {transaction_id} to {customer_id}."
            ),
            raw={
                "mode": "simulated",
                "transaction_id": transaction_id,
                "customer_id": customer_id,
                "amount": amount,
                "outcome": "DELIVERED" if success else "DELIVERY_FAILED",
            },
        )

    def offer_alternate_method(self, transaction_id: str, customer_id: str, amount: float) -> SimResult:
        success = _deterministic_outcome(f"alt_method:{transaction_id}", self.DELIVERY_SUCCESS_RATE_PCT)
        return SimResult(
            success=success,
            detail=(
                f"[SIMULATED] Alternate-method prompt {'delivered' if success else 'delivery failed'} "
                f"for {transaction_id} to {customer_id}."
            ),
            raw={
                "mode": "simulated",
                "transaction_id": transaction_id,
                "customer_id": customer_id,
                "amount": amount,
                "outcome": "DELIVERED" if success else "DELIVERY_FAILED",
            },
        )

    def notify_merchant(self, incident_id: str, message: str) -> SimResult:
        return SimResult(
            success=True,
            detail=f"[SIMULATED] Merchant notified for incident {incident_id}.",
            raw={"mode": "simulated", "incident_id": incident_id, "message": message, "outcome": "DELIVERED"},
        )
