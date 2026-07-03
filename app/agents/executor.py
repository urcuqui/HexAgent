"""Executor agent: selects a tool for a step and dispatches it to a specialist.

Tool selection uses the LLM when available (via the executor prompt); otherwise
it falls back to the tool named on the step, or a sensible default. Running the
chosen tool is delegated to the domain specialist that owns it (recon vs. HTTP
analysis), which also enforces the human-approval gate for sensitive tools.
"""

from __future__ import annotations

import json
from typing import Any

from app.agents.specialists import (
    ApprovalCallback,
    BrowserAgent,
    HttpAnalysisAgent,
    ReconAgent,
    SpecialistAgent,
)
from app.models.plan import PlanStep
from app.models.tool_io import ToolCall, ToolResult
from app.prompts import PromptLibrary
from app.tools.registry import ToolRegistry
from app.utils.llm import invoke_json
from app.utils.logging import get_logger
from app.utils.parsing import extract_json

logger = get_logger(__name__)


class ExecutorAgent:
    """Chooses a tool for a step and dispatches it to the owning specialist."""

    def __init__(
        self,
        registry: ToolRegistry,
        llm: Any | None = None,
        prompts: PromptLibrary | None = None,
        approval_callback: ApprovalCallback | None = None,
        require_sensitive_approval: bool = False,
    ) -> None:
        self._registry = registry
        self._llm = llm
        self._prompts = prompts or PromptLibrary()
        self._specialists: list[SpecialistAgent] = [
            ReconAgent(registry, approval_callback, require_sensitive_approval),
            HttpAnalysisAgent(registry, approval_callback, require_sensitive_approval),
            BrowserAgent(registry, approval_callback, require_sensitive_approval),
        ]

    def select(self, step: PlanStep, target: str, observations: list[str]) -> ToolCall:
        """Decide which tool to run and with what arguments for ``step``."""
        if self._llm is not None:
            call = self._select_with_llm(step, target, observations)
            if call is not None:
                return call
        return self._select_heuristically(step, target)

    def execute(self, step: PlanStep, target: str, observations: list[str]) -> ToolResult:
        """Select a tool for ``step`` and run it via its owning specialist."""
        call = self.select(step, target, observations)
        logger.info("Executing step %s via tool '%s'", step.id, call.tool_name)
        specialist = next((s for s in self._specialists if s.owns(call.tool_name)), None)
        if specialist is not None:
            return specialist.run(call)
        # Fallback for any tool not yet assigned to a specialist (e.g. a new
        # extension) — keeps the executor forward-compatible without a change
        # here for every future tool.
        return self._registry.run(call.tool_name, **call.arguments)

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
        text = None
        try:
            text = invoke_json(self._llm, prompt)
            data = extract_json(text)
            tool_name = data.get("tool_name")
            if not tool_name or self._registry.get(tool_name) is None:
                return None
            args = data.get("arguments") or {}
            args.setdefault("target", target)
            return ToolCall(tool_name=tool_name, arguments=args, rationale=data.get("rationale"))
        except Exception as exc:  # noqa: BLE001 - fall back to heuristic selection
            logger.warning("LLM tool selection failed (%s); using step default", exc)
            if text is not None:
                logger.debug("Raw LLM executor output was: %r", text)
            return None
