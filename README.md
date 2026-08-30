# Razeyn

AI agent for Razorpay's **AI Revenue Recovery** track (5-day hackathon build).

Razeyn detects payment degradation, investigates the cause using evidence,
diagnoses the likely issue, estimates revenue at risk, and chooses a
**bounded, guardrailed recovery action** — with a full audit trail.

## Core workflow

```
Payment data
  → detect payment degradation
  → investigate the incident using relevant evidence
  → diagnose likely cause
  → estimate revenue at risk
  → choose a bounded recovery action
  → execute/simulate the action
  → measure recovered revenue
  → maintain an audit trail
```

## Design principles

- **LLM never controls money.** The AI agent (Claude) only investigates,
  reasons, and recommends. All calculations, policy checks, and financial
  values are owned by deterministic Python code.
- **Every AI decision is traceable to evidence.** Diagnoses reference the
  specific evidence items that support them.
- **Actions are bounded and auditable.** The agent picks from a fixed set
  of allowed actions; a deterministic guardrail layer approves, blocks, or
  escalates to a human before anything executes.
- **Simple over clever.** No microservices, no Kafka/Redis/Kubernetes.
  SQLite + a monolithic FastAPI backend is enough for this scope.

## Architecture

```
razeyn/
├── backend/
│   ├── app/
│   │   ├── main.py           FastAPI app entrypoint
│   │   ├── core/              settings (env vars) + DB session setup
│   │   ├── api/                HTTP routers (health.py so far)
│   │   ├── models/            SQLAlchemy tables (not yet implemented)
│   │   ├── data/                synthetic payment data + loaders (implemented, see docs/data_layer.md)
│   │   ├── detection/           deterministic degradation detection engine (implemented, see docs/detection.md)
│   │   ├── retrieval/          evidence retrieval, structured + unstructured (implemented, see docs/retrieval.md)
│   │   ├── agent/               AI investigation + recovery-decision agent, Claude API (implemented, see docs/agent.md)
│   │   ├── policies/            policy engine, guardrails, bounded executor (implemented, see docs/policy_engine.md)
│   │   └── audit/                audit trail persistence (not yet implemented)
│   ├── tests/                    pytest suite for detection + retrieval + agent + policies
│   ├── requirements.txt
│   └── .env.example
├── frontend/
│   ├── src/
│   │   ├── main.jsx
│   │   └── App.jsx            currently just a backend health-check ping
│   ├── package.json
│   └── .env.example
└── docs/
```

Each pipeline stage above maps to one backend module, so the flow stays
easy to reason about: `data → retrieval → agent → policies → audit → api`.

The `data` layer (synthetic payments + ground-truth incidents) is
implemented — see `docs/data_layer.md` for the full schema, incident
patterns, and how to regenerate it.

## Running the project

### Backend

```bash
cd backend
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env             # then fill in ANTHROPIC_API_KEY etc.
uvicorn app.main:app --reload
```

Backend runs at `http://localhost:8000`. Check `http://localhost:8000/api/health`
and interactive docs at `http://localhost:8000/docs`.

### Frontend

```bash
cd frontend
npm install
cp .env.example .env             # optional, default points to /api via proxy
npm run dev
```

Frontend runs at `http://localhost:5173` and proxies `/api/*` to the
backend (see `vite.config.js`). The homepage shows a live backend health
status to confirm connectivity.

## Environment variables

| File | Key | Purpose |
|---|---|---|
| `backend/.env` | `ANTHROPIC_API_KEY` | Claude API key for the investigation agent |
| `backend/.env` | `ANTHROPIC_MODEL` | Model name, defaults to `claude-sonnet-4-6` |
| `backend/.env` | `MISTRAL_API_KEY` | Optional, for evidence retrieval if needed |
| `backend/.env` | `DATABASE_URL` | SQLite connection string |
| `backend/.env` | `CORS_ORIGINS` | Allowed frontend origin(s) |
| `frontend/.env` | `VITE_API_BASE_URL` | Override API base path if not using the dev proxy |

Never commit real `.env` files — only the `.env.example` templates.

## Status

Synthetic data + ground-truth incident generation is implemented (see
`docs/data_layer.md`). The deterministic degradation detection engine is
implemented (see `docs/detection.md`). The evidence retrieval layer is
implemented (see `docs/retrieval.md`). The AI investigation and
recovery-decision agent is implemented (see `docs/agent.md`). The
deterministic policy, guardrail, and recovery-execution layer is
implemented (see `docs/policy_engine.md`). What remains is audit-log
persistence and the dashboard. See `docs/architecture.md` for what's
built vs. what's still to come.
