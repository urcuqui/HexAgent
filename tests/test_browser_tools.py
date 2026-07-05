"""Unit tests for browser_tools.py.

All tests run without a real browser: Playwright is mocked so the suite
remains fast and offline. The integration test (test_browser_integration.py)
covers real Playwright behaviour and is skipped when the library is absent.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.models.plan import ReplanReason
from app.models.tool_io import ToolStatus
from app.tools.browser_tools import (
    PLAYWRIGHT_AVAILABLE,
    BrowserAnalyzePageTool,
    BrowserCloseTool,
    BrowserManager,
    BrowserOpenTool,
    BrowserScreenshotTool,
    _redact_headers,
    _sanitize_filename,
    _validate_url,
    build_browser_tools,
)
from app.tools.registry import ToolRegistry, default_registry


# ---------------------------------------------------------------------------
# _validate_url
# ---------------------------------------------------------------------------


class TestValidateUrl:
    def test_allows_in_scope_http(self):
        assert _validate_url("http://demo.thm.local/login", "demo.thm.local") is None

    def test_allows_in_scope_https(self):
        assert _validate_url("https://demo.thm.local/api/v1", "demo.thm.local") is None

    def test_blocks_file_scheme(self):
        err = _validate_url("file:///etc/passwd", "demo.thm.local")
        assert err is not None
        assert "file" in err.lower()

    def test_blocks_javascript_scheme(self):
        err = _validate_url("javascript:alert(1)", "demo.thm.local")
        assert err is not None

    def test_blocks_data_scheme(self):
        err = _validate_url("data:text/html,<h1>hi</h1>", "demo.thm.local")
        assert err is not None

    def test_blocks_cloud_metadata(self):
        err = _validate_url("http://169.254.169.254/latest/meta-data/", "demo.thm.local")
        assert err is not None
        assert "metadata" in err.lower() or "169.254" in err

    def test_blocks_out_of_scope_host(self):
        err = _validate_url("http://evil.com/steal", "demo.thm.local")
        assert err is not None
        assert "scope" in err.lower() or "evil.com" in err

    def test_empty_hostname_in_url_passes_when_target_matches(self):
        # A URL with no hostname (relative) passes scope validation.
        assert _validate_url("/api/users", "demo.thm.local") is None

    def test_redirect_to_different_host_is_blocked(self):
        err = _validate_url("https://other.site/path", "demo.thm.local")
        assert err is not None


# ---------------------------------------------------------------------------
# _redact_headers
# ---------------------------------------------------------------------------


class TestRedactHeaders:
    def test_redacts_cookie(self):
        result = _redact_headers({"Cookie": "session=abc123", "Content-Type": "application/json"})
        assert result["Cookie"] == "[REDACTED]"
        assert result["Content-Type"] == "application/json"

    def test_redacts_authorization(self):
        result = _redact_headers({"Authorization": "Bearer token123"})
        assert result["Authorization"] == "[REDACTED]"

    def test_redacts_set_cookie(self):
        result = _redact_headers({"Set-Cookie": "id=42; Path=/"})
        assert result["Set-Cookie"] == "[REDACTED]"

    def test_redacts_api_key_header(self):
        result = _redact_headers({"X-API-Key": "secret"})
        assert result["X-API-Key"] == "[REDACTED]"

    def test_preserves_safe_headers(self):
        result = _redact_headers({"Content-Type": "text/html", "Server": "nginx"})
        assert result == {"Content-Type": "text/html", "Server": "nginx"}

    def test_case_insensitive(self):
        result = _redact_headers({"authorization": "Basic dXNlcjpwYXNz"})
        assert result["authorization"] == "[REDACTED]"


# ---------------------------------------------------------------------------
# _sanitize_filename
# ---------------------------------------------------------------------------


class TestSanitizeFilename:
    def test_replaces_special_chars(self):
        result = _sanitize_filename("http://target:8080/login?q=1")
        assert "/" not in result
        assert ":" not in result
        assert "?" not in result

    def test_max_length_80(self):
        long_name = "a" * 200
        result = _sanitize_filename(long_name)
        assert len(result) <= 80

    def test_allows_alphanumeric_and_hyphen(self):
        result = _sanitize_filename("my-screenshot_01")
        assert result == "my-screenshot_01"


# ---------------------------------------------------------------------------
# build_browser_tools factory
# ---------------------------------------------------------------------------


class TestBuildBrowserTools:
    def test_returns_six_tools(self):
        tools = build_browser_tools()
        assert len(tools) == 6  # noqa: PLR2004

    def test_tool_names_are_correct(self):
        names = {t.name for t in build_browser_tools()}
        assert names == {
            "browser_open",
            "browser_analyze_page",
            "browser_login",
            "browser_click",
            "browser_screenshot",
            "browser_close",
        }

    def test_all_tools_share_same_manager(self):
        tools = build_browser_tools()
        # All tools store a reference to the same BrowserManager instance.
        managers = {id(t._mgr) for t in tools}  # type: ignore[attr-defined]
        assert len(managers) == 1

    def test_screenshot_dir_is_configurable(self, tmp_path):
        tools = build_browser_tools(screenshot_dir=tmp_path / "shots")
        open_tool: BrowserOpenTool = next(t for t in tools if t.name == "browser_open")
        assert open_tool._mgr._screenshot_dir == tmp_path / "shots"


# ---------------------------------------------------------------------------
# Registry integration
# ---------------------------------------------------------------------------


class TestBrowserRegistry:
    def test_default_registry_does_not_include_browser_tools(self):
        reg = default_registry()
        for name in ("browser_open", "browser_analyze_page", "browser_login"):
            assert reg.get(name) is None

    def test_browser_tools_registered_when_enabled(self):
        reg = default_registry(enable_playwright=True)
        assert reg.get("browser_open") is not None
        assert reg.get("browser_login") is not None
        assert reg.get("browser_close") is not None

    def test_browser_tool_in_catalogue_when_enabled(self):
        reg = default_registry(enable_playwright=True)
        catalogue = reg.catalogue()
        assert "browser_open" in catalogue

    def test_existing_tools_still_present_with_playwright_enabled(self):
        reg = default_registry(enable_playwright=True)
        for name in ("http_get", "http_post", "port_scan", "url_crawler"):
            assert reg.get(name) is not None


# ---------------------------------------------------------------------------
# Tool behaviour without Playwright installed
# ---------------------------------------------------------------------------


class TestToolsWithoutPlaywright:
    """When playwright is not importable, every tool returns ToolResult.fail."""

    def _make_tools_without_playwright(self):
        with patch("app.tools.browser_tools.PLAYWRIGHT_AVAILABLE", False):
            return build_browser_tools()

    def test_browser_open_fails_gracefully_without_playwright(self):
        with patch("app.tools.browser_tools.PLAYWRIGHT_AVAILABLE", False):
            tools = build_browser_tools()
            open_tool: BrowserOpenTool = next(t for t in tools if t.name == "browser_open")
            result = open_tool.run(target="demo.thm.local")
            assert result.status == ToolStatus.ERROR
            assert "playwright" in (result.error or "").lower()

    def test_browser_close_no_op_when_not_active(self):
        tools = build_browser_tools()
        close_tool: BrowserCloseTool = next(t for t in tools if t.name == "browser_close")
        result = close_tool.run(target="demo.thm.local")
        assert result.status == ToolStatus.SUCCESS
        assert "no active" in result.summary.lower()


# ---------------------------------------------------------------------------
# BrowserManager lifecycle
# ---------------------------------------------------------------------------


class TestBrowserManager:
    def test_active_is_false_initially(self):
        mgr = BrowserManager()
        assert mgr.active is False

    def test_close_is_idempotent_when_not_started(self):
        mgr = BrowserManager()
        mgr.close()  # should not raise
        assert mgr.active is False

    def test_flush_network_events_returns_empty_initially(self):
        mgr = BrowserManager()
        assert mgr.flush_network_events() == []

    def test_save_screenshot_returns_none_without_page(self):
        mgr = BrowserManager()
        assert mgr.save_screenshot("test") is None


# ---------------------------------------------------------------------------
# Scope validation via ToolResult
# ---------------------------------------------------------------------------


class TestScopeEnforcement:
    def _open_tool(self):
        tools = build_browser_tools()
        return next(t for t in tools if t.name == "browser_open")

    def test_file_scheme_rejected(self):
        tool = self._open_tool()
        with patch("app.tools.browser_tools.PLAYWRIGHT_AVAILABLE", True):
            result = tool.run(target="demo.thm.local", url="file:///etc/passwd")
        assert result.status == ToolStatus.ERROR
        assert "scope" in (result.error or "").lower() or "blocked" in (result.error or "").lower()

    def test_out_of_scope_host_rejected(self):
        tool = self._open_tool()
        with patch("app.tools.browser_tools.PLAYWRIGHT_AVAILABLE", True):
            result = tool.run(target="demo.thm.local", url="http://evil.com/")
        assert result.status == ToolStatus.ERROR


# ---------------------------------------------------------------------------
# Planner integration: BROWSER_LOGIN_FORM_FOUND replan reason exists
# ---------------------------------------------------------------------------


def test_browser_login_form_found_is_a_valid_replan_reason():
    assert ReplanReason.BROWSER_LOGIN_FORM_FOUND == "browser_login_form_found"


# ---------------------------------------------------------------------------
# Workflow: graph completes without Playwright (regression check)
# ---------------------------------------------------------------------------


def test_workflow_completes_without_playwright(offline_settings):
    """Existing workflow must still complete when Playwright is disabled."""
    from app.graph.workflow import run_workflow

    state = run_workflow("Passive recon", "demo.thm.local", settings=offline_settings)
    assert state.plan is not None
    assert state.plan.is_complete()
    assert state.stopped_reason == "objective completed"
    assert state.report_markdown


# ---------------------------------------------------------------------------
# BrowserAgent specialist
# ---------------------------------------------------------------------------


def test_browser_agent_owns_all_browser_tool_names():
    from app.agents.specialists import BrowserAgent

    expected = {
        "browser_open",
        "browser_analyze_page",
        "browser_login",
        "browser_click",
        "browser_screenshot",
        "browser_close",
    }
    assert BrowserAgent.TOOL_NAMES == expected


def test_browser_login_is_sensitive():
    tools = build_browser_tools()
    login_tool = next(t for t in tools if t.name == "browser_login")
    assert login_tool.sensitive is True


def test_other_browser_tools_are_not_sensitive():
    tools = build_browser_tools()
    for tool in tools:
        if tool.name != "browser_login":
            assert tool.sensitive is False, f"{tool.name} should not be sensitive"


# ---------------------------------------------------------------------------
# Evaluator: browser heuristics
# ---------------------------------------------------------------------------


class TestEvaluatorBrowserHeuristics:
    """Heuristic evaluation rules for browser tool results."""

    def _make_evaluator(self):
        from app.agents.evaluator import EvaluatorAgent

        return EvaluatorAgent(llm=None)

    def _make_step(self, tool_name: str):
        from app.models.plan import PlanStep

        return PlanStep(id="s1", description="test", tool_name=tool_name)

    def test_browser_open_with_login_form_triggers_replan(self):
        from app.models.tool_io import ToolResult

        evaluator = self._make_evaluator()
        step = self._make_step("browser_open")
        result = ToolResult.ok(
            "browser_open",
            "browser_open http://target -> 200",
            {
                "success": True,
                "current_url": "http://target/login",
                "title": "Login",
                "forms": [{"action": "/login", "method": "POST", "fields": [{"name": "password", "type": "password"}]}],
                "auth_indicators": ["password field 'password'"],
                "potential_api_endpoints": [],
                "links": [],
                "network_requests": [],
            },
        )
        evaluation = evaluator._evaluate_heuristically(step, result)
        assert evaluation.needs_replan is True
        assert evaluation.replan_reason == ReplanReason.BROWSER_LOGIN_FORM_FOUND

    def test_browser_open_without_login_form_does_not_replan(self):
        from app.models.tool_io import ToolResult

        evaluator = self._make_evaluator()
        step = self._make_step("browser_open")
        result = ToolResult.ok(
            "browser_open",
            "browser_open http://target -> 200",
            {
                "success": True,
                "current_url": "http://target/",
                "title": "Home",
                "forms": [],
                "auth_indicators": [],
                "potential_api_endpoints": ["/api/users"],
                "links": [],
                "network_requests": [],
            },
        )
        evaluation = evaluator._evaluate_heuristically(step, result)
        assert evaluation.needs_replan is False

    def test_browser_open_with_api_endpoints_creates_finding(self):
        from app.models.tool_io import ToolResult

        evaluator = self._make_evaluator()
        step = self._make_step("browser_open")
        result = ToolResult.ok(
            "browser_open",
            "browser_open http://target -> 200",
            {
                "success": True,
                "current_url": "http://target/",
                "title": "Home",
                "forms": [],
                "auth_indicators": [],
                "potential_api_endpoints": ["/api/users", "/api/profile"],
                "links": [],
                "network_requests": [],
            },
        )
        evaluation = evaluator._evaluate_heuristically(step, result)
        assert any("api" in f.title.lower() for f in evaluation.findings)

    def test_browser_screenshot_adds_to_new_screenshots(self):
        from app.models.tool_io import ToolResult

        evaluator = self._make_evaluator()
        step = self._make_step("browser_screenshot")
        result = ToolResult.ok(
            "browser_screenshot",
            "Screenshot saved: /tmp/shot.png",
            {"screenshot_path": "/tmp/shot.png", "name": "test", "full_page": True},
        )
        evaluation = evaluator._evaluate_heuristically(step, result)
        assert "/tmp/shot.png" in evaluation.new_screenshots

    def test_browser_session_active_set_on_open(self):
        from app.models.tool_io import ToolResult

        evaluator = self._make_evaluator()
        step = self._make_step("browser_open")
        result = ToolResult.ok(
            "browser_open",
            "summary",
            {
                "success": True,
                "current_url": "http://t/",
                "title": "T",
                "forms": [],
                "auth_indicators": [],
                "potential_api_endpoints": [],
                "links": [],
                "network_requests": [],
            },
        )
        evaluation = evaluator._evaluate_heuristically(step, result)
        assert evaluation.browser_session_active is True

    def test_browser_session_active_cleared_on_close(self):
        from app.models.tool_io import ToolResult

        evaluator = self._make_evaluator()
        step = self._make_step("browser_close")
        result = ToolResult.ok("browser_close", "Browser session closed.", {"closed": True})
        evaluation = evaluator._evaluate_heuristically(step, result)
        assert evaluation.browser_session_active is False


# ---------------------------------------------------------------------------
# Planner: browser phase queued when tools registered
# ---------------------------------------------------------------------------


class TestPlannerBrowserPhase:
    def _make_planner_with_browser(self):
        from app.planners.planner import HeuristicPlanner

        reg = default_registry(enable_playwright=True)
        return HeuristicPlanner(reg)

    def _make_planner_without_browser(self):
        from app.planners.planner import HeuristicPlanner

        reg = default_registry(enable_playwright=False)
        return HeuristicPlanner(reg)

    def _open_web_ports_result(self):
        from app.models.tool_io import ToolResult

        return ToolResult.ok(
            "port_scan",
            "2 open ports",
            {"open_ports": [{"port": 80}, {"port": 443}]},
        )

    def _initial_plan(self, planner):
        return planner.create_plan("recon", "demo.thm.local")

    def test_browser_phase_queued_when_playwright_enabled(self):
        from app.models.plan import ReplanReason

        planner = self._make_planner_with_browser()
        plan = self._initial_plan(planner)
        plan = planner.replan(
            plan, ReplanReason.OPEN_WEB_PORTS_FOUND, [], self._open_web_ports_result()
        )
        tool_names = {s.tool_name for s in plan.steps}
        assert "browser_open" in tool_names
        assert "browser_analyze_page" in tool_names
        assert "browser_close" in tool_names

    def test_browser_phase_not_queued_when_playwright_disabled(self):
        from app.models.plan import ReplanReason

        planner = self._make_planner_without_browser()
        plan = self._initial_plan(planner)
        plan = planner.replan(
            plan, ReplanReason.OPEN_WEB_PORTS_FOUND, [], self._open_web_ports_result()
        )
        tool_names = {s.tool_name for s in plan.steps}
        assert "browser_open" not in tool_names

    def test_browser_login_queued_on_browser_login_form_found(self):
        from app.models.plan import ReplanReason
        from app.models.tool_io import ToolResult

        planner = self._make_planner_with_browser()
        plan = self._initial_plan(planner)
        result = ToolResult.ok(
            "browser_open",
            "summary",
            {"success": True, "current_url": "http://demo.thm.local/login"},
        )
        plan = planner.replan(plan, ReplanReason.BROWSER_LOGIN_FORM_FOUND, [], result)
        tool_names = {s.tool_name for s in plan.steps}
        assert "browser_login" in tool_names

    def test_browser_login_not_double_queued(self):
        from app.models.plan import ReplanReason
        from app.models.tool_io import ToolResult

        planner = self._make_planner_with_browser()
        plan = self._initial_plan(planner)
        result = ToolResult.ok(
            "browser_open",
            "summary",
            {"success": True, "current_url": "http://demo.thm.local/login"},
        )
        plan = planner.replan(plan, ReplanReason.BROWSER_LOGIN_FORM_FOUND, [], result)
        plan = planner.replan(plan, ReplanReason.BROWSER_LOGIN_FORM_FOUND, [], result)
        login_steps = [s for s in plan.steps if s.tool_name == "browser_login"]
        assert len(login_steps) == 1


# ---------------------------------------------------------------------------
# Discovered endpoints added to existing state (via observations)
# ---------------------------------------------------------------------------


def test_browser_discovered_endpoints_appear_in_observations():
    """Browser-found API endpoints must feed into the shared observations list."""
    from app.agents.evaluator import EvaluatorAgent
    from app.models.plan import PlanStep
    from app.models.tool_io import ToolResult

    evaluator = EvaluatorAgent(llm=None)
    step = PlanStep(id="s1", description="browser open", tool_name="browser_open")
    result = ToolResult.ok(
        "browser_open",
        "summary",
        {
            "success": True,
            "current_url": "http://target/",
            "title": "App",
            "forms": [],
            "auth_indicators": [],
            "potential_api_endpoints": ["/api/users"],
            "links": [],
            "network_requests": [],
        },
    )
    evaluation = evaluator._evaluate_heuristically(step, result)
    combined = " ".join(o.content for o in evaluation.observations)
    assert "/api/users" in combined


# ---------------------------------------------------------------------------
# Browser failure does not crash the graph
# ---------------------------------------------------------------------------


def test_browser_tool_failure_returns_error_not_exception():
    """A browser launch crash must return a graceful ok result (success=False), not raise."""
    with patch("app.tools.browser_tools.PLAYWRIGHT_AVAILABLE", True):
        tools = build_browser_tools()
        open_tool = next(t for t in tools if t.name == "browser_open")
        # Inject a manager whose get_page() raises to simulate a Chromium launch crash.
        open_tool._mgr.get_page = MagicMock(side_effect=RuntimeError("browser crash"))
        result = open_tool.run(target="demo.thm.local", url="http://demo.thm.local/")
    # browser_open now catches the launch error internally so the graph can continue.
    assert result.status == ToolStatus.SUCCESS
    assert result.data.get("success") is False
    assert any("browser crash" in e for e in (result.data.get("errors") or []))


def test_browser_analyze_page_failure_returns_success_false_not_error():
    """A page inspection crash should not surface as ToolStatus.ERROR."""
    with patch("app.tools.browser_tools.PLAYWRIGHT_AVAILABLE", True):
        tools = build_browser_tools()
        analyze_tool: BrowserAnalyzePageTool = next(
            t for t in tools if t.name == "browser_analyze_page"
        )
        analyze_tool._mgr._active = True
        page = MagicMock()
        page.url = "http://demo.thm.local/"
        page.inner_text.return_value = ""
        page.query_selector_all.return_value = []
        page.title.side_effect = RuntimeError("title crashed")
        analyze_tool._mgr.get_page = MagicMock(return_value=page)

        result = analyze_tool.run(target="demo.thm.local")

    assert result.status == ToolStatus.SUCCESS
    assert result.data.get("success") is False
    assert result.data.get("current_url") == "http://demo.thm.local/"
    assert any("title crashed" in e for e in (result.data.get("errors") or []))
