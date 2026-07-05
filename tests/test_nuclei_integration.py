"""End-to-end integration test for the Nuclei extension.

Drives the *existing* planner/evaluator/executor pipeline (no separate
workflow) through the flow described in the design brief:

    port_scan finds 80/443 open
      -> planner queues HTTP phase + nuclei_scan_url
      -> nuclei_scan_url returns a JSONL candidate finding (mocked binary)
      -> evaluator stores it as a candidate Finding, requests a replan
      -> planner queues http_get to validate the matched URL
      -> http_get (mock, deterministic) confirms it
      -> evaluator marks the finding validated

No live nuclei binary or network access is required: subprocess.run/
shutil.which are monkeypatched with a fixture JSONL payload, exactly like
tests/test_nuclei_tool.py.
"""

from __future__ import annotations

import json
import shutil
import subprocess

from app.agents.evaluator import EvaluatorAgent
from app.agents.executor import ExecutorAgent
from app.models.plan import PlanStep, ReplanReason, StepStatus
from app.models.tool_io import ToolStatus
from app.planners.planner import HeuristicPlanner
from app.tools.registry import default_registry

_TARGET = "demo.thm.local"

_NUCLEI_RECORD = {
    "template-id": "exposed-admin-panel",
    "info": {
        "name": "Exposed Admin Panel",
        "severity": "info",
        "description": "An admin panel was detected without authentication.",
        "tags": ["panel", "exposure"],
    },
    "matched-at": f"https://{_TARGET}/admin",
    "matcher-name": "panel-detect",
    "extracted-results": [],
}


def _mock_nuclei(monkeypatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/nuclei")

    def fake_run(command, **kwargs):
        if "-o" in command:
            out_path = command[command.index("-o") + 1]
            with open(out_path, "w") as fh:
                fh.write(json.dumps(_NUCLEI_RECORD) + "\n")
        return subprocess.CompletedProcess(command, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)


def test_candidate_to_validated_flow_through_existing_agents(monkeypatch):
    _mock_nuclei(monkeypatch)

    registry = default_registry(enable_nuclei=True)  # mock_mode stays default (True)
    planner = HeuristicPlanner(registry)
    executor = ExecutorAgent(registry)
    evaluator = EvaluatorAgent()

    # 1. Existing planner: initial plan is just a port scan + summary.
    plan = planner.create_plan("Recon", _TARGET)
    scan_step = plan.next_runnable()
    assert scan_step.tool_name == "port_scan"

    # 2. Existing reconnaissance discovers HTTP (mock port_scan always opens
    # 80/443 -- see app/tools/fixtures.py _OPEN_PORTS).
    scan_result = executor.execute(scan_step, _TARGET, [])
    assert scan_result.status is ToolStatus.SUCCESS
    scan_step.status = StepStatus.DONE

    evaluation = evaluator.evaluate("Recon", scan_step, scan_result)
    assert evaluation.needs_replan
    assert evaluation.replan_reason == ReplanReason.OPEN_WEB_PORTS_FOUND

    # 3. Planner selects nuclei_scan_url (queued alongside the HTTP phase).
    plan = planner.replan(plan, evaluation.replan_reason, [], scan_result)
    nuclei_step = next(s for s in plan.steps if s.tool_name == "nuclei_scan_url")
    assert nuclei_step is not None

    # 4. Nuclei "runs" (mocked binary) and returns a JSONL candidate finding.
    nuclei_result = executor.execute(nuclei_step, _TARGET, [])
    assert nuclei_result.status is ToolStatus.SUCCESS
    assert nuclei_result.data["result_count"] == 1
    nuclei_step.status = StepStatus.DONE

    # 5. State stores it as a candidate (never auto-confirmed).
    evaluation2 = evaluator.evaluate("Recon", nuclei_step, nuclei_result)
    candidate = next(f for f in evaluation2.findings if f.validation_status == "candidate")
    assert candidate.title.startswith("Candidate:")
    assert evaluation2.needs_replan
    assert evaluation2.replan_reason == ReplanReason.NUCLEI_CANDIDATE_FOUND

    # 6. Planner selects existing HTTP validation for the matched URL.
    plan = planner.replan(plan, evaluation2.replan_reason, [], nuclei_result)
    validate_step = next(
        s for s in plan.steps if s.tool_name == "http_get" and s.arguments.get("path") == "/admin"
    )
    assert "Validate Nuclei candidate" in validate_step.description

    # 7. HTTP tool reproduces the request; /admin is in the mock site profile
    # so the deterministic mock returns 200 -> validated.
    validate_result = executor.execute(validate_step, _TARGET, [])
    assert validate_result.status is ToolStatus.SUCCESS
    assert validate_result.data["status_code"] == 200

    # 8. Evaluator marks the finding validated -- confirmed only after
    # validation, not directly from Nuclei's raw output.
    evaluation3 = evaluator.evaluate("Recon", validate_step, validate_result)
    validated = next(f for f in evaluation3.findings if f.validation_status == "validated")
    assert "exposed-admin-panel" in validated.title
    assert validated.severity.value == "medium"


def test_validation_marks_false_positive_when_endpoint_missing(monkeypatch):
    _mock_nuclei(monkeypatch)
    registry = default_registry(enable_nuclei=True)
    executor = ExecutorAgent(registry)
    evaluator = EvaluatorAgent()

    nuclei_step_result = executor.execute(
        _step("nuclei_scan_url", {"target": _TARGET}), _TARGET, []
    )
    evaluation = evaluator.evaluate("Recon", _step("nuclei_scan_url", {}), nuclei_step_result)
    assert evaluation.replan_reason == ReplanReason.NUCLEI_CANDIDATE_FOUND

    planner = HeuristicPlanner(registry)
    plan = planner.create_plan("Recon", _TARGET)
    plan = planner.replan(plan, evaluation.replan_reason, [], nuclei_step_result)
    validate_step = next(s for s in plan.steps if s.tool_name == "http_get")

    # Force a 404 by validating a path that isn't in the mock site profile.
    validate_step.arguments["path"] = "/definitely-not-a-real-path"
    validate_result = executor.execute(validate_step, _TARGET, [])
    assert validate_result.data["status_code"] == 404

    evaluation2 = evaluator.evaluate("Recon", validate_step, validate_result)
    false_positive = next(
        f for f in evaluation2.findings if f.validation_status == "false_positive"
    )
    assert "False positive" in false_positive.title


def _step(tool_name: str, arguments: dict):
    return PlanStep(id="s-test", description="test step", tool_name=tool_name, arguments=arguments)
