# Policy, guardrail, and recovery-execution layer

This document covers `backend/app/policies/` only. It consumes an AI
agent's recommendation (`docs/agent.md`) and decides, deterministically,
whether that recommendation is actually allowed to happen -- then
executes (or simulates) it and records the result. The detection engine
(`docs/detection.md`) was not touched by this work.

## Files

| File | Purpose |
|---|---|
| `app/policies/config.py` | `PolicyConfig` -- every explicit limit, as named constants, plus tighten-only merchant overrides |
| `app/policies/ledger.py` | `ActionRecord` schema + `ActionLedger` (in-memory history for retry/cooldown/contact lookups) |
| `app/policies/engine.py` | `evaluate_policy(...)` -- the deterministic decision-maker |
| `app/policies/adapter.py` | `RecoveryActionAdapter` interface + `SimulatedAdapter` (deterministic test-mode simulation) |
| `app/policies/executor.py` | `execute_action(...)` -- dispatches an approved decision, records the outcome |
| `backend/tests/test_policy_engine.py` | 28 tests covering every required scenario |
| `backend/tests/test_policy_executor.py` | 9 tests for the adapter/executor specifically |

## Running it

```bash
cd backend
source .venv/bin/activate
python -m pytest tests/test_policy_*.py -v
```

```python
from app.policies.engine import evaluate_policy
from app.policies.executor import execute_action
from app.policies.ledger import ActionLedger

ledger = ActionLedger()
incident = {"incident_id": "cand_00005", "severity": "HIGH"}
transactions = [
    {"transaction_id": "txn_1", "amount": 500.0, "customer_id": "cust_1", "status": "FAILED"},
]

decision = evaluate_policy(
    recommended_action="RETRY_ELIGIBLE_PAYMENTS",   # from AgentOutput.recommended_action
    incident=incident,
    transactions=transactions,                       # real transaction records, not AI output
    confidence=0.8,                                    # from AgentOutput.confidence
    revenue_at_risk=500.0,                              # from AgentOutput.revenue_at_risk
    ledger=ledger,
    merchant_policies={"max_eligible_amount": 2000.0},
)

record = execute_action("RETRY_ELIGIBLE_PAYMENTS", decision, incident, transactions, ledger)
print(record.to_dict())
```

## Flow

```
AI recommendation (action, confidence, revenue_at_risk)
        |
        v
1. Is the action a recognized type at all?  --no--> REJECTED (unsupported action)
        | yes
        v
2. Pass-through: STOP / WAIT_AND_REASSESS / ESCALATE?  --yes--> approved trivially, no txn logic
        | no
        v
3. Forced STOP: severity in {LOW}, or revenue_at_risk below materiality floor?  --yes--> REJECTED
        | no
        v
4. Forced ESCALATE: severity in {CRITICAL}, or confidence below floor?  --yes--> approved, escalation_required=True
        | no
        v
5. NOTIFY_MERCHANT?  --yes--> approved directly (touches no money/customers)
        | no
        v
6. Transaction eligibility: amount range, FAILED status (retry only),
   per-transaction retry limit, cooldown period
        |
        v
7. Customer contact limit (SEND_RECOVERY_LINK / OFFER_ALTERNATE_METHOD only)
        |
        v
   any eligible transactions left?  --no--> REJECTED
        | yes
        v
8. Incident-level retry budget (RETRY_ELIGIBLE_PAYMENTS only) -- truncate if needed
        |
        v
9. Merchant-approval requirement (action type, or revenue ceiling)  --yes--> approved, escalation_required=True
        | no
        v
   PolicyDecision(approved=True, escalation_required=False, eligible_transaction_ids, expected_revenue_recovery)
        |
        v
   execute_action(...) -> adapter call (SimulatedAdapter) per eligible transaction -> ActionRecord
```

Every step appends a `PolicyCheckResult` (name, passed, detail) to the
decision regardless of outcome -- the full trail is always available,
not just the first failure.

## The seven explicit policies

| # | Policy | Config field | Default |
|---|---|---|---|
| 1 | Maximum retry attempts | `max_retry_attempts_per_transaction` (per txn), `max_retry_attempts_per_incident` (safety cap across the whole incident) | 3 per transaction, 300 per incident |
| 2 | Min/max transaction amount eligible | `min_eligible_amount`, `max_eligible_amount` | 10 - 5,000 INR |
| 3 | Cooldown period | `cooldown_minutes` | 30 minutes since the transaction's last automated action |
| 4 | Maximum customer contacts | `max_customer_contacts_per_incident`, `contact_actions` | 2 contacts per customer per incident, for `SEND_RECOVERY_LINK`/`OFFER_ALTERNATE_METHOD` |
| 5 | Actions requiring merchant approval | `actions_requiring_merchant_approval`, `auto_approval_revenue_ceiling` | `OFFER_ALTERNATE_METHOD` always; anything else above 20,000 INR total |
| 6 | Conditions that force STOP | `stop_severities`, `stop_below_revenue` | severity `LOW`; revenue at risk below 5 INR |
| 7 | Conditions requiring ESCALATE | `escalate_severities`, `min_confidence_to_auto_act` | severity `CRITICAL`; AI confidence below 0.5 |

All of these are `PolicyConfig` fields with a full docstring in
`config.py` -- that file is the single source of truth for every number.

### Merchant policy overrides -- tighten only

A merchant can supply `merchant_policies` (the same dict the AI agent
receives -- see `app/agent/schema.py`) to make specific limits *more*
restrictive: a lower `max_eligible_amount`, a longer `cooldown_minutes`,
a lower `max_retry_attempts_per_transaction`, a higher
`min_confidence_to_auto_act`, or `auto_retry_enabled: false` to disable
automated retries entirely. Any attempt to *loosen* a limit beyond the
platform default is silently clamped back -- see
`config.apply_merchant_overrides` and
`test_merchant_cannot_loosen_max_amount`. A merchant's own policy
configuration is not treated as unconditionally trusted input any more
than the AI's output is.

## Action record schema

Every decision -- approved or rejected, executed or not -- produces an
`ActionRecord`:

```python
{
  "action_id": "act_00001",
  "incident_id": "cand_00005",
  "transaction_ids": ["txn_1", "txn_2"],
  "requested_action": "RETRY_ELIGIBLE_PAYMENTS",
  "approved": true,
  "reason": "Approved for automated execution.",
  "timestamp": "2026-08-30T14:38:55+00:00",
  "expected_revenue_recovery": 1700.0,
  "actual_result": {
    "outcome": "COMPLETED",
    "attempted": 2,
    "succeeded": 1,
    "failed": 1,
    "per_transaction": [ { "transaction_id": "txn_1", "mode": "simulated", "outcome": "SUCCESS" } ],
    "customer_ids_contacted": []
  },
  "policy_checks": [ { "name": "amount_eligibility", "passed": true, "detail": "..." } ],
  "escalation_required": false,
  "execution_status": "SIMULATED"
}
```

`execution_status` is one of: `SIMULATED` (ran, via the simulation
adapter), `NOT_EXECUTED_REJECTED`, `NOT_EXECUTED_ESCALATED`,
`NOT_EXECUTED_STOPPED`, `NOT_EXECUTED_WAIT`. (`EXECUTED` exists as a
constant for a future live adapter -- see below -- but nothing in this
environment produces it.)

Records are appended to an `ActionLedger` (in-memory, injectable) -- the
same ledger the policy engine queries for retry counts, cooldown timing,
and contact counts, so repeated calls across an incident's lifecycle stay
consistent. Persisting this ledger durably is `app/audit/`'s job (not yet
built); this module only needs it to be queryable within a
process/session.

## Bounded action executor

`SimulatedAdapter` (`adapter.py`) is a **deterministic simulation**, not a
live integration:

- **Why simulation, not real Razorpay test-mode:** `razorpay.com` is not
  in this project's sandboxed environment's network allowlist, so a real
  test-mode integration can't be exercised here. Per the brief's
  fallback instruction, the executor uses "a deterministic simulation
  layer with clearly documented behavior" instead.
- **`RETRY_ELIGIBLE_PAYMENTS`**: simulated success rate 55% per
  transaction, deterministic per `transaction_id` (same id -> same
  outcome, every run -- see `_deterministic_outcome`, a seeded hash, not
  `random`).
- **`SEND_RECOVERY_LINK` / `OFFER_ALTERNATE_METHOD`**: simulates only the
  *delivery* step (97% success), not whether the customer goes on to pay
  -- modeling subsequent customer behavior over time is out of scope here.
- **`NOTIFY_MERCHANT`**: always succeeds (an internal channel, no
  financial risk).
- **Swapping in a real adapter later** means implementing
  `RecoveryActionAdapter` with real Razorpay (or other) calls -- nothing
  in `engine.py` or `executor.py` would need to change, since they only
  depend on the interface.

## Tests

37 tests total, all passing, split across:

- **`test_policy_engine.py`** (28 tests) -- every scenario the brief asks
  for by name, plus supporting coverage:
  - **Allowed retry** -- approved and executed; excludes non-`FAILED`
    transactions automatically.
  - **Retry limit exceeded** -- per-transaction limit (3 by default)
    blocks a 4th retry; a separate test for the incident-level retry
    budget being enforced and truncating the eligible set.
  - **Cooldown violation** -- an immediate second retry is blocked; a
    companion test confirms it clears once the cooldown window has
    passed (both using an injectable `now` clock for determinism).
  - **Repeated customer contact** -- blocked once a customer hits the
    contact limit; a companion test confirms only *successfully
    delivered* contacts count against the limit (a delivery failure
    isn't itself a spam risk).
  - **Unsupported action** -- a string outside the finite action universe
    is rejected outright, with `escalation_required=True`, and is
    confirmed to never reach the executor's execution path.
  - **Escalation** -- the AI directly recommending `ESCALATE`; forced
    escalation from `CRITICAL` severity; forced escalation from low
    confidence; escalated decisions confirmed never executed;
    `OFFER_ALTERNATE_METHOD`'s always-requires-approval rule; the
    revenue-ceiling-triggers-approval rule.
  - **STOP condition** -- forced STOP from `LOW` severity; forced STOP
    from immaterial revenue; the AI directly recommending `STOP`;
    confirmation that a STOP decision never reaches the adapter even if
    transactions were supplied.
  - Amount eligibility (below minimum, above maximum, all-excluded
    rejects the action), merchant policy tighten-only overrides, and two
    "never trust the AI for money" tests (expected revenue is always
    computed from real transaction amounts; `NOTIFY_MERCHANT` never
    touches money at all).
- **`test_policy_executor.py`** (9 tests) -- the adapter's fixed interface
  and determinism, plus executor-specific guarantees: only real
  transaction amounts/customer IDs are ever passed to the adapter (a
  wildly different AI-supplied revenue figure is confirmed absent from
  what the adapter actually received); the dispatch is a fixed mapping,
  not dynamic attribute lookup (probed with an action string chosen to
  look like a Python dunder attribute, to make the "no arbitrary
  operation" guarantee concrete rather than just asserted);
  `NOTIFY_MERCHANT` calls the adapter exactly once regardless of how many
  transactions are in scope.

Run with `cd backend && python -m pytest tests/test_policy_*.py -v`, or
`python -m pytest tests/ -v` for the full project suite (139 tests as of
this module).

## How this guardrail prevents unsafe autonomous behavior

This is the actual point of the module, so it's worth being explicit:

1. **The AI never gets a code path to money.** `evaluate_policy` takes
   `revenue_at_risk` from the AI only to check it against the
   `stop_below_revenue` floor -- it is never used to compute
   `expected_revenue_recovery`, which is always summed from the real
   `transactions` list's `amount` fields. `test_expected_revenue_recovery_ignores_any_ai_supplied_revenue_value`
   pins this: feeding a wildly different, "invented" `revenue_at_risk`
   changes nothing about what the executor would actually pay out or
   report as recovered.

2. **The AI never gets a code path to an arbitrary operation.** The
   action universe is a finite, closed set (`app/agent/actions.py`,
   re-used here, not redefined). An action string outside that set is
   rejected at check #1, before anything else runs. Within the set, the
   executor's dispatch from action name to adapter method is a hardcoded
   `if/elif` chain, not `getattr(adapter, action_name)` -- there is no
   code path by which any string, however crafted, invokes a method that
   isn't one of the four fixed, typed adapter methods.
   `test_executor_dispatch_is_a_fixed_mapping_not_dynamic_lookup` probes
   this directly with an action name chosen to resemble a Python
   attribute-lookup exploit, and confirms it's rejected at the
   action-supported check with zero adapter calls made.

3. **State-based limits can't be talked around by the AI being
   confident.** Retry limits, cooldowns, and contact limits are enforced
   from the `ActionLedger`'s actual history -- real prior `ActionRecord`s
   -- not from anything the AI reports about its own past actions. A
   maximally persuasive, maximally confident AI recommendation to retry
   a transaction for the 4th time, or contact the same customer for the
   3rd time, or retry within the cooldown window, is rejected purely on
   the ledger's count -- confidence and reasoning quality are irrelevant
   to these checks.

4. **Defense in depth on confidence and severity.** The AI agent module
   already has its own internal confidence-threshold guardrails
   (`app/agent/guardrails.py`). This layer does not assume those worked
   correctly and re-checks confidence independently
   (`min_confidence_to_auto_act`), plus adds a check the agent module has
   no visibility into: incident *severity* forcing escalation regardless
   of how confident or well-evidenced the diagnosis was.

5. **Irreversible/customer-facing/high-value actions default to a human
   in the loop.** `OFFER_ALTERNATE_METHOD` always requires merchant
   approval; any action whose expected revenue exceeds the
   auto-approval ceiling requires it too. These are checked *after* an
   action has otherwise cleared every other policy, so "eligible" and
   "unattended" are different things -- eligibility never implies
   automatic execution when the stakes or customer-facing nature of the
   action cross a defined line.

6. **Every decision is recorded, whether it executes or not.** Rejected,
   escalated, and stopped decisions all produce a full `ActionRecord`
   with the same schema as an executed one -- nothing about "the AI
   suggested X and it didn't happen" is silent or undiscoverable after
   the fact.

7. **A merchant's own configuration can't reopen a safety limit.**
   `apply_merchant_overrides` only accepts tightening. A merchant
   attempting to raise `max_eligible_amount` to an arbitrary number, or
   disable the confidence floor, has that attempt silently clamped back
   to the platform default -- merchant input is untrusted in the same
   direction as AI input is.

Put together: **the AI's job is only to point at what evidence-backed
outcome would help. Whether anything actually happens, to which specific
transactions, for how much, and how many times, is decided entirely by
this file -- code that behaves identically whether the AI's confidence
score is 0.99 or the underlying model were swapped out entirely.**
