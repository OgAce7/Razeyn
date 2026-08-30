# Architecture notes

## Pipeline → module mapping

| Pipeline stage | Backend module | Status |
|---|---|---|
| Synthetic payment data | `app/data/` | **implemented** — see `docs/data_layer.md` |
| Incident Detection | `app/detection/` | **implemented** — see `docs/detection.md` |
| Evidence Retrieval | `app/retrieval/` | **implemented** — see `docs/retrieval.md` |
| AI Investigation Agent | `app/agent/` | **implemented** — see `docs/agent.md` |
| Recovery Decision | `app/agent/` (produces recommendation) | **implemented** — part of the agent's output, see `docs/agent.md` |
| Policy / Guardrail | `app/policies/` | **implemented** — see `docs/policy_engine.md` |
| Recovery Action | `app/policies/executor.py` (execution/simulation) | **implemented** — bounded, simulated executor, see `docs/policy_engine.md` |
| Outcome + Audit Trail | `app/audit/` + `app/models/` | scaffolded (empty) — note: `app/policies/ledger.py` already produces full `ActionRecord`s in-memory; persisting these durably is what remains |
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

- Audit log persistence (durable storage of `ActionRecord`s — the record
  schema and in-memory ledger already exist in `app/policies/ledger.py`)
- Dashboard/metrics UI

These are intentionally left out per the current build step so the
scaffold stays minimal and reviewable.
