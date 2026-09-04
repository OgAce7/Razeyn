"""
Razeyn backend entrypoint.

Run with:
    uvicorn app.main:app --reload
(from inside backend/, with the virtualenv active)
"""

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.core.db import init_db
from app.api import health, incidents, datasets
from app.api.pipeline import seed_from_synthetic_dataset
from app.api.state import get_app_state

logger = logging.getLogger("razeyn")

app = FastAPI(title="Razeyn API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router, prefix="/api")
app.include_router(incidents.router)
app.include_router(datasets.router)


@app.on_event("startup")
def on_startup():
    init_db()

    # Single, long-lived AppState for this process (ledger + audit store +
    # pending escalations). Everything in app/api/ reads/writes this one
    # instance -- see app/api/state.py's docstring for why that matters
    # for the double-recovery guardrails. Not persisted across restarts;
    # that trade-off is intentional for this pass (real SQLAlchemy
    # persistence was scoped out -- see app/api/state.py).
    #
    # IMPORTANT: app.state.app_state MUST be set before this function
    # returns, no matter what happens below. Every other endpoint
    # (including GET /api/datasets) reads request.app.state.app_state
    # unconditionally, so if seeding raises and this attribute is left
    # unset, EVERY request 500s with an AttributeError, not just the
    # ones related to the seeded dataset -- that's indistinguishable
    # from "the whole backend is broken" from the frontend's point of
    # view, however small the actual seeding failure was.
    app.state.app_state = get_app_state()
    try:
        seed_from_synthetic_dataset(app.state.app_state)
        logger.info(
            "Seeded %d audit record(s) from the synthetic dataset (%d pending escalation(s)).",
            len(app.state.app_state.audit_store.all()),
            len(app.state.app_state.pending),
        )
    except FileNotFoundError as e:
        # Dataset not generated yet -- app still starts, but
        # /api/evaluation/audit-trail will return an empty list until a
        # dataset exists. Logged loudly rather than crashing startup.
        logger.warning("Could not seed synthetic dataset: %s", e)
    except Exception:
        # Seeding calls the real investigation pipeline (including a live
        # Mistral API call per candidate incident, see
        # app/agent/investigate.py) -- a rate limit, timeout, transient
        # network failure, or any other unexpected error here must not
        # take the whole app down. app.state.app_state is already set
        # above, so the app still boots and serves requests; it just
        # starts with zero seeded incidents until the dataset is
        # re-activated (POST /api/datasets/activate/seeded) or a real
        # dataset is uploaded.
        logger.exception(
            "Seeding the synthetic dataset failed; the app will still start, but with "
            "no seeded incidents. Retry via POST /api/datasets/activate/seeded once the "
            "underlying issue (often a Mistral API problem) is resolved."
        )


@app.get("/")
def root():
    return {"message": "Razeyn API is running. See /api/health and /docs."}
