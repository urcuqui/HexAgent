"""Executor agent: selects a tool for a step and runs it.

Tool selection uses the LLM when available (via the executor prompt); otherwise
it falls back to the tool named on the step, or a sensible default. Execution is
always delegated to the :class:`~app.tools.registry.ToolRegistry`.
"""

from __future__ import annotations

import json
from typing import Any

from app.models.plan import PlanStep
from app.models.tool_io import ToolCall, ToolResult
from app.prompts import PromptLibrary
from app.tools.registry import ToolRegistry
from app.utils.logging import get_logger
from app.utils.parsing import extract_json

logger = get_logger(__name__)


class ExecutorAgent:
    """Chooses and invokes the appropriate tool for a single plan step."""

    def __init__(
        self, registry: ToolRegistry, llm: Any | None = None, prompts: PromptLibrary | None = None
    ) -> None:
        self._registry = registry
        self._llm = llm
        self._prompts = prompts or PromptLibrary()

    def select(self, step: PlanStep, target: str, observations: list[str]) -> ToolCall:
        """Decide which tool to run and with what arguments for ``step``."""
        if self._llm is not None:
            call = self._select_with_llm(step, target, observations)
            if call is not None:
                return call
        return self._select_heuristically(step, target)

    def execute(self, step: PlanStep, target: str, observations: list[str]) -> ToolResult:
        """Select and run a tool for ``step``, returning the structured result."""
        call = self.select(step, target, observations)
        logger.info("Executing step %s via tool '%s'", step.id, call.tool_name)
        result = self._registry.run(call.tool_name, **call.arguments)
        return result

    def _select_heuristically(self, step: PlanStep, target: str) -> ToolCall:
        tool_name = step.tool_name or "http_header_inspect"
        args = dict(step.arguments)
        args.setdefault("target", target)
        return ToolCall(tool_name=tool_name, arguments=args, rationale="Step-declared tool")

    def _select_with_llm(
        self, step: PlanStep, target: str, observations: list[str]
    ) -> ToolCall | None:
        prompt = self._prompts.executor.format(
            tool_catalogue=self._registry.catalogue(),
            target=target,
            step=step.model_dump_json(),
            observations=json.dumps(observations[-5:], indent=2) or "[]",
        )
        try:
            response = self._llm.invoke(prompt)
            data = extract_json(getattr(response, "content", str(response)))
            tool_name = data.get("tool_name")
            if not tool_name or self._registry.get(tool_name) is None:
                return None
            args = data.get("arguments") or {}
            args.setdefault("target", target)
            return ToolCall(tool_name=tool_name, arguments=args, rationale=data.get("rationale"))
        except Exception as exc:  # noqa: BLE001 - fall back to heuristic selection
            logger.warning("LLM tool selection failed (%s); using step default", exc)
            return None
