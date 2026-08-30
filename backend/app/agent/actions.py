"""
The finite, explicit set of recovery actions the agent is allowed to
recommend. This is the universe of *possible* actions — the actual set
usable for a given call is `AgentInput.allowed_actions`, supplied by the
caller (the not-yet-built policy engine, in later work). The agent must
never recommend anything outside whichever list it was given; see
app/agent/guardrails.py for the deterministic enforcement of that rule.

The agent only *recommends* one of these. Nothing in this module executes
a financial action — that's explicitly out of scope (the action executor
is a separate, not-yet-built component).
"""

RETRY_ELIGIBLE_PAYMENTS = "RETRY_ELIGIBLE_PAYMENTS"
OFFER_ALTERNATE_METHOD = "OFFER_ALTERNATE_METHOD"
SEND_RECOVERY_LINK = "SEND_RECOVERY_LINK"
NOTIFY_MERCHANT = "NOTIFY_MERCHANT"
WAIT_AND_REASSESS = "WAIT_AND_REASSESS"
STOP = "STOP"
ESCALATE = "ESCALATE"

ALL_ACTIONS = [
    RETRY_ELIGIBLE_PAYMENTS,
    OFFER_ALTERNATE_METHOD,
    SEND_RECOVERY_LINK,
    NOTIFY_MERCHANT,
    WAIT_AND_REASSESS,
    STOP,
    ESCALATE,
]

ACTION_DESCRIPTIONS = {
    RETRY_ELIGIBLE_PAYMENTS: (
        "Retry payments that failed for transient/technical reasons (e.g. bank "
        "timeout, network error) where a retry is plausible to succeed."
    ),
    OFFER_ALTERNATE_METHOD: (
        "Prompt affected customers to complete their payment using a different "
        "payment method than the one currently degraded."
    ),
    SEND_RECOVERY_LINK: (
        "Send affected customers a link to complete/retry their payment outside "
        "the original checkout flow."
    ),
    NOTIFY_MERCHANT: (
        "Alert the merchant/operations team of the incident without taking any "
        "customer-facing or financial action."
    ),
    WAIT_AND_REASSESS: (
        "Take no action yet; re-evaluate after a defined interval, appropriate "
        "when evidence is ambiguous or the pattern may be resolving on its own."
    ),
    STOP: (
        "End investigation/response for this incident — appropriate when the "
        "evidence indicates this is not a genuine incident (e.g. normal "
        "volume fluctuation) or it has already resolved with no ongoing impact."
    ),
    ESCALATE: (
        "Hand off to a human for review — appropriate when confidence is low, "
        "evidence is insufficient, the recommended action would violate policy, "
        "or the situation exceeds this agent's authority."
    ),
}
