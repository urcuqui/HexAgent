"""Application configuration.

Settings are loaded from environment variables (and a local ``.env`` file via
``python-dotenv``) using ``pydantic-settings``. A single :func:`get_settings`
accessor returns a cached, validated :class:`Settings` instance that the rest of
the application depends on.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Load .env once at import time so plain ``os.environ`` access also works.
load_dotenv()


class Settings(BaseSettings):
    """Validated runtime settings for HexAgent.

    Attributes mirror the keys documented in ``.env.example``. When no API key is
    available (or ``mock_mode`` is forced) the agents fall back to deterministic
    offline behaviour so the POC always runs.
    """

    model_config = SettingsConfigDict(
        env_prefix="",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    # LLM (Vercel AI Gateway by default — an OpenAI-compatible endpoint).
    # The API key is read from AI_GATEWAY_API_KEY (Vercel's convention) and falls
    # back to OPENAI_API_KEY for compatibility with other OpenAI-compatible hosts.
    openai_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("AI_GATEWAY_API_KEY", "OPENAI_API_KEY"),
    )
    openai_base_url: str = Field(default="https://ai-gateway.vercel.sh/v1", alias="OPENAI_BASE_URL")
    model: str = Field(default="openai/gpt-4o-mini", alias="HEXAGENT_MODEL")
    temperature: float = Field(default=0.1, alias="HEXAGENT_TEMPERATURE")
    mock_mode: bool = Field(default=False, alias="HEXAGENT_MOCK_MODE")

    # Agent behaviour
    max_iterations: int = Field(default=12, alias="HEXAGENT_MAX_ITERATIONS")
    require_human_approval: bool = Field(default=False, alias="HEXAGENT_REQUIRE_HUMAN_APPROVAL")

    # Logging / output
    log_level: str = Field(default="INFO", alias="HEXAGENT_LOG_LEVEL")
    report_dir: Path = Field(default=Path("reports"), alias="HEXAGENT_REPORT_DIR")

    @property
    def use_llm(self) -> bool:
        """True when a real LLM should be used (API key present and mock off)."""
        return bool(self.openai_api_key) and not self.mock_mode

    def ensure_report_dir(self) -> Path:
        """Create the report output directory if needed and return its path."""
        self.report_dir.mkdir(parents=True, exist_ok=True)
        return self.report_dir


@lru_cache
def get_settings() -> Settings:
    """Return a cached, validated :class:`Settings` instance."""
    return Settings()
