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

    # Real tools (off by default; only use against explicitly authorised targets).
    enable_nmap: bool = Field(default=False, alias="HEXAGENT_ENABLE_NMAP")
    enable_playwright: bool = Field(default=False, alias="HEXAGENT_ENABLE_PLAYWRIGHT")
    enable_nuclei: bool = Field(default=False, alias="HEXAGENT_ENABLE_NUCLEI")

    # Nuclei settings (only used when enable_nuclei=True). Tag/severity names
    # follow ALLOWED_DEFAULT_TAGS/ALLOWED_SEVERITIES in app/tools/nuclei_tool.py.
    nuclei_binary: str = Field(default="nuclei", alias="NUCLEI_BINARY")
    nuclei_templates_dir: str | None = Field(default=None, alias="NUCLEI_TEMPLATES_DIR")
    nuclei_default_tags: str = Field(
        default="exposure,misconfig,headers,tech,panel,files,tokens", alias="NUCLEI_DEFAULT_TAGS"
    )
    nuclei_default_severity: str = Field(default="info,low,medium", alias="NUCLEI_DEFAULT_SEVERITY")
    nuclei_allow_high: bool = Field(default=False, alias="NUCLEI_ALLOW_HIGH")
    nuclei_allow_critical: bool = Field(default=False, alias="NUCLEI_ALLOW_CRITICAL")
    nuclei_rate_limit: int = Field(default=5, alias="NUCLEI_RATE_LIMIT")
    # The safe-default profile (7 tags) loads ~3600 templates, clustered to
    # ~1500 requests; at the conservative default rate-limit of 5 req/s
    # that's ~5min minimum against a real target, hence the generous default.
    nuclei_timeout_seconds: int = Field(default=600, alias="NUCLEI_TIMEOUT_SECONDS")
    nuclei_max_targets: int = Field(default=20, alias="NUCLEI_MAX_TARGETS")
    nuclei_max_results: int = Field(default=100, alias="NUCLEI_MAX_RESULTS")
    nuclei_update_templates: bool = Field(default=False, alias="NUCLEI_UPDATE_TEMPLATES")

    # Playwright browser settings (only used when enable_playwright=True).
    playwright_headless: bool = Field(default=True, alias="PLAYWRIGHT_HEADLESS")
    playwright_timeout_ms: int = Field(default=15_000, alias="PLAYWRIGHT_TIMEOUT_MS")
    playwright_max_actions: int = Field(default=20, alias="PLAYWRIGHT_MAX_ACTIONS")
    playwright_max_requests: int = Field(default=200, alias="PLAYWRIGHT_MAX_REQUESTS")
    playwright_max_body_preview_bytes: int = Field(
        default=2_000, alias="PLAYWRIGHT_MAX_BODY_PREVIEW_BYTES"
    )
    playwright_screenshots_enabled: bool = Field(
        default=True, alias="PLAYWRIGHT_SCREENSHOTS_ENABLED"
    )

    # Agent behaviour
    max_iterations: int = Field(default=12, alias="HEXAGENT_MAX_ITERATIONS")
    require_human_approval: bool = Field(default=False, alias="HEXAGENT_REQUIRE_HUMAN_APPROVAL")
    require_sensitive_approval: bool = Field(
        default=False, alias="HEXAGENT_REQUIRE_SENSITIVE_APPROVAL"
    )

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
