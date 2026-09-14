"""Application configuration — the single source of truth for every setting.

Values are read once from the environment or a local `.env` file and validated
by pydantic-settings, so a malformed value fails here at import rather than
surfacing as a broken feature mid-request.

Credentials are `SecretStr`: they render as `**********` in logs, reprs and
tracebacks, and have to be unwrapped deliberately with `.get_secret_value()`
at the point of use.

Every credential is optional. MarketPulse degrades feature-by-feature rather
than refusing to start: no Gemini key disables AI analysis, no news keys
disable the news panel, and the price dashboard works with neither.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Environment-driven settings. Instantiate via `get_settings()`."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        extra="ignore",
    )

    # --- external credentials -------------------------------------------
    GEMINI_API_KEY: SecretStr | None = None
    NEWS_API_KEY: SecretStr | None = None
    MARKETAUX_API_KEY: SecretStr | None = None

    # --- AI --------------------------------------------------------------
    GEMINI_MODEL: str = "gemini-1.5-flash"

    # --- data refresh ------------------------------------------------------
    DEFAULT_REFRESH_SECONDS: int = 60

    # --- derived -----------------------------------------------------------
    @property
    def gemini_enabled(self) -> bool:
        return self.GEMINI_API_KEY is not None

    @property
    def news_enabled(self) -> bool:
        return self.NEWS_API_KEY is not None or self.MARKETAUX_API_KEY is not None

    def missing_credentials(self) -> list[str]:
        """Credential names that are unset, for a startup banner."""
        return [
            name
            for name in ("GEMINI_API_KEY", "NEWS_API_KEY", "MARKETAUX_API_KEY")
            if getattr(self, name) is None
        ]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """The process-wide settings singleton."""
    return Settings()


def secret(value: SecretStr | None) -> str | None:
    """Unwrap an optional SecretStr for passing to a client library."""
    return value.get_secret_value() if value is not None else None
