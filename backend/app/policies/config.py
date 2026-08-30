"""
Explicit policy configuration.

Every limit the guardrail layer enforces lives here as a named,
documented constant -- no LLM, no implicit behavior. This is the single
place to look to answer "what exactly is this system allowed to do
automatically, and where does it require a human?"

Merchants can tighten (never loosen) certain limits via
`merchant_policies` passed through from the agent's input (see
app/agent/schema.py's `AgentInput.merchant_policies`) -- see
`apply_merchant_overrides` below for exactly which fields are
overridable and in which direction.
"""

from __future__ import annotations

from dataclasses import dataclass, replace


@dataclass(frozen=True)
class PolicyConfig:
    # --- Retry limits ---------------------------------------------------
    max_retry_attempts_per_transaction: int = 3
    """A single failed transaction may be auto-retried at most this many
    times, ever. Prevents infinite/runaway retry loops on a transaction
    that simply will not succeed."""

    max_retry_attempts_per_incident: int = 300
    """Safety cap on total retries triggered by ONE incident's recovery
    action, regardless of how many individual transactions are eligible.
    Prevents a single mis-scoped or unusually large incident from
    generating an unbounded burst of automated activity."""

    # --- Transaction amount eligibility ----------------------------------
    min_eligible_amount: float = 10.0
    """Transactions below this amount (INR) are not eligible for automated
    recovery -- the operational cost of a retry/contact exceeds the
    revenue at stake."""

    max_eligible_amount: float = 5000.0
    """Transactions above this amount (INR) are never auto-actioned; they
    always require merchant approval (see
    `actions_requiring_merchant_approval` / the approval-ceiling check),
    regardless of how confident the AI recommendation was."""

    # --- Cooldown ----------------------------------------------------------
    cooldown_minutes: int = 30
    """Minimum time that must pass after any automated action on a given
    transaction before another automated action may be taken on it."""

    # --- Customer contact limits ---------------------------------------------
    max_customer_contacts_per_incident: int = 2
    """A given customer may be contacted (SEND_RECOVERY_LINK or
    OFFER_ALTERNATE_METHOD -- anything customer-facing) at most this many
    times within the scope of one incident, to avoid spamming someone
    whose payment is already failing."""

    contact_actions: frozenset = frozenset({"SEND_RECOVERY_LINK", "OFFER_ALTERNATE_METHOD"})
    """Which actions count as a "customer contact" for the limit above."""

    # --- Merchant approval ----------------------------------------------------
    actions_requiring_merchant_approval: frozenset = frozenset({"OFFER_ALTERNATE_METHOD"})
    """Actions that always require merchant sign-off before execution,
    regardless of amount or confidence -- e.g. changing the customer's
    payment method touches the merchant's own checkout configuration."""

    auto_approval_revenue_ceiling: float = 20000.0
    """Even for actions not in the list above, if the total expected
    revenue recovery for one action exceeds this amount, merchant
    approval is required before execution."""

    # --- Forced STOP conditions -------------------------------------------
    stop_severities: frozenset = frozenset({"LOW"})
    """Incident severities (from the detection engine) for which automated
    recovery is never attempted -- treated as not worth acting on."""

    stop_below_revenue: float = 5.0
    """If the AI's revenue_at_risk figure (itself a deterministic value --
    see app/agent/guardrails.py) is below this, there is nothing
    meaningful to recover; force STOP rather than acting."""

    # --- Forced ESCALATE conditions ----------------------------------------
    escalate_severities: frozenset = frozenset({"CRITICAL"})
    """Incident severities that always require human review before any
    automated action, regardless of AI confidence."""

    min_confidence_to_auto_act: float = 0.5
    """Below this AI confidence, escalate rather than act automatically --
    a second, independent check on top of the agent module's own internal
    confidence guardrails (defense in depth: this layer does not trust
    that the agent's own thresholds were applied correctly)."""


DEFAULT_POLICY = PolicyConfig()

# Fields a merchant is allowed to override, and in which direction.
# "tighten_only" fields may only be moved to be MORE restrictive than the
# default; a merchant cannot use their own policy to grant themselves
# looser safety limits than the platform default.
_TIGHTEN_ONLY_NUMERIC_FIELDS = {
    "max_retry_attempts_per_transaction": "max",
    "max_retry_attempts_per_incident": "max",
    "max_eligible_amount": "max",
    "cooldown_minutes": "min",  # a longer cooldown is more restrictive
    "max_customer_contacts_per_incident": "max",
    "auto_approval_revenue_ceiling": "max",
    "min_confidence_to_auto_act": "min",  # requiring HIGHER confidence is more restrictive
}


def apply_merchant_overrides(base: PolicyConfig, merchant_policies: dict) -> PolicyConfig:
    """Apply merchant-supplied overrides on top of `base`, allowing only
    tightening of the fields listed above. Any attempt to loosen a limit
    (e.g. a merchant setting max_eligible_amount higher than the
    platform default) is silently clamped back to the platform value --
    a merchant's own policy dict is not a trusted, unbounded input any
    more than the AI's output is."""
    updates = {}
    for field, direction in _TIGHTEN_ONLY_NUMERIC_FIELDS.items():
        if field not in merchant_policies:
            continue
        requested = merchant_policies[field]
        current = getattr(base, field)
        if direction == "max":
            updates[field] = min(current, requested)
        else:  # "min" -- requested value must be >= current to tighten
            updates[field] = max(current, requested)

    if "auto_retry_enabled" in merchant_policies and merchant_policies["auto_retry_enabled"] is False:
        # A merchant may fully disable automated retries; represented as
        # a retry-attempts cap of zero, which the eligibility check
        # already treats as "no automated retries permitted."
        updates["max_retry_attempts_per_transaction"] = 0

    return replace(base, **updates) if updates else base
