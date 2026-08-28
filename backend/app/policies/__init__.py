"""
Policy / Guardrail layer.

Responsible for: deterministic, hard-coded rules that validate any
recovery action the AI agent proposes before it is allowed to execute.
This is the safety backstop — no LLM involvement here.

Not implemented yet. Planned contents:
- rules.py         Hard limits (max $ per action, allowed action types, etc.)
- guardrail.py      validate(action, incident) -> approve / escalate_to_human
- actions.py       Enumeration of the bounded, allowed recovery actions
"""
