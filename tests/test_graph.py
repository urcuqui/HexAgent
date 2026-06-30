"""End-to-end tests for the LangGraph workflow (offline/mock mode)."""

from __future__ import annotations

from app.graph.workflow import run_workflow


def test_workflow_completes_offline(offline_settings):
    state = run_workflow("Passive recon", "demo.thm.local", settings=offline_settings)
    assert state.plan is not None
    assert state.plan.is_complete()
    assert state.stopped_reason == "objective completed"
    assert state.report_markdown
    assert state.findings  # heuristic evaluation should surface findings


def test_workflow_report_has_required_sections(offline_settings):
    state = run_workflow("Passive recon", "demo.thm.local", settings=offline_settings)
    md = state.report_markdown
    for heading in (
        "# Reconnaissance Report",
        "## Plan",
        "## Executed Steps",
        "## Tool Outputs",
        "## Findings",
        "## Suggested Next Actions",
        "## Human Validation Points",
    ):
        assert heading in md


def test_iteration_cap_is_respected(offline_settings):
    offline_settings.max_iterations = 2
    state = run_workflow("Passive recon", "demo.thm.local", settings=offline_settings)
    assert state.iterations <= 2
    assert state.stopped_reason == "maximum iterations reached"


def test_human_approval_pauses_before_report(offline_settings):
    offline_settings.require_human_approval = True
    state = run_workflow("Passive recon", "demo.thm.local", settings=offline_settings)
    assert state.awaiting_human is True
    assert state.stopped_reason == "awaiting human approval"
    # A report is still produced for human review.
    assert state.report_markdown


def test_workflow_is_deterministic_offline(offline_settings):
    a = run_workflow("Passive recon", "demo.thm.local", settings=offline_settings)
    b = run_workflow("Passive recon", "demo.thm.local", settings=offline_settings)
    assert [f.title for f in a.findings] == [f.title for f in b.findings]
