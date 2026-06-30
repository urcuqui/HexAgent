"""Concrete planners: a deterministic heuristic planner and an LLM planner.

:func:`build_planner` selects the LLM planner when a model is available and
otherwise returns the heuristic planner, guaranteeing the POC always produces a
sensible plan offline.
"""

from __future__ import annotations

from typing import Any

from app.models.plan import Plan, PlanStep, StepStatus
from app.planners.base import BasePlanner
from app.prompts import PromptLibrary
from app.tools.registry import ToolRegistry
from app.utils.logging import get_logger
from app.utils.parsing import extract_json

logger = get_logger(__name__)

# Canonical recon recipe used by the heuristic planner and as an LLM fallback.
_CANONICAL_STEPS: list[tuple[str, str | None]] = [
    ("Identify technologies and server software", "tech_fingerprint"),
    ("Inspect HTTP response headers", "http_header_inspect"),
    ("Evaluate security headers", "security_headers"),
    ("Review robots.txt for disallowed paths", "robots_txt"),
    ("Discover endpoints by crawling", "url_crawler"),
    ("Summarise findings", None),
]


class HeuristicPlanner(BasePlanner):
    """Deterministic planner that emits a canonical reconnaissance plan."""

    def create_plan(self, objective: str, target: str) -> Plan:
        steps: list[PlanStep] = []
        for index, (desc, tool) in enumerate(_CANONICAL_STEPS, start=1):
            step_id = f"s{index}"
            depends = [s.id for s in steps] if tool is None else []
            args = {"target": target} if tool else {}
            steps.append(
                PlanStep(
                    id=step_id,
                    description=desc,
                    tool_name=tool,
                    arguments=args,
                    depends_on=depends,
                )
            )
        return Plan(objective=objective, steps=steps, rationale="Standard passive recon workflow.")

    def replan(self, plan: Plan, reason: str, observations: list[str]) -> Plan:
        # Append an extra targeted GET on the first interesting endpoint, if any.
        interesting = next(
            (o for o in observations if "interesting" in o.lower() or "/admin" in o.lower()), None
        )
        if interesting and not any(s.tool_name == "http_get" for s in plan.steps):
            new_id = f"s{len(plan.steps) + 1}"
            target = next((s.arguments.get("target") for s in plan.steps if s.arguments), "")
            plan.steps.insert(
                len(plan.steps) - 1,
                PlanStep(
                    id=new_id,
                    description=f"Inspect notable endpoint ({reason})",
                    tool_name="http_get",
                    arguments={"target": target, "path": "/admin"},
                    status=StepStatus.PENDING,
                ),
            )
            plan.rationale = f"Revised: {reason}"
        return plan


class LLMPlanner(BasePlanner):
    """LLM-backed planner that asks the model for a structured plan."""

    def __init__(
        self, registry: ToolRegistry, llm: Any, prompts: PromptLibrary | None = None
    ) -> None:
        super().__init__(registry)
        self._llm = llm
        self._prompts = prompts or PromptLibrary()
        self._fallback = HeuristicPlanner(registry)

    def create_plan(self, objective: str, target: str) -> Plan:
        prompt = self._prompts.planner.format(
            tool_catalogue=self._registry.catalogue(), objective=f"{objective} (target: {target})"
        )
        try:
            response = self._llm.invoke(prompt)
            data = extract_json(getattr(response, "content", str(response)))
            plan = self._plan_from_dict(objective, target, data)
            if plan.steps:
                return plan
        except Exception as exc:  # noqa: BLE001 - fall back gracefully
            logger.warning("LLM planning failed (%s); using heuristic plan", exc)
        return self._fallback.create_plan(objective, target)

    def replan(self, plan: Plan, reason: str, observations: list[str]) -> Plan:
        # For the POC, reuse the deterministic replanning logic.
        return self._fallback.replan(plan, reason, observations)

    def _plan_from_dict(self, objective: str, target: str, data: dict[str, Any]) -> Plan:
        steps: list[PlanStep] = []
        for raw in data.get("steps", []):
            tool = raw.get("tool_name")
            args = raw.get("arguments") or {}
            if tool and "target" not in args:
                args["target"] = target
            steps.append(
                PlanStep(
                    id=raw.get("id") or f"s{len(steps) + 1}",
                    description=raw.get("description", "(no description)"),
                    tool_name=tool,
                    arguments=args,
                    depends_on=raw.get("depends_on") or [],
                )
            )
        return Plan(objective=objective, steps=steps, rationale=data.get("rationale"))


def build_planner(registry: ToolRegistry, llm: Any | None = None) -> BasePlanner:
    """Return an :class:`LLMPlanner` when ``llm`` is provided, else heuristic."""
    if llm is not None:
        return LLMPlanner(registry, llm)
    return HeuristicPlanner(registry)
