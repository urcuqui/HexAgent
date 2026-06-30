"""Tests for the planning components."""

from __future__ import annotations

from app.models.findings import Observation
from app.planners.planner import HeuristicPlanner, build_planner


def test_heuristic_plan_structure(registry):
    planner = HeuristicPlanner(registry)
    plan = planner.create_plan("Recon", "example.com")
    assert len(plan.steps) == 6
    # Final step is a tool-less synthesis step depending on all prior steps.
    final = plan.steps[-1]
    assert final.tool_name is None
    assert set(final.depends_on) == {s.id for s in plan.steps[:-1]}


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


def test_replan_inserts_targeted_get_on_interesting_observation(registry):
    planner = HeuristicPlanner(registry)
    plan = planner.create_plan("Recon", "example.com")
    before = len(plan.steps)
    revised = planner.replan(plan, "found admin", ["interesting endpoint /admin discovered"])
    assert len(revised.steps) == before + 1
    assert any(s.tool_name == "http_get" for s in revised.steps)


def test_observation_model_roundtrip():
    obs = Observation(source_tool="robots_txt", content="x")
    assert obs.model_dump()["source_tool"] == "robots_txt"
