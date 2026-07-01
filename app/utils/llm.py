"""LLM factory.

Wraps construction of an OpenAI-compatible chat model so the rest of the code
depends on a single seam. When no API key is configured (or mock mode is forced)
:meth:`LLMFactory.build` returns ``None`` and callers fall back to deterministic
offline behaviour. This keeps the POC runnable with zero external dependencies
while remaining trivially swappable for any OpenAI-compatible backend.
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, Any

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


_JSON_MODE_ATTR = "_hexagent_json_mode_supported"


def invoke_json(llm: Any, prompt: str) -> str:
    """Invoke ``llm`` requesting native JSON-mode output; return the raw text.

    Agents that expect a JSON object back (planner, executor, evaluator) use
    this instead of a plain ``llm.invoke(prompt)`` so the model is constrained
    to emit valid JSON directly (via the OpenAI-compatible ``response_format``
    param) rather than relying solely on prompt instructions — this is what
    :func:`app.utils.parsing.extract_json` was otherwise failing to recover
    from prose the model wrapped around the JSON.

    Some OpenAI-compatible gateways reject ``response_format`` outright (a
    real network round trip that returns a 400). Once that happens for a given
    ``llm`` instance, remember it (best-effort, via a plain attribute) so
    subsequent calls skip straight to a plain invoke instead of paying that
    failed round trip every single time for the rest of the run.
    """
    if getattr(llm, _JSON_MODE_ATTR, True):
        try:
            response = llm.bind(response_format={"type": "json_object"}).invoke(prompt)
        except Exception as exc:  # noqa: BLE001 - fall back to an unconstrained call
            logger.debug("JSON-mode invoke failed (%s); retrying without response_format", exc)
            _remember_json_mode_support(llm, supported=False)
        else:
            _remember_json_mode_support(llm, supported=True)
            return getattr(response, "content", str(response))
    response = llm.invoke(prompt)
    return getattr(response, "content", str(response))


def _remember_json_mode_support(llm: Any, *, supported: bool) -> None:
    with contextlib.suppress(Exception):  # caching is best-effort only
        setattr(llm, _JSON_MODE_ATTR, supported)
