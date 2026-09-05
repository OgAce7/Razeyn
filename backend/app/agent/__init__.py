"""
AI Investigation Agent.

Investigates a detected incident using ONLY supplied evidence, and
recommends ONE bounded recovery action from a caller-supplied finite
list. Uses the Groq API with forced tool-use for strict structured
output, backed by deterministic (non-LLM) guardrails that enforce
evidence integrity, revenue-figure integrity, and policy compliance
regardless of what the model returns.

Public entrypoint:
    from app.agent.investigate import investigate_incident
    from app.agent.schema import AgentInput
    result = investigate_incident(AgentInput(incident=..., structured_evidence=..., ...))

Constraints (per project spec):
- The agent reasons and recommends only. It never moves money directly
  (no execution logic lives here -- that's a separate, not-yet-built
  action-executor component).
- Every diagnosis/recommendation must cite the evidence it used, and any
  evidence_id or revenue figure is verified/overwritten deterministically
  against the caller-supplied evidence -- never trusted from the model
  alone.
- Final action selection is checked against the caller-supplied
  allowed_actions list (the not-yet-built policy engine decides what's
  eligible; this module only enforces the model stays inside it).

Files:
- actions.py       The finite universe of possible recovery actions.
- schema.py        AgentInput / AgentOutput (strict Pydantic models).
- prompt.py         System/user prompt construction + forced tool-use schema.
- client.py         Groq API wrapper (the only network call in this module).
- guardrails.py      Deterministic post-processing enforcement.
- investigate.py     Orchestration + fallback handling for every failure mode.
- errors.py           Exception types for each failure mode.

See docs/agent.md for the full design write-up, example input/output, and
prompt design rationale.
"""
