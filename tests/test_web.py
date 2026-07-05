"""Tests for the Flask web UI (app.web).

These drive the underlying ``RunSession`` state machine directly (via the
in-memory ``RUNS`` registry) rather than parsing raw SSE bytes, per the
design's testing guidance — polling ``session.status`` is far less fragile
than consuming a live ``text/event-stream`` response in a test.
"""

from __future__ import annotations

import time

import pytest

from app import web as web_module
from app.models.tool_io import ToolResult


@pytest.fixture(autouse=True)
def _clear_runs():
    """Prevent state leaking between tests via the module-level RUNS dict."""
    web_module.RUNS.clear()
    yield
    web_module.RUNS.clear()


@pytest.fixture
def client():
    web_module.app.config.update(TESTING=True)
    return web_module.app.test_client()


def _wait_for_status(run_id: str, target_statuses: set[str], timeout: float = 5.0):
    """Poll RUNS[run_id] until its status is one of ``target_statuses``."""
    deadline = time.monotonic() + timeout
    session = None
    while time.monotonic() < deadline:
        session = web_module.RUNS.get(run_id)
        if session is not None and session.status in target_statuses:
            return session
        time.sleep(0.02)
    status = session.status if session else None
    events = session.events if session else None
    raise AssertionError(
        f"Timed out waiting for status in {target_statuses}; "
        f"last status={status!r}, events={events!r}"
    )


def _start_mock_run(client, **extra_fields):
    data = {"objective": "Recon", "target": "demo.thm.local", "mock": "on", **extra_fields}
    resp = client.post("/run", data=data)
    assert resp.status_code == 202, resp.get_json()
    return resp.get_json()["run_id"]


def test_index_page_loads(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert b"New Engagement" in resp.data


def test_health_endpoint(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.get_json() == {"service": "hexagent", "status": "ok"}


def test_run_requires_objective_and_target(client):
    resp = client.post("/run", data={"objective": "", "target": ""})
    assert resp.status_code == 400


def test_run_page_unknown_id_404(client):
    resp = client.get("/run/does-not-exist")
    assert resp.status_code == 404


def test_approve_unknown_run_404(client):
    resp = client.post("/approve/does-not-exist", json={"approved": True})
    assert resp.status_code == 404


def test_mock_run_completes(client):
    run_id = _start_mock_run(client)

    session = _wait_for_status(run_id, {"completed", "error"})

    assert session.status == "completed"
    assert session.final_markdown
    assert any(e["type"] == "done" for e in session.events)
    assert any(e["type"] == "execute" and e.get("tool_name") == "port_scan" for e in session.events)


def test_report_html_is_sanitised_against_malicious_objective(client):
    # objective/target flow verbatim into the report; the rendered HTML must
    # never let raw script/event-handler markup through.
    resp = client.post(
        "/run",
        data={
            "objective": "<script>alert(1)</script><img src=x onerror=alert(2)>",
            "target": "demo.thm.local",
            "mock": "on",
        },
    )
    run_id = resp.get_json()["run_id"]
    session = _wait_for_status(run_id, {"completed", "error"})
    assert session.status == "completed"

    report_events = [e for e in session.events if e["type"] == "report"]
    assert report_events, session.events
    report_html = report_events[-1]["report_html"]

    assert "<script" not in report_html.lower()
    assert "onerror" not in report_html.lower()
    # Raw markdown (never rendered as HTML client-side) keeps the literal text.
    assert "<script>" in report_events[-1]["report_markdown"]


def test_approve_without_pending_returns_409(client):
    run_id = _start_mock_run(client)
    _wait_for_status(run_id, {"completed", "error"})

    resp = client.post(f"/approve/{run_id}", json={"approved": True})
    assert resp.status_code == 409


def test_sensitive_approval_gate_deny_skips_the_action(client):
    run_id = _start_mock_run(client, require_sensitive_approval="on")

    session = _wait_for_status(run_id, {"awaiting_approval", "completed", "error"})
    assert session.status == "awaiting_approval"
    assert session.pending_approval["tool_name"] == "http_post"

    resp = client.post(f"/approve/{run_id}", json={"approved": False})
    assert resp.status_code == 200

    session = _wait_for_status(run_id, {"completed", "error"})
    assert session.status == "completed"
    post_events = [
        e
        for e in session.events
        if e.get("type") == "execute" and e.get("tool_name") == "http_post"
    ]
    assert post_events, session.events
    assert post_events[-1]["status"] == "skipped"
    assert any(e["type"] == "approval_resolved" and e["approved"] is False for e in session.events)


def test_sensitive_approval_gate_approve_runs_the_action(client):
    run_id = _start_mock_run(client, require_sensitive_approval="on")

    session = _wait_for_status(run_id, {"awaiting_approval", "completed", "error"})
    assert session.status == "awaiting_approval"

    resp = client.post(f"/approve/{run_id}", json={"approved": True})
    assert resp.status_code == 200

    session = _wait_for_status(run_id, {"completed", "error"})
    assert session.status == "completed"
    post_events = [
        e
        for e in session.events
        if e.get("type") == "execute" and e.get("tool_name") == "http_post"
    ]
    assert post_events, session.events
    assert post_events[-1]["status"] == "success"
    assert any(e["type"] == "approval_resolved" and e["approved"] is True for e in session.events)


def test_browser_close_event_defaults_to_success_when_tool_succeeded():
    result = ToolResult.ok("browser_close", "Browser session closed.", {"closed": True})
    event = web_module._translate_event(
        "execute",
        {"tool_results": [result]},
    )

    assert event["browser_tool"] is True
    assert event["browser_success"] is True


def test_browser_error_event_surfaces_tool_error_message():
    result = ToolResult.fail("browser_open", "Playwright not installed.")
    event = web_module._translate_event("execute", {"tool_results": [result]})

    assert event["browser_tool"] is True
    assert event["browser_success"] is False
    assert event["browser_errors"] == ["Playwright not installed."]
