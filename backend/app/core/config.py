"""
Centralized application settings.

All environment variables are read once here via pydantic-settings.
Every other module should import `settings` from this file instead of
calling os.environ directly, so config stays in one place.
"""

from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # LLM
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-6"

    # Retrieval (optional)
    mistral_api_key: str = ""

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
