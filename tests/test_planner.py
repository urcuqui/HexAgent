"""Tests for the planning components."""

from __future__ import annotations

import logging

from app.models.findings import Observation
from app.models.plan import ReplanReason
from app.models.tool_io import ToolResult
from app.planners.planner import HeuristicPlanner, LLMPlanner, build_planner


class _UnparsableLLM:
    """Always returns prose with no valid JSON, to exercise the fallback path."""

    def invoke(self, prompt):
        class _Response:
            content = "Sure, here is my plan: I will start with {some prose}."

        return _Response()


def test_heuristic_plan_structure(registry):
    planner = HeuristicPlanner(registry)
    plan = planner.create_plan("Recon", "example.com")
    # The initial plan is deliberately minimal: scan first, decide later.
    assert len(plan.steps) == 2
    assert plan.steps[0].tool_name == "port_scan"
    final = plan.steps[-1]
    assert final.tool_name is None
    assert set(final.depends_on) == {plan.steps[0].id}


def test_heuristic_plan_prefers_nmap_when_registered(registry):
    from app.tools.nmap_tool import NmapScanTool

    registry.register(NmapScanTool())
    plan = HeuristicPlanner(registry).create_plan("Recon", "example.com")
    assert plan.steps[0].tool_name == "nmap_scan"


def test_llm_planner_falls_back_and_logs_raw_output_on_unparsable_response(registry, caplog):
    planner = LLMPlanner(registry, llm=_UnparsableLLM())
    with caplog.at_level(logging.DEBUG, logger="app.planners.planner"):
        plan = planner.create_plan("Recon", "example.com")

    # Still gets a usable plan via the heuristic fallback.
    assert plan.steps[0].tool_name == "port_scan"
    assert any("LLM planning failed" in r.message for r in caplog.records)
    assert any("Raw LLM planner output was" in r.message for r in caplog.records)


def test_heuristic_plan_tool_steps_carry_target(registry):
    plan = HeuristicPlanner(registry).create_plan("Recon", "example.com")
    for step in plan.steps:
        if step.tool_name is not None:
            assert step.arguments.get("target") == "example.com"


def test_build_planner_defaults_to_heuristic(registry):
    planner = build_planner(registry, llm=None)
    assert isinstance(planner, HeuristicPlanner)


def test_next_runnable_respects_dependencies(registry):
    plan = HeuristicPlanner(registry).create_plan("Recon", "example.com")
    # The synthesis step must not be runnable until its dependencies complete.
    first = plan.next_runnable()
    assert first is plan.steps[0]


def _port_scan_result(ports: list[int]) -> ToolResult:
    return ToolResult.ok(
        "port_scan", "scan", {"open_ports": [{"port": p, "state": "open"} for p in ports]}
    )


def test_replan_without_last_result_is_noop(registry):
    plan = HeuristicPlanner(registry).create_plan("Recon", "example.com")
    before = len(plan.steps)
    revised = HeuristicPlanner(registry).replan(plan, ReplanReason.OPEN_WEB_PORTS_FOUND, [])
    assert len(revised.steps) == before


def test_replan_on_open_web_ports_found_queues_http_phase(registry):
    planner = HeuristicPlanner(registry)
    plan = planner.create_plan("Recon", "example.com")
    result = _port_scan_result([80, 443])
    revised = planner.replan(plan, ReplanReason.OPEN_WEB_PORTS_FOUND, [], result)
    tool_names = [s.tool_name for s in revised.steps]
    for expected in (
        "tech_fingerprint",
        "http_header_inspect",
        "security_headers",
        "robots_txt",
        "url_crawler",
    ):
        assert expected in tool_names
    # Summary step must now depend on every queued tool step.
    summary = revised.steps[-1]
    assert summary.tool_name is None
    assert set(summary.depends_on) == {s.id for s in revised.steps if s.tool_name is not None}


def test_replan_skips_http_phase_when_no_web_ports_open(registry):
    """Decision logic: no 80/443 open -> don't bother with HTTP-layer analysis."""
    planner = HeuristicPlanner(registry)
    plan = planner.create_plan("Recon", "example.com")
    result = _port_scan_result([22])
    revised = planner.replan(plan, ReplanReason.OPEN_WEB_PORTS_FOUND, [], result)
    assert len(revised.steps) == len(plan.steps)
    assert not any(s.tool_name == "tech_fingerprint" for s in revised.steps)


def test_replan_is_idempotent_for_http_phase(registry):
    planner = HeuristicPlanner(registry)
    plan = planner.create_plan("Recon", "example.com")
    result = _port_scan_result([80, 443])
    once = planner.replan(plan, ReplanReason.OPEN_WEB_PORTS_FOUND, [], result)
    twice = planner.replan(once, ReplanReason.OPEN_WEB_PORTS_FOUND, [], result)
    assert len(twice.steps) == len(once.steps)


def test_replan_on_robots_paths_found_queues_targeted_get(registry):
    planner = HeuristicPlanner(registry)
    plan = planner.create_plan("Recon", "example.com")
    result = ToolResult.ok("robots_txt", "found", {"disallowed_paths": ["/admin", "/private"]})
    revised = planner.replan(plan, ReplanReason.ROBOTS_PATHS_FOUND, [], result)
    get_steps = [s for s in revised.steps if s.tool_name == "http_get"]
    assert len(get_steps) == 1
    assert get_steps[0].arguments["path"] == "/admin"


def test_replan_on_login_endpoint_found_queues_controlled_post(registry):
    planner = HeuristicPlanner(registry)
    plan = planner.create_plan("Recon", "example.com")
    result = ToolResult.ok(
        "url_crawler",
        "found",
        {"interesting_urls": ["https://example.com/login", "https://example.com/admin"]},
    )
    revised = planner.replan(plan, ReplanReason.LOGIN_ENDPOINT_FOUND, [], result)
    post_steps = [s for s in revised.steps if s.tool_name == "http_post"]
    assert len(post_steps) == 1
    assert post_steps[0].arguments["path"] == "/login"


def test_observation_model_roundtrip():
    obs = Observation(source_tool="robots_txt", content="x")
    assert obs.model_dump()["source_tool"] == "robots_txt"
