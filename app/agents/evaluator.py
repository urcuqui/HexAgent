"""Evaluator agent: interprets a tool result into observations and findings.

Uses the LLM when available; otherwise applies a deterministic rule set keyed on
the tool name so the POC produces meaningful, educational findings offline.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.models.findings import Finding, Observation, Severity
from app.models.plan import PlanStep, ReplanReason
from app.models.tool_io import ToolResult, ToolStatus
from app.prompts import PromptLibrary
from app.utils.llm import invoke_json
from app.utils.logging import get_logger
from app.utils.parsing import extract_json

logger = get_logger(__name__)

# Ports that, if open, mean the target is worth analysing at the HTTP layer.
_WEB_PORTS = {80, 443}
# Ports the evaluator flags as sensitive regardless of the HTTP-layer decision.
_SENSITIVE_PORTS = {22, 3306}


class EvaluationResult(BaseModel):
    """Structured output of the evaluator for a single tool result."""

    observations: list[Observation] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)
    needs_replan: bool = False
    replan_reason: str = ""


class EvaluatorAgent:
    """Turns raw tool results into observations and security findings."""

    def __init__(self, llm: Any | None = None, prompts: PromptLibrary | None = None) -> None:
        self._llm = llm
        self._prompts = prompts or PromptLibrary()

    def evaluate(self, objective: str, step: PlanStep, result: ToolResult) -> EvaluationResult:
        """Evaluate ``result`` for ``step`` in the context of ``objective``."""
        if self._llm is not None:
            evaluation = self._evaluate_with_llm(objective, step, result)
            if evaluation is not None:
                return evaluation
        return self._evaluate_heuristically(step, result)

    def _evaluate_with_llm(
        self, objective: str, step: PlanStep, result: ToolResult
    ) -> EvaluationResult | None:
        prompt = self._prompts.evaluator.format(
            objective=objective, step=step.model_dump_json(), tool_result=result.model_dump_json()
        )
        text = None
        try:
            text = invoke_json(self._llm, prompt)
            data = extract_json(text)
            obs = [
                Observation(source_tool=result.tool_name, step_id=step.id, content=o)
                for o in data.get("observations", [])
            ]
            findings = [Finding(**f) for f in data.get("findings", [])]
            return EvaluationResult(
                observations=obs,
                findings=findings,
                needs_replan=bool(data.get("needs_replan")),
                replan_reason=data.get("replan_reason", ""),
            )
        except Exception as exc:  # noqa: BLE001 - fall back to heuristic evaluation
            logger.warning("LLM evaluation failed (%s); using heuristic evaluation", exc)
            if text is not None:
                logger.debug("Raw LLM evaluator output was: %r", text)
            return None

    def _evaluate_heuristically(self, step: PlanStep, result: ToolResult) -> EvaluationResult:
        data = result.data
        obs = [Observation(source_tool=result.tool_name, step_id=step.id, content=result.summary)]
        findings: list[Finding] = []
        needs_replan = False
        reason = ""

        def add(title: str, sev: Severity, desc: str, rec: str, human: bool = False) -> None:
            findings.append(
                Finding(
                    title=title,
                    severity=sev,
                    description=desc,
                    evidence=[result.summary],
                    recommendation=rec,
                    requires_human_validation=human,
                )
            )

        if result.status is ToolStatus.SKIPPED:
            add(
                "Sensitive action skipped",
                Severity.INFO,
                f"'{result.tool_name}' was not executed: {result.summary}",
                "Review whether this action should be approved and re-run manually.",
                human=True,
            )

        if result.tool_name == "security_headers" and data.get("missing"):
            missing = ", ".join(data["missing"])
            add(
                "Missing security headers",
                Severity.MEDIUM,
                f"Response is missing: {missing} (grade {data.get('grade')}).",
                "Add the missing headers to harden the application.",
            )
        if result.tool_name == "tech_fingerprint":
            add(
                "Technology disclosure",
                Severity.LOW,
                f"Stack disclosed: {', '.join(data.get('technologies', []))}.",
                "Suppress version banners where possible.",
            )
        if result.tool_name == "robots_txt" and data.get("disallowed_paths"):
            add(
                "Sensitive paths in robots.txt",
                Severity.INFO,
                f"Disallowed paths hint at: {', '.join(data['disallowed_paths'])}.",
                "Review whether these paths require authentication.",
                human=True,
            )
            needs_replan = True
            reason = ReplanReason.ROBOTS_PATHS_FOUND
        if result.tool_name == "url_crawler" and data.get("interesting_urls"):
            add(
                "Interesting endpoints discovered",
                Severity.LOW,
                f"Endpoints of interest: {', '.join(data['interesting_urls'])}.",
                "Confirm access controls on these endpoints.",
                human=True,
            )
            if any("login" in u for u in data["interesting_urls"]):
                needs_replan = True
                reason = ReplanReason.LOGIN_ENDPOINT_FOUND
        if result.tool_name in ("port_scan", "nmap_scan"):
            open_ports = {p.get("port") for p in data.get("open_ports", [])}
            risky = open_ports & _SENSITIVE_PORTS
            if risky:
                add(
                    "Sensitive service exposure",
                    Severity.MEDIUM,
                    f"Potentially sensitive ports open: {sorted(risky)}.",
                    "Restrict management/database ports via firewall or VPN.",
                    human=True,
                )
            # Decision logic: only bother with HTTP-layer analysis if there's
            # actually a web service listening — this is the "if the scan
            # finds 80/443 -> use HTTP tools" branch.
            if open_ports & _WEB_PORTS:
                needs_replan = True
                reason = ReplanReason.OPEN_WEB_PORTS_FOUND

        return EvaluationResult(
            observations=obs, findings=findings, needs_replan=needs_replan, replan_reason=reason
        )
