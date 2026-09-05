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
        # Never expose the key itself -- just whether one is configured at
        # all, so a missing GROQ_API_KEY (the #1 cause of every incident
        # silently falling back to ESCALATE/0 revenue recovered) is visible
        # from a simple health check instead of being discovered incident
        # by incident in the dashboard.
        "groq_api_key_configured": bool(settings.groq_api_key),
        "groq_agent_model": settings.groq_agent_model,
    }
