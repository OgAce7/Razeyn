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


@app.get("/")
def root():
    return {"message": "Razeyn API is running. See /api/health and /docs."}
