"""
Centralized application settings.

All environment variables are read once here via pydantic-settings.
Every other module should import `settings` from this file instead of
calling os.environ directly, so config stays in one place.
"""

from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # LLM -- the investigation agent (app/agent/client.py) calls Groq.
    # anthropic_* settings are kept (unused by the agent) only so a
    # switch back to Claude later doesn't require touching config.py.
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-6"

    # GROQ_API_KEY powers the investigation agent (app/agent/client.py).
    # Get a free key (no credit card) from https://console.groq.com --
    # the free tier allows 30 requests/minute, vs. Mistral's ~2/minute
    # (see app/agent/client.py's module docstring for why this project
    # moved off Mistral for the agent specifically).
    groq_api_key: str = ""
    # llama-3.3-70b-versatile was Groq's original recommended tool-calling
    # model, but Groq deprecated it (announced 2026-06-17, decommissioned
    # 2026-08-16) in favor of openai/gpt-oss-120b (or the smaller
    # openai/gpt-oss-20b) -- see https://console.groq.com/docs/deprecations.
    # Using the old name now fails every call with a 404 model_not_found.
    groq_agent_model: str = "openai/gpt-oss-120b"

    # mistral_api_key is UNRELATED to the agent above -- it's used only,
    # optionally, by app/retrieval/embeddings.py's Mistral embedding
    # backend, which silently falls back to a local, dependency-free
    # vectorizer if this is unset. Not required for the app to function.
    mistral_api_key: str = ""

    # Delay (seconds) between successive investigate_incident() calls when
    # processing a batch of candidates (dataset seeding/upload) -- 0 by
    # default (Groq's free tier rarely needs this given 30 req/min), but
    # can be set if you're on a more restrictive tier and seeing 429s
    # even after the retry/backoff in app/agent/client.py.
    agent_call_interval_seconds: float = 0.0

    # Database
    database_url: str = "sqlite:///./razeyn.db"

    # App
    app_env: str = "development"
    cors_origins: str = "http://localhost:5173"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    """Cached settings instance — import this via `settings` below in normal use."""
    return Settings()


settings = get_settings()
