"""Planner agent.

A thin orchestration wrapper around a :class:`~app.planners.base.BasePlanner`.
Keeping it separate from the planner implementation lets the graph node depend
on a stable agent surface while planning strategies evolve independently.
"""

from __future__ import annotations

from typing import Any

from app.models.plan import Plan
from app.models.tool_io import ToolResult
from app.planners.base import BasePlanner
from app.planners.planner import build_planner
from app.tools.registry import ToolRegistry
from app.utils.logging import get_logger

logger = get_logger(__name__)


class PlannerAgent:
    """Creates and revises plans on behalf of the graph."""

    def __init__(self, registry: ToolRegistry, llm: Any | None = None) -> None:
        self._planner: BasePlanner = build_planner(registry, llm)

    def plan(self, objective: str, target: str) -> Plan:
        """Generate an initial plan."""
        logger.info("Planning for objective=%r target=%r", objective, target)
        plan = self._planner.create_plan(objective, target)
        logger.info("Plan created with %d step(s)", len(plan.steps))
        return plan

    def replan(
        self,
        plan: Plan,
        reason: str,
        observations: list[str],
        last_result: ToolResult | None = None,
    ) -> Plan:
        """Revise an existing plan given new information."""
        logger.info("Replanning due to: %s", reason)
        return self._planner.replan(plan, reason, observations, last_result)
