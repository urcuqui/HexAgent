"""Tests for the domain-specialist agents and the sensitive-action approval gate."""

from __future__ import annotations

from unittest.mock import MagicMock

from app.agents.executor import ExecutorAgent
from app.agents.specialists import HttpAnalysisAgent, ReconAgent
from app.models.plan import PlanStep
from app.models.tool_io import ToolCall, ToolStatus
from app.tools.registry import default_registry


def test_recon_and_http_specialists_partition_the_registry(registry):
    recon = ReconAgent(registry)
    http = HttpAnalysisAgent(registry)
    all_names = set(registry.names())
    owned = recon.TOOL_NAMES | http.TOOL_NAMES
    # Every registered tool is owned by exactly one specialist (nmap_scan is
    # owned even when not registered, since enable_nmap is opt-in).
    assert all_names <= owned
    assert not (recon.TOOL_NAMES & http.TOOL_NAMES)


def test_non_sensitive_tool_runs_without_approval(registry):
    agent = ReconAgent(registry, require_sensitive_approval=True)
    call = ToolCall(tool_name="tech_fingerprint", arguments={"target": "example.com"})
    result = agent.run(call)
    assert result.status is ToolStatus.SUCCESS


def test_sensitive_tool_denied_by_default_when_no_callback(registry):
    agent = HttpAnalysisAgent(registry, require_sensitive_approval=True)
    call = ToolCall(tool_name="http_post", arguments={"target": "example.com", "path": "/login"})
    result = agent.run(call)
    assert result.status is ToolStatus.SKIPPED


def test_sensitive_tool_runs_when_approved(registry):
    agent = HttpAnalysisAgent(
        registry, approval_callback=lambda call: True, require_sensitive_approval=True
    )
    call = ToolCall(tool_name="http_post", arguments={"target": "example.com", "path": "/login"})
    result = agent.run(call)
    assert result.status is ToolStatus.SUCCESS


def test_sensitive_tool_skipped_when_denied(registry):
    agent = HttpAnalysisAgent(
        registry, approval_callback=lambda call: False, require_sensitive_approval=True
    )
    call = ToolCall(tool_name="http_post", arguments={"target": "example.com", "path": "/login"})
    result = agent.run(call)
    assert result.status is ToolStatus.SKIPPED


def test_sensitive_tool_runs_freely_when_gate_disabled(registry):
    agent = HttpAnalysisAgent(registry, require_sensitive_approval=False)
    call = ToolCall(tool_name="http_post", arguments={"target": "example.com", "path": "/login"})
    result = agent.run(call)
    assert result.status is ToolStatus.SUCCESS


def test_executor_dispatches_to_owning_specialist_and_gates_sensitive_tools(registry):
    executor = ExecutorAgent(
        registry, require_sensitive_approval=True, approval_callback=lambda c: False
    )
    step = PlanStep(
        id="s1", description="post", tool_name="http_post", arguments={"path": "/login"}
    )
    result = executor.execute(step, target="example.com", observations=[])
    assert result.status is ToolStatus.SKIPPED

    recon_step = PlanStep(id="s2", description="scan", tool_name="port_scan")
    recon_result = executor.execute(recon_step, target="example.com", observations=[])
    assert recon_result.status is ToolStatus.SUCCESS


def test_executor_preserves_declared_browser_tool_when_llm_is_enabled():
    registry = default_registry(enable_playwright=True)
    executor = ExecutorAgent(registry, llm=object())
    executor._select_with_llm = MagicMock(
        return_value=ToolCall(
            tool_name="browser_analyze_page",
            arguments={"target": "example.com"},
        )
    )
    step = PlanStep(
        id="s1",
        description="open browser",
        tool_name="browser_open",
        arguments={"target": "example.com"},
    )

    call = executor.select(step, target="example.com", observations=[])

    assert call.tool_name == "browser_open"
    executor._select_with_llm.assert_not_called()
