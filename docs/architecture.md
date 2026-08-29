# Architecture notes

## Pipeline → module mapping

| Pipeline stage | Backend module | Status |
|---|---|---|
| Synthetic payment data | `app/data/` | **implemented** — see `docs/data_layer.md` |
| Incident Detection | new `app/detection/` (not yet created) | not started |
| Evidence Retrieval | `app/retrieval/` | scaffolded (empty) |
| AI Investigation Agent | `app/agent/` | scaffolded (empty) |
| Recovery Decision | `app/agent/` (produces recommendation) | not started |
| Policy / Guardrail | `app/policies/` | scaffolded (empty) |
| Recovery Action | `app/policies/actions.py` (execution/simulation) | not started |
| Outcome + Audit Trail | `app/audit/` + `app/models/` | scaffolded (empty) |
| Dashboard / Metrics | `frontend/src/` | not started (health check only) |

## Why this structure

A single FastAPI app with clearly separated modules keeps the build fast
for a 5-day hackathon while still mirroring the pipeline 1:1 — anyone
reading the folder list can reconstruct the architecture diagram. No
services need to be split out or containerized separately; SQLite avoids
any external DB dependency.

## Guardrail boundary (important)

The dividing line in this codebase is:

- **`app/agent/`** — LLM-backed. Investigates, diagnoses, and *recommends*
  an action + confidence score. Never touches money.
- **`app/policies/`** — pure deterministic Python. Validates the agent's
  recommendation against hard rules (limits, allowed action types) and
  either approves it for execution, blocks it, or escalates to a human
  queue.
- **`app/audit/`** — records every step above, regardless of outcome.

Any future code that executes a recovery action should live under
`app/policies/` (or a thin `app/execution.py` it calls), not inside
`app/agent/`.

## Explicitly not built yet

- Incident detection logic (anomaly/degradation detection over payment data)
- Evidence retrieval implementation (structured + unstructured)
- The agent itself (tool use, prompting, diagnosis)
- Guardrail rules and allowed-action definitions
- Audit log persistence
- Dashboard/metrics UI

These are intentionally left out per the current build step so the
scaffold stays minimal and reviewable.
