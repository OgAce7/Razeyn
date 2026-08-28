"""
AI Investigation Agent.

Responsible for: investigating an incident using retrieved evidence,
producing a diagnosis, and selecting a recovery action from the
allowed/bounded action set defined in app/policies.

Constraints (per project spec):
- The agent reasons and recommends only. It never moves money directly.
- Every diagnosis/recommendation must cite the evidence it used.
- Final action selection is validated by app/policies (deterministic)
  before anything executes.

Not implemented yet. Planned contents:
- client.py      Claude API wrapper (prompt construction, calls)
- tools.py       Tool definitions the agent can call (lookup_transaction, etc.)
- investigate.py Orchestration: evidence -> diagnosis -> recommended action
"""
