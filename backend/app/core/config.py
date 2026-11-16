"""Application settings, loaded from environment variables and the repo-root `.env`."""

from datetime import date
from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env",
        env_ignore_empty=True,
        extra="ignore",
    )

    # Database
    database_url: str = "postgresql+psycopg://uiuc:uiuc@localhost:5432/uiuc_research"

    # OpenAlex
    openalex_api_key: SecretStr | None = None
    openalex_institution_id: str = "I157725225"

    # Snapshot window (ADR-007): fixed date, never `date.today()`
    snapshot_date: date = date(2026, 11, 16)
    window_years: int = 2

    # Retrieval
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    embedding_dim: int = 384

    # LLM (ADR-004): provider-agnostic, `none` needs no key
    llm_provider: str = "none"
    llm_model: str | None = None
    llm_api_key: SecretStr | None = None
    llm_base_url: str | None = None

    # API
    api_cors_origins: str = "http://localhost:3000"

    @property
    def window_start(self) -> date:
        """First publication date included in the corpus (snapshot minus `window_years`)."""
        try:
            return self.snapshot_date.replace(year=self.snapshot_date.year - self.window_years)
        except ValueError:  # Feb 29 -> Feb 28
            return self.snapshot_date.replace(
                year=self.snapshot_date.year - self.window_years, day=28
            )

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.api_cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
