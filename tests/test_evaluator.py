"""Tests for the evaluator agent's heuristic interpretation."""

from __future__ import annotations

from app.agents.evaluator import EvaluatorAgent
from app.models.findings import Severity
from app.models.plan import PlanStep


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
    assert any(f.requires_human_validation for f in evaluation.findings)


def test_tech_fingerprint_is_informational(registry):
    evaluation, _ = _eval_for(registry, "tech_fingerprint")
    assert any(f.title == "Technology disclosure" for f in evaluation.findings)


def test_evaluation_always_records_observation(registry):
    evaluation, _ = _eval_for(registry, "http_header_inspect")
    assert len(evaluation.observations) >= 1
