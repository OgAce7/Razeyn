"""
Razeyn backend entrypoint.

Run with:
    uvicorn app.main:app --reload
(from inside backend/, with the virtualenv active)
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.core.db import init_db
from app.api import health

app = FastAPI(title="Razeyn API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router, prefix="/api")


@app.on_event("startup")
def on_startup():
    init_db()


@app.get("/")
def root():
    return {"message": "Razeyn API is running. See /api/health and /docs."}
