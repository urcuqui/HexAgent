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
    # Browser-specific state carried back to the graph node.
    new_screenshots: list[str] = Field(default_factory=list)
    browser_session_active: bool | None = None  # None = no change


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

        # -- Browser tool heuristics ------------------------------------------
        new_screenshots: list[str] = []
        browser_session_active: bool | None = None

        if result.tool_name == "browser_open":
            browser_session_active = data.get("success", False)
            title = data.get("title") or ""
            url = data.get("current_url") or ""
            api_hints = data.get("potential_api_endpoints") or []
            auth_indicators = data.get("auth_indicators") or []
            forms = data.get("forms") or []
            screenshot = data.get("screenshot_path")
            if screenshot:
                new_screenshots.append(screenshot)

            obs.append(
                Observation(
                    source_tool=result.tool_name,
                    step_id=step.id if step else None,
                    content=f"Browser opened {url!r} (title={title!r}). "
                    f"Auth indicators: {auth_indicators}. "
                    f"API hints: {api_hints}. "
                    f"Forms found: {len(forms)}.",
                )
            )
            # Surface API endpoints as a finding.
            if api_hints:
                add(
                    "Browser-discovered API endpoints",
                    Severity.INFO,
                    f"JavaScript-rendered links hint at API endpoints: {', '.join(api_hints[:10])}.",
                    "Replay these requests with the HTTP validation tool to confirm access controls.",
                )
            # Trigger a replan to queue browser_login when a login form is present.
            has_password_field = any(
                f.get("type") == "password"
                for form in forms
                for f in form.get("fields", [])
            )
            if has_password_field or any(
                "password" in str(ind).lower() for ind in auth_indicators
            ):
                needs_replan = True
                reason = ReplanReason.BROWSER_LOGIN_FORM_FOUND

        if result.tool_name == "browser_analyze_page":
            browser_session_active = True
            url = data.get("current_url") or ""
            api_hints = data.get("potential_api_endpoints") or []
            screenshot = data.get("screenshot_path")
            if screenshot:
                new_screenshots.append(screenshot)
            obs.append(
                Observation(
                    source_tool=result.tool_name,
                    step_id=step.id if step else None,
                    content=f"Browser page analysis at {url!r}. "
                    f"API hints: {api_hints}. "
                    f"Network requests captured: "
                    f"{len(data.get('network_requests') or [])}.",
                )
            )
            if api_hints:
                add(
                    "Browser-observed API endpoints",
                    Severity.INFO,
                    f"API-like endpoints observed in browser session: {', '.join(api_hints[:10])}.",
                    "Replay these requests with the HTTP validation tool.",
                )

        if result.tool_name == "browser_login":
            browser_session_active = data.get("success", False)
            url = data.get("current_url") or ""
            screenshot = data.get("screenshot_path")
            if screenshot:
                new_screenshots.append(screenshot)
            outcome = "succeeded" if data.get("success") else "failed"
            obs.append(
                Observation(
                    source_tool=result.tool_name,
                    step_id=step.id if step else None,
                    content=f"Browser login {outcome}; now at {url!r}. "
                    f"Network requests captured: "
                    f"{len(data.get('network_requests') or [])}.",
                )
            )
            # Surface API calls captured post-login as findings.
            api_reqs = [
                r["url"]
                for r in (data.get("network_requests") or [])
                if any(k in r.get("url", "") for k in ("/api/", "/graphql", "/v1/", "/v2/"))
            ]
            if api_reqs:
                add(
                    "Post-login API requests captured",
                    Severity.INFO,
                    f"API endpoints observed after authentication: {', '.join(api_reqs[:10])}.",
                    "Replay these authenticated requests with the HTTP validation tool to test "
                    "authorisation controls.",
                    human=True,
                )

        if result.tool_name == "browser_screenshot":
            browser_session_active = True
            path = data.get("screenshot_path")
            if path:
                new_screenshots.append(path)
                obs.append(
                    Observation(
                        source_tool=result.tool_name,
                        step_id=step.id if step else None,
                        content=f"Evidence screenshot saved: {path}.",
                    )
                )

        if result.tool_name == "browser_close":
            browser_session_active = False
            obs.append(
                Observation(
                    source_tool=result.tool_name,
                    step_id=step.id if step else None,
                    content="Browser session closed.",
                )
            )

        return EvaluationResult(
            observations=obs,
            findings=findings,
            needs_replan=needs_replan,
            replan_reason=reason,
            new_screenshots=new_screenshots,
            browser_session_active=browser_session_active,
        )
