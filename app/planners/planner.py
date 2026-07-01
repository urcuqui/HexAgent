"""Concrete planners: a deterministic heuristic planner and an LLM planner.

:func:`build_planner` selects the LLM planner when a model is available and
otherwise returns the heuristic planner, guaranteeing the POC always produces a
sensible plan offline.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from app.models.plan import Plan, PlanStep, ReplanReason
from app.models.tool_io import ToolResult
from app.planners.base import BasePlanner
from app.prompts import PromptLibrary
from app.tools.registry import ToolRegistry
from app.utils.llm import invoke_json
from app.utils.logging import get_logger
from app.utils.parsing import extract_json

logger = get_logger(__name__)

# Ports that make the HTTP-layer phase worth queueing.
_WEB_PORTS = {80, 443}

# Preferred initial scan tool, in order: use the real nmap when it has been
# opted into (HEXAGENT_ENABLE_NMAP=true registers it), otherwise the mock.
_SCAN_TOOL_PREFERENCE = ("nmap_scan", "port_scan")

# Steps queued once a port scan reveals a listening web service — the
# "if Nmap finds 80/443 -> use HTTP tools" decision from the design brief.
_HTTP_PHASE_STEPS: list[tuple[str, str]] = [
    ("Identify technologies and server software", "tech_fingerprint"),
    ("Inspect HTTP response headers", "http_header_inspect"),
    ("Evaluate security headers", "security_headers"),
    ("Review robots.txt for disallowed paths", "robots_txt"),
    ("Discover endpoints by crawling", "url_crawler"),
]


class HeuristicPlanner(BasePlanner):
    """Deterministic planner that reacts to results instead of front-loading
    a fixed recipe: it starts with a port scan, then grows the plan based on
    what that (and later steps) reveal.
    """

    def create_plan(self, objective: str, target: str) -> Plan:
        scan_tool = next(
            (t for t in _SCAN_TOOL_PREFERENCE if self._registry.get(t) is not None), "port_scan"
        )
        scan_step = PlanStep(
            id="s1",
            description="Scan for open ports",
            tool_name=scan_tool,
            arguments={"target": target},
        )
        summary_step = PlanStep(
            id="s2", description="Summarise findings", tool_name=None, depends_on=[scan_step.id]
        )
        return Plan(
            objective=objective,
            steps=[scan_step, summary_step],
            rationale="Start with a port scan; grow the plan based on what it reveals.",
        )

    def replan(
        self,
        plan: Plan,
        reason: str,
        observations: list[str],
        last_result: ToolResult | None = None,
    ) -> Plan:
        if last_result is None:
            return plan
        if reason == ReplanReason.OPEN_WEB_PORTS_FOUND:
            return self._on_open_web_ports(plan, last_result)
        if reason == ReplanReason.ROBOTS_PATHS_FOUND:
            return self._on_robots_paths(plan, last_result)
        if reason == ReplanReason.LOGIN_ENDPOINT_FOUND:
            return self._on_login_endpoint(plan, last_result)
        return plan

    def _on_open_web_ports(self, plan: Plan, result: ToolResult) -> Plan:
        # Re-verify the decision here rather than trusting the caller: this
        # handler is the actual "if 80/443 open -> use HTTP tools" branch.
        open_ports = {p.get("port") for p in result.data.get("open_ports", [])}
        if not (open_ports & _WEB_PORTS):
            plan.rationale = "No web ports (80/443) open; skipping HTTP-layer analysis."
            return plan
        if any(s.tool_name == "tech_fingerprint" for s in plan.steps):
            return plan  # already queued
        target = self._target_from_plan(plan)
        new_steps = [
            PlanStep(
                id=f"s{len(plan.steps) + i + 1}",
                description=desc,
                tool_name=tool,
                arguments={"target": target},
            )
            for i, (desc, tool) in enumerate(_HTTP_PHASE_STEPS)
        ]
        plan.rationale = "Open web port(s) found; queued HTTP-layer analysis."
        return self._insert_before_summary(plan, new_steps)

    def _on_robots_paths(self, plan: Plan, result: ToolResult) -> Plan:
        disallowed = result.data.get("disallowed_paths") or []
        if not disallowed:
            return plan
        path = disallowed[0]
        if any(s.tool_name == "http_get" and s.arguments.get("path") == path for s in plan.steps):
            return plan  # already queued
        target = self._target_from_plan(plan)
        step = PlanStep(
            id=f"s{len(plan.steps) + 1}",
            description=f"Inspect disallowed path {path}",
            tool_name="http_get",
            arguments={"target": target, "path": path},
        )
        plan.rationale = f"robots.txt disallowed {path!r}; inspecting it directly."
        return self._insert_before_summary(plan, [step])

    def _on_login_endpoint(self, plan: Plan, result: ToolResult) -> Plan:
        if any(s.tool_name == "http_post" for s in plan.steps):
            return plan  # already queued
        login_url = next((u for u in result.data.get("interesting_urls", []) if "login" in u), None)
        if login_url is None:
            return plan
        path = urlparse(login_url).path or "/login"
        target = self._target_from_plan(plan)
        step = PlanStep(
            id=f"s{len(plan.steps) + 1}",
            description=f"Submit a controlled POST to {path}",
            tool_name="http_post",
            arguments={"target": target, "path": path, "data": {"probe": "hexagent"}},
        )
        plan.rationale = f"Login endpoint discovered ({path}); queued a controlled POST."
        return self._insert_before_summary(plan, [step])

    @staticmethod
    def _insert_before_summary(plan: Plan, new_steps: list[PlanStep]) -> Plan:
        summary = next((s for s in plan.steps if s.tool_name is None), None)
        insert_at = plan.steps.index(summary) if summary is not None else len(plan.steps)
        plan.steps[insert_at:insert_at] = new_steps
        if summary is not None:
            summary.depends_on = list({*summary.depends_on, *(s.id for s in new_steps)})
        return plan

    @staticmethod
    def _target_from_plan(plan: Plan) -> str:
        return next((s.arguments["target"] for s in plan.steps if s.arguments.get("target")), "")


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
        text = None
        try:
            text = invoke_json(self._llm, prompt)
            data = extract_json(text)
            plan = self._plan_from_dict(objective, target, data)
            if plan.steps:
                return plan
        except Exception as exc:  # noqa: BLE001 - fall back gracefully
            logger.warning("LLM planning failed (%s); using heuristic plan", exc)
            if text is not None:
                logger.debug("Raw LLM planner output was: %r", text)
        return self._fallback.create_plan(objective, target)

    def replan(
        self,
        plan: Plan,
        reason: str,
        observations: list[str],
        last_result: ToolResult | None = None,
    ) -> Plan:
        # For the POC, reuse the deterministic replanning logic.
        return self._fallback.replan(plan, reason, observations, last_result)

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
