"""Tests for the Plan/PlanStep dependency-resolution model."""

from __future__ import annotations

from app.models.plan import Plan, PlanStep, StepStatus


def _plan_with(dependency_status: StepStatus) -> Plan:
    dep = PlanStep(
        id="dep", description="dependency", tool_name="http_get", status=dependency_status
    )
    dependent = PlanStep(id="dependent", description="summary", tool_name=None, depends_on=["dep"])
    return Plan(objective="x", steps=[dep, dependent])


def test_dependent_step_runnable_after_dependency_succeeds():
    plan = _plan_with(StepStatus.DONE)
    assert plan.next_runnable() is plan.steps[1]


def test_dependent_step_runnable_after_dependency_fails():
    # A failed prerequisite must not permanently block a step that depends on
    # it (e.g. the final synthesis step) — it should still resolve, just not
    # with fresh data from that step.
    plan = _plan_with(StepStatus.FAILED)
    assert plan.next_runnable() is plan.steps[1]


def test_dependent_step_runnable_after_dependency_skipped():
    # Same as above, for a step skipped by the human-approval gate.
    plan = _plan_with(StepStatus.SKIPPED)
    assert plan.next_runnable() is plan.steps[1]


def test_dependent_step_not_runnable_while_dependency_pending():
    plan = _plan_with(StepStatus.PENDING)
    assert plan.next_runnable() is plan.steps[0]
