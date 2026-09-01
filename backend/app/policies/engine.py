"""
The policy engine -- the deterministic decision-maker this whole module
exists for. No LLM anywhere in this file.

Flow (see docs/policy_engine.md for the full write-up):
    AI recommendation
      -> validate action is known/supported          (_check_action_supported logic inline)
      -> forced STOP / ESCALATE conditions
      -> transaction eligibility (amount + status)
      -> per-transaction policy checks (retries, cooldown)
         and per-customer contact limits
      -> merchant-approval requirement
      -> PolicyDecision (approved/rejected + reason + all check results)

Every check appends a PolicyCheckResult to the decision's policy_checks
list, whether it passed or failed -- the full trail is always returned,
not just the first failure, so a caller/audit reader can see exactly
what was and wasn't satisfied.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.agent.actions import ALL_ACTIONS, ESCALATE, STOP, WAIT_AND_REASSESS
from app.policies.config import PolicyConfig, apply_merchant_overrides
from app.policies.ledger import ActionLedger, PolicyCheckResult

# Actions that actually touch a transaction/customer when executed.
# STOP, WAIT_AND_REASSESS, and ESCALATE are handled as special pass-through
# cases before any transaction-level eligibility logic runs.
ACTIONABLE_ACTIONS = frozenset(
    {"RETRY_ELIGIBLE_PAYMENTS", "OFFER_ALTERNATE_METHOD", "SEND_RECOVERY_LINK", "NOTIFY_MERCHANT"}
)

# The only severities the detection engine ever produces (see
# app/detection/stats.severity_from). An incident dict with a severity
# outside this set (missing, None, or a typo/corrupted value) has failed
# to give this engine enough information to apply the severity-based
# STOP/ESCALATE rules below -- rather than silently falling through as
# "not in the stop set, not in the escalate set" and defaulting to full
# automated approval, that case is treated the same as the forced-
# ESCALATE severities: require a human before any action.
KNOWN_SEVERITIES = frozenset({"LOW", "MEDIUM", "HIGH", "CRITICAL"})


@dataclass
class PolicyDecision:
    approved: bool
    escalation_required: bool
    reason: str
    policy_checks: list = field(default_factory=list)
    eligible_transaction_ids: list = field(default_factory=list)
    expected_revenue_recovery: float = 0.0

    def checks_as_dicts(self) -> list:
        return [c.to_dict() for c in self.policy_checks]


def evaluate_policy(
    recommended_action: str,
    incident: dict,
    transactions: list,
    confidence: float,
    revenue_at_risk: float,
    ledger: ActionLedger,
    merchant_policies: dict | None = None,
    now: datetime | None = None,
) -> PolicyDecision:
    """Decide whether `recommended_action` may proceed.

    Parameters
    ----------
    recommended_action : the AI agent's AgentOutput.recommended_action.
    incident : the candidate incident dict (from the detection engine).
    transactions : the specific transaction records this action would
        touch -- each dict needs at least transaction_id, amount,
        customer_id, status. This is the ONLY source of per-transaction
        amounts this engine will ever use (see docs/policy_engine.md's
        "never trust the AI for money" section).
    confidence : the AI agent's AgentOutput.confidence.
    revenue_at_risk : the AI agent's AgentOutput.revenue_at_risk (already
        a deterministic figure by the time it reaches here -- see
        app/agent/guardrails.py -- but this engine re-derives its own
        expected_revenue_recovery from `transactions` rather than trusting
        this value for anything except the STOP-below-revenue check).
    ledger : history used for retry-count/cooldown/contact-count checks.
    merchant_policies : optional overrides (tighten-only, see config.py).
    now : injectable "current time" for deterministic testing; defaults
        to real UTC now.
    """
    now = now or datetime.now(timezone.utc)
    policy = apply_merchant_overrides(PolicyConfig(), merchant_policies or {})
    checks = []

    # 1. Is this even a recognized action at all? ---------------------------
    if recommended_action not in ALL_ACTIONS:
        checks.append(
            PolicyCheckResult(
                "action_supported",
                False,
                f"{recommended_action!r} is not a recognized action; the executor only "
                f"supports {sorted(ALL_ACTIONS)}.",
            )
        )
        return PolicyDecision(
            approved=False,
            escalation_required=True,
            reason=f"Unsupported action: {recommended_action!r}.",
            policy_checks=checks,
        )
    checks.append(PolicyCheckResult("action_supported", True, "Action is a recognized type."))

    # 2. Pass-through actions: STOP / WAIT_AND_REASSESS / ESCALATE ------------
    if recommended_action == STOP:
        checks.append(PolicyCheckResult("pass_through", True, "STOP is always approved as a no-op."))
        return PolicyDecision(approved=True, escalation_required=False, reason="STOP recommendation accepted; no action taken.", policy_checks=checks)

    if recommended_action == WAIT_AND_REASSESS:
        checks.append(PolicyCheckResult("pass_through", True, "WAIT_AND_REASSESS is always approved as a no-op."))
        return PolicyDecision(approved=True, escalation_required=False, reason="WAIT_AND_REASSESS accepted; re-evaluate later, no action taken now.", policy_checks=checks)

    if recommended_action == ESCALATE:
        checks.append(PolicyCheckResult("pass_through", True, "ESCALATE is always approved and always routes to a human."))
        return PolicyDecision(approved=True, escalation_required=True, reason="ESCALATE recommendation accepted; routed to human review.", policy_checks=checks)

    # 3. Forced STOP conditions (severity, immaterial revenue) ---------------
    severity = incident.get("severity")

    if severity not in KNOWN_SEVERITIES:
        checks.append(PolicyCheckResult(
            "severity_recognized",
            False,
            f"Incident severity {severity!r} is missing or not one of the recognized values "
            f"{sorted(KNOWN_SEVERITIES)}; cannot apply severity-based automation rules safely.",
        ))
        return PolicyDecision(
            approved=True,
            escalation_required=True,
            reason=f"Forced ESCALATE: incident severity {severity!r} is missing or unrecognized.",
            policy_checks=checks,
        )
    checks.append(PolicyCheckResult("severity_recognized", True, f"Severity {severity!r} is a recognized value."))

    if severity in policy.stop_severities:
        checks.append(PolicyCheckResult("forced_stop_severity", False, f"Incident severity {severity!r} is in the forced-STOP set {sorted(policy.stop_severities)}."))
        return PolicyDecision(approved=False, escalation_required=False, reason=f"Forced STOP: incident severity {severity!r} does not warrant automated recovery.", policy_checks=checks)
    checks.append(PolicyCheckResult("forced_stop_severity", True, f"Severity {severity!r} is not in the forced-STOP set."))

    if revenue_at_risk < policy.stop_below_revenue:
        checks.append(PolicyCheckResult("forced_stop_revenue_floor", False, f"revenue_at_risk {revenue_at_risk} is below the materiality floor {policy.stop_below_revenue}."))
        return PolicyDecision(approved=False, escalation_required=False, reason=f"Forced STOP: revenue at risk ({revenue_at_risk}) is below the materiality threshold ({policy.stop_below_revenue}); not worth automated action.", policy_checks=checks)
    checks.append(PolicyCheckResult("forced_stop_revenue_floor", True, f"revenue_at_risk {revenue_at_risk} clears the materiality floor."))

    # 4. Forced ESCALATE conditions (severity, confidence) -------------------
    if severity in policy.escalate_severities:
        checks.append(PolicyCheckResult("forced_escalate_severity", False, f"Incident severity {severity!r} is in the forced-ESCALATE set {sorted(policy.escalate_severities)}."))
        return PolicyDecision(approved=True, escalation_required=True, reason=f"Forced ESCALATE: incident severity {severity!r} always requires human review before any action.", policy_checks=checks)
    checks.append(PolicyCheckResult("forced_escalate_severity", True, f"Severity {severity!r} is not in the forced-ESCALATE set."))

    if confidence < policy.min_confidence_to_auto_act:
        checks.append(PolicyCheckResult("forced_escalate_confidence", False, f"confidence {confidence} is below the auto-act floor {policy.min_confidence_to_auto_act}."))
        return PolicyDecision(approved=True, escalation_required=True, reason=f"Forced ESCALATE: confidence ({confidence}) is below the threshold ({policy.min_confidence_to_auto_act}) required for automated action.", policy_checks=checks)
    checks.append(PolicyCheckResult("forced_escalate_confidence", True, f"confidence {confidence} clears the auto-act floor."))

    if recommended_action not in ACTIONABLE_ACTIONS:  # pragma: no cover - defensive, all cases already handled above
        checks.append(PolicyCheckResult("action_actionable", False, f"{recommended_action} has no transaction-level handling defined."))
        return PolicyDecision(approved=False, escalation_required=True, reason=f"{recommended_action} is not an actionable recovery action.", policy_checks=checks)

    # 5. NOTIFY_MERCHANT touches no transactions/customers -- approve directly ---
    if recommended_action == "NOTIFY_MERCHANT":
        checks.append(PolicyCheckResult("notify_merchant_pass_through", True, "NOTIFY_MERCHANT does not touch transactions or customers; approved directly."))
        return PolicyDecision(approved=True, escalation_required=False, reason="NOTIFY_MERCHANT approved.", policy_checks=checks, eligible_transaction_ids=[t["transaction_id"] for t in transactions], expected_revenue_recovery=0.0)

    # 6. Transaction-level eligibility (amount, status, retry limit, cooldown) --
    eligible, txn_checks, exclusion_counts = _filter_eligible_transactions(
        recommended_action, transactions, policy, ledger, incident.get("incident_id", ""), now
    )
    checks.extend(txn_checks)

    # 7. Customer contact limits (for customer-facing actions) -------------------
    if recommended_action in policy.contact_actions:
        eligible, contact_checks = _filter_by_contact_limit(
            eligible, policy, ledger, incident.get("incident_id", "")
        )
        checks.extend(contact_checks)
        if len(eligible) < exclusion_counts.get("_pre_contact_eligible_count", len(eligible)):
            exclusion_counts["contact_limit"] = exclusion_counts.get("_pre_contact_eligible_count", 0) - len(eligible)

    if not eligible:
        checks.append(PolicyCheckResult("eligible_transactions_remaining", False, "No transactions remain eligible after policy filtering."))
        # Distinguish "will become eligible again later" (cooldown) from
        # "has permanently exhausted its retry budget after genuinely
        # being attempted" (retry_limit with a nonzero budget) from "a
        # merchant has deliberately disabled auto-retry entirely"
        # (max_retry_attempts_per_transaction == 0, which is a clean,
        # intentional opt-out, not a failure needing escalation). A
        # rejection caused ENTIRELY by retry exhaustion -- with retries
        # actually enabled, and no transactions merely waiting out a
        # cooldown -- means retrying this incident automatically will
        # never make progress and a human should be looped in rather
        # than the caller silently re-trying (or a scheduler silently
        # re-polling) forever with no path to resolution. See
        # docs/policy_engine.md and the "repeated recovery failure" QA
        # scenario.
        retry_exhausted_only = (
            recommended_action == "RETRY_ELIGIBLE_PAYMENTS"
            and policy.max_retry_attempts_per_transaction > 0
            and exclusion_counts.get("retry", 0) > 0
            and exclusion_counts.get("cooldown", 0) == 0
            and exclusion_counts.get("amount", 0) == 0
            and exclusion_counts.get("status", 0) == 0
            and exclusion_counts.get("already_recovered", 0) == 0
            and exclusion_counts.get("contact_limit", 0) == 0
        )
        if retry_exhausted_only:
            checks.append(PolicyCheckResult(
                "retry_budget_exhausted_escalation",
                False,
                f"All {exclusion_counts['retry']} transaction(s) have permanently exhausted their "
                f"per-transaction retry limit ({policy.max_retry_attempts_per_transaction}); automated "
                "retrying cannot make further progress on this incident. Escalating for human review.",
            ))
            return PolicyDecision(
                approved=True,
                escalation_required=True,
                reason=(
                    "Forced ESCALATE: every remaining transaction has exhausted its automated retry "
                    "budget with no successful recovery; further automated retries would not help."
                ),
                policy_checks=checks,
            )
        return PolicyDecision(approved=False, escalation_required=False, reason="Rejected: no transactions are eligible for this action after applying amount, retry, cooldown, and contact-limit policies.", policy_checks=checks)
    checks.append(PolicyCheckResult("eligible_transactions_remaining", True, f"{len(eligible)} of {len(transactions)} transaction(s) remain eligible."))

    expected_revenue = round(sum(t["amount"] for t in eligible), 2)

    # 8. Incident-level retry cap -------------------------------------------------
    if recommended_action == "RETRY_ELIGIBLE_PAYMENTS":
        already = ledger.total_retries_for_incident(incident.get("incident_id", ""))
        remaining_budget = policy.max_retry_attempts_per_incident - already
        if remaining_budget <= 0:
            checks.append(PolicyCheckResult("incident_retry_budget", False, f"Incident has already used {already} of {policy.max_retry_attempts_per_incident} allowed retries."))
            return PolicyDecision(approved=False, escalation_required=False, reason="Rejected: this incident has exhausted its incident-level retry budget.", policy_checks=checks)
        if len(eligible) > remaining_budget:
            eligible = eligible[:remaining_budget]
            checks.append(PolicyCheckResult("incident_retry_budget", True, f"Only {remaining_budget} retr(y/ies) remain in the incident budget; truncated eligible set accordingly."))
            expected_revenue = round(sum(t["amount"] for t in eligible), 2)
        else:
            checks.append(PolicyCheckResult("incident_retry_budget", True, f"{remaining_budget} retr(y/ies) remain in the incident budget; all eligible transactions fit within it."))

    # 9. Merchant-approval requirement -------------------------------------------
    needs_approval, approval_check = _check_merchant_approval(recommended_action, expected_revenue, policy)
    checks.append(approval_check)

    return PolicyDecision(
        approved=True,
        escalation_required=needs_approval,
        reason=(
            "Approved, pending merchant sign-off before execution."
            if needs_approval
            else "Approved for automated execution."
        ),
        policy_checks=checks,
        eligible_transaction_ids=[t["transaction_id"] for t in eligible],
        expected_revenue_recovery=expected_revenue,
    )


def _filter_eligible_transactions(action, transactions, policy, ledger, incident_id, now):
    checks = []
    eligible = []
    excluded_amount = excluded_status = excluded_retry = excluded_cooldown = excluded_already_recovered = 0

    for t in transactions:
        txn_id = t["transaction_id"]
        amount = t["amount"]

        if not (policy.min_eligible_amount <= amount <= policy.max_eligible_amount):
            excluded_amount += 1
            continue

        if action == "RETRY_ELIGIBLE_PAYMENTS" and t.get("status") != "FAILED":
            excluded_status += 1
            continue

        if action == "RETRY_ELIGIBLE_PAYMENTS":
            if ledger.already_succeeded(txn_id, action):
                excluded_already_recovered += 1
                continue
            if ledger.retry_count(txn_id) >= policy.max_retry_attempts_per_transaction:
                excluded_retry += 1
                continue

        last_action = ledger.last_action_time(txn_id)
        if last_action is not None:
            elapsed_minutes = (now - last_action).total_seconds() / 60.0
            if elapsed_minutes < policy.cooldown_minutes:
                excluded_cooldown += 1
                continue

        eligible.append(t)

    checks.append(PolicyCheckResult(
        "amount_eligibility",
        excluded_amount == 0,
        f"{excluded_amount} of {len(transactions)} transaction(s) fell outside the eligible amount range "
        f"[{policy.min_eligible_amount}, {policy.max_eligible_amount}] and were excluded.",
    ))
    if action == "RETRY_ELIGIBLE_PAYMENTS":
        checks.append(PolicyCheckResult(
            "status_eligibility",
            excluded_status == 0,
            f"{excluded_status} transaction(s) were not in FAILED status and cannot be retried.",
        ))
        checks.append(PolicyCheckResult(
            "already_recovered",
            excluded_already_recovered == 0,
            f"{excluded_already_recovered} transaction(s) already have a successful retry on record "
            "and were excluded to prevent double-counting recovered revenue.",
        ))
        checks.append(PolicyCheckResult(
            "retry_attempt_limit",
            excluded_retry == 0,
            f"{excluded_retry} transaction(s) already reached the per-transaction retry limit "
            f"({policy.max_retry_attempts_per_transaction}) and were excluded.",
        ))
    checks.append(PolicyCheckResult(
        "cooldown_period",
        excluded_cooldown == 0,
        f"{excluded_cooldown} transaction(s) are within the {policy.cooldown_minutes}-minute cooldown "
        "since their last automated action and were excluded.",
    ))
    exclusion_counts = {
        "amount": excluded_amount,
        "status": excluded_status,
        "already_recovered": excluded_already_recovered,
        "retry": excluded_retry,
        "cooldown": excluded_cooldown,
        "_pre_contact_eligible_count": len(eligible),
    }
    return eligible, checks, exclusion_counts


def _filter_by_contact_limit(transactions, policy, ledger, incident_id):
    eligible = []
    excluded = 0
    for t in transactions:
        customer_id = t.get("customer_id")
        if customer_id and ledger.contact_count(customer_id, incident_id, policy.contact_actions) >= policy.max_customer_contacts_per_incident:
            excluded += 1
            continue
        eligible.append(t)

    check = PolicyCheckResult(
        "customer_contact_limit",
        excluded == 0,
        f"{excluded} transaction(s) belonged to customers who already reached the "
        f"{policy.max_customer_contacts_per_incident}-contact limit for this incident and were excluded.",
    )
    return eligible, [check]


def _check_merchant_approval(action, expected_revenue, policy):
    if action in policy.actions_requiring_merchant_approval:
        return True, PolicyCheckResult(
            "merchant_approval_required",
            False,
            f"{action} is always in the merchant-approval-required action set.",
        )
    if expected_revenue > policy.auto_approval_revenue_ceiling:
        return True, PolicyCheckResult(
            "merchant_approval_required",
            False,
            f"Expected revenue recovery ({expected_revenue}) exceeds the auto-approval ceiling "
            f"({policy.auto_approval_revenue_ceiling}).",
        )
    return False, PolicyCheckResult(
        "merchant_approval_required", True, "No merchant approval required for this action/amount."
    )
