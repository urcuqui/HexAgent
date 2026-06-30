"""Abstract base class for all HexAgent tools.

Tools follow a small, uniform contract (Liskov-friendly): subclasses implement
:meth:`_run` and the base class handles timing, error wrapping and producing a
:class:`~app.models.tool_io.ToolResult`. This keeps the executor agnostic to the
concrete tool and makes adding real tools (nmap, nuclei, ...) a matter of writing
a new subclass — no changes to the orchestration layer.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from typing import Any

from app.models.tool_io import ToolResult
from app.utils.logging import get_logger

logger = get_logger(__name__)


class BaseTool(ABC):
    """Common behaviour for every tool.

    Attributes:
        name: Unique registry key used by planners/executors.
        description: One-line human description shown in the tool catalogue.
        argument_help: Mapping of argument name -> short description, used to
            build the catalogue presented to the LLM.
    """

    name: str = "base"
    description: str = "Abstract tool"
    argument_help: dict[str, str] = {}

    @abstractmethod
    def _run(self, **kwargs: Any) -> ToolResult:
        """Execute the tool's logic and return a structured result."""
        raise NotImplementedError

    def run(self, **kwargs: Any) -> ToolResult:
        """Execute the tool, measuring duration and capturing failures.

        Any exception raised by :meth:`_run` is converted into an error
        ``ToolResult`` so a single misbehaving tool never crashes the graph.
        """
        start = time.perf_counter()
        logger.info("Running tool '%s' with args=%s", self.name, kwargs)
        try:
            result = self._run(**kwargs)
        except Exception as exc:  # noqa: BLE001 - deliberately defensive at the seam
            logger.exception("Tool '%s' raised an exception", self.name)
            return ToolResult.fail(self.name, str(exc))
        result.duration_ms = round((time.perf_counter() - start) * 1000, 2)
        return result

    def catalogue_entry(self) -> str:
        """Return a one-line catalogue description including its arguments."""
        args = ", ".join(f"{k} ({v})" for k, v in self.argument_help.items()) or "none"
        return f"- {self.name}: {self.description} | args: {args}"
