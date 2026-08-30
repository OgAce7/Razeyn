"""
Policy / Guardrail / Recovery-Execution layer.

Deterministic, hard-coded rules that validate any recovery action the AI
agent (app/agent/) proposes before it is allowed to execute, plus a
bounded executor that carries out (or simulates) an approved action. No
LLM anywhere in this module -- this is the actual safety backstop.

Flow:
    AI recommendation
      -> validate action (engine.evaluate_policy, step 1)
      -> validate transaction eligibility (engine.py, steps 6-8)
      -> validate policy / forced STOP / ESCALATE / merchant approval
      -> PolicyDecision (approve/reject)
      -> execute or simulate (executor.execute_action)
      -> ActionRecord (audit-style result, appended to an ActionLedger)

Files:
- config.py     Explicit PolicyConfig: retry limits, amount eligibility,
                cooldown, contact limits, merchant-approval rules, and
                forced STOP/ESCALATE conditions.
- ledger.py      ActionRecord schema + ActionLedger (in-memory history
                used for retry-count/cooldown/contact-count lookups).
- engine.py       evaluate_policy(...) -> PolicyDecision. The deterministic
                 decision-maker.
- adapter.py      RecoveryActionAdapter interface + SimulatedAdapter (a
                  deterministic, documented test-mode simulation -- no
                  live Razorpay integration in this environment).
- executor.py     execute_action(...) -> ActionRecord. Dispatches an
                  approved decision to the adapter via a fixed mapping;
                  never a dynamic/arbitrary operation.

See docs/policy_engine.md for the full design write-up and how this
guardrail prevents unsafe autonomous behavior.
"""
