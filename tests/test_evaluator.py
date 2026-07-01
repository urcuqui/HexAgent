"""Tests for the evaluator agent's heuristic interpretation."""

from __future__ import annotations

from app.agents.evaluator import EvaluatorAgent
from app.models.findings import Severity
from app.models.plan import PlanStep, ReplanReason
from app.models.tool_io import ToolResult


def _eval_for(registry, tool_name: str):
    step = PlanStep(description="x", tool_name=tool_name, arguments={"target": "example.com"})
    result = registry.run(tool_name, target="example.com")
    return EvaluatorAgent().evaluate("Recon", step, result), result


def test_security_headers_yields_medium_finding(registry):
    evaluation, result = _eval_for(registry, "security_headers")
    if result.data.get("missing"):
        assert any(f.severity is Severity.MEDIUM for f in evaluation.findings)


def test_robots_requests_replan_and_human_validation(registry):
    evaluation, _ = _eval_for(registry, "robots_txt")
    assert evaluation.needs_replan is True
    assert evaluation.replan_reason == ReplanReason.ROBOTS_PATHS_FOUND
    assert any(f.requires_human_validation for f in evaluation.findings)


def test_tech_fingerprint_is_informational(registry):
    evaluation, _ = _eval_for(registry, "tech_fingerprint")
    assert any(f.title == "Technology disclosure" for f in evaluation.findings)


def test_evaluation_always_records_observation(registry):
    evaluation, _ = _eval_for(registry, "http_header_inspect")
    assert len(evaluation.observations) >= 1


def test_port_scan_with_web_ports_requests_replan(registry):
    # The mock port_scan fixture always includes 80/443, exercising the
    # "if Nmap finds 80/443 -> use HTTP tools" decision logic end to end.
    evaluation, result = _eval_for(registry, "port_scan")
    assert {80, 443} & {p["port"] for p in result.data["open_ports"]}
    assert evaluation.needs_replan is True
    assert evaluation.replan_reason == ReplanReason.OPEN_WEB_PORTS_FOUND


def test_port_scan_without_web_ports_does_not_request_replan():
    step = PlanStep(description="x", tool_name="port_scan", arguments={"target": "example.com"})
    result = ToolResult.ok("port_scan", "scan", {"open_ports": [{"port": 22, "state": "open"}]})
    evaluation = EvaluatorAgent().evaluate("Recon", step, result)
    assert evaluation.needs_replan is False


def test_port_scan_flags_sensitive_ports_as_finding():
    step = PlanStep(description="x", tool_name="port_scan", arguments={"target": "example.com"})
    result = ToolResult.ok(
        "port_scan",
        "scan",
        {"open_ports": [{"port": 22, "state": "open"}, {"port": 80, "state": "open"}]},
    )
    evaluation = EvaluatorAgent().evaluate("Recon", step, result)
    assert any(f.title == "Sensitive service exposure" for f in evaluation.findings)


def test_url_crawler_with_login_requests_replan(registry):
    evaluation, result = _eval_for(registry, "url_crawler")
    if any("login" in u for u in result.data.get("interesting_urls", [])):
        assert evaluation.needs_replan is True
        assert evaluation.replan_reason == ReplanReason.LOGIN_ENDPOINT_FOUND


def test_skipped_result_yields_human_validation_finding():
    step = PlanStep(description="x", tool_name="http_post", arguments={"target": "example.com"})
    result = ToolResult.skipped("http_post", "requires human approval, which was not granted")
    evaluation = EvaluatorAgent().evaluate("Recon", step, result)
    assert any(f.title == "Sensitive action skipped" for f in evaluation.findings)
    assert any(f.requires_human_validation for f in evaluation.findings)
