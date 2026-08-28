"""Simple health-check endpoint for backend/frontend connectivity checks."""

from fastapi import APIRouter

from app.core.config import settings

router = APIRouter(tags=["health"])


@router.get("/health")
def health_check():
    return {
        "status": "ok",
        "service": "razeyn-backend",
        "env": settings.app_env,
    }
