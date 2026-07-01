"""Planner interface.

A planner turns an objective into a :class:`~app.models.plan.Plan` and can revise
an existing plan in light of new observations. Concrete planners (heuristic or
LLM-backed) implement this interface so the graph depends only on the abstraction.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from app.models.plan import Plan
from app.models.tool_io import ToolResult
from app.tools.registry import ToolRegistry


class BasePlanner(ABC):
    """Abstract base class for planning strategies."""

    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry

    @abstractmethod
    def create_plan(self, objective: str, target: str) -> Plan:
        """Produce an initial plan for ``objective`` against ``target``."""
        raise NotImplementedError

    @abstractmethod
    def replan(
        self,
        plan: Plan,
        reason: str,
        observations: list[str],
        last_result: ToolResult | None = None,
    ) -> Plan:
        """Return a revised plan given a replanning ``reason`` and observations.

        ``last_result`` is the :class:`ToolResult` that triggered the replan
        (when known), letting implementations react to its structured data
        instead of re-parsing free-text observations.

        Implementations should preserve already-completed steps and append or
        adjust pending ones rather than discarding progress.
        """
        raise NotImplementedError
