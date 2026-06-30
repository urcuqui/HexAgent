"""LLM factory.

Wraps construction of an OpenAI-compatible chat model so the rest of the code
depends on a single seam. When no API key is configured (or mock mode is forced)
:meth:`LLMFactory.build` returns ``None`` and callers fall back to deterministic
offline behaviour. This keeps the POC runnable with zero external dependencies
while remaining trivially swappable for any OpenAI-compatible backend.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.config import Settings, get_settings
from app.utils.logging import get_logger

if TYPE_CHECKING:  # pragma: no cover - typing only
    from langchain_openai import ChatOpenAI

logger = get_logger(__name__)


class LLMFactory:
    """Builds chat models from :class:`~app.config.Settings`.

    Injecting this factory (rather than importing a model directly) lets tests
    and alternative deployments swap the backend without touching agent code.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    @property
    def enabled(self) -> bool:
        """Whether a real LLM is available given the current settings."""
        return self._settings.use_llm

    def build(self) -> ChatOpenAI | None:
        """Return a configured chat model, or ``None`` for offline/mock mode."""
        if not self._settings.use_llm:
            logger.info(
                "LLM disabled (no API key or mock mode); using deterministic offline agents."
            )
            return None

        from langchain_openai import ChatOpenAI

        logger.info("Building ChatOpenAI model '%s'", self._settings.model)
        return ChatOpenAI(
            model=self._settings.model,
            temperature=self._settings.temperature,
            api_key=self._settings.openai_api_key,
            base_url=self._settings.openai_base_url,
            timeout=60,
            max_retries=2,
        )


def build_llm(settings: Settings | None = None) -> ChatOpenAI | None:
    """Convenience wrapper around :meth:`LLMFactory.build`."""
    return LLMFactory(settings).build()
