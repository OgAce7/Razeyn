"""
Centralized application settings.

All environment variables are read once here via pydantic-settings.
Every other module should import `settings` from this file instead of
calling os.environ directly, so config stays in one place.
"""

from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # LLM -- the investigation agent (app/agent/client.py) calls Mistral.
    # anthropic_* settings are kept (unused by the agent) only so a
    # switch back to Claude later doesn't require touching config.py.
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-6"

    mistral_api_key: str = ""
    mistral_agent_model: str = "mistral-large-latest"

    # Delay (seconds) between successive investigate_incident() calls when
    # processing a batch of candidates (dataset seeding/upload) -- 0 by
    # default (paid tiers don't need it), but free-tier Mistral accounts
    # commonly enforce a strict requests-per-second limit that firing
    # several incidents back-to-back at startup reliably trips, even with
    # per-call retry/backoff in app/agent/client.py. Set e.g. to 2.0 in
    # .env if you're on a free/rate-limited tier and seeing 429s.
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
