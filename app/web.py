"""HexAgent web UI: a small Flask front-end over the existing agent graph.

This module never touches :func:`app.graph.workflow.run_workflow` (the
CLI/test entry point). It reuses :func:`build_nodes`/:func:`build_workflow`
directly and drives the compiled graph's ``.stream(..., stream_mode="updates")``
itself so the UI can show one live event per graph node, and supplies an
``approval_callback`` that pauses on a real HTTP round trip (Approve/Deny in
the browser) instead of blocking on a terminal ``input()`` prompt.

Runs are kept in an in-memory registry (``RUNS``) — this is a local,
single-process educational tool with no auth, meant to run on 127.0.0.1 only.
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import markdown
import nh3
from flask import Flask, Response, jsonify, render_template, request, send_from_directory

from app.agents.specialists import ApprovalCallback
from app.config import Settings
from app.graph.state import AgentState
from app.graph.workflow import build_nodes, build_workflow
from app.models.tool_io import ToolCall
from app.tools.registry import default_registry
from app.utils.logging import configure_logging, get_logger

logger = get_logger(__name__)

app = Flask(__name__)

# run_id -> RunSession. In-memory only; fine for a local single-process tool.
RUNS: dict[str, RunSession] = {}

_HEARTBEAT_SECONDS = 1.0
_MAX_IDLE_SECONDS = 300.0
# Emitted into session.events while a run is active so a single tool call that
# blocks longer than _MAX_IDLE_SECONDS (e.g. a real Nuclei scan) still produces
# new SSE events and never trips the idle-timeout below.
_PROGRESS_HEARTBEAT_SECONDS = 20.0

# The report is built from `Report` model fields, but `objective`/`target` and
# tool arguments flow into it verbatim from user input — so its rendered HTML
# is sanitised (not just "escaped-then-rendered", which double-escapes fenced
# code blocks) rather than trusted. Only the structural tags ReporterAgent's
# markdown actually produces are allow-listed; anything else (script, style,
# event handler attributes, raw <img>, etc.) is stripped entirely.
_REPORT_HTML_TAGS = {
    "p",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "table",
    "thead",
    "tbody",
    "tr",
    "th",
    "td",
    "pre",
    "code",
    "strong",
    "em",
    "ul",
    "ol",
    "li",
    "hr",
    "br",
    "blockquote",
}
_REPORT_MD_EXTENSIONS = ["tables", "fenced_code"]


def _render_report_html(report_markdown: str) -> str:
    """Render report markdown to sanitised HTML, safe for user-influenced content."""
    raw_html = markdown.markdown(report_markdown, extensions=_REPORT_MD_EXTENSIONS)
    return nh3.clean(raw_html, tags=_REPORT_HTML_TAGS, attributes={"code": {"class"}})


@dataclass
class RunSession:
    """Live state for one triggered workflow run.

    ``lock`` guards every mutable field below (not just ``events``) so the
    approve/deny HTTP handler and the background worker thread never race on
    ``status``/``pending_approval``/``approval_decision``.
    """

    run_id: str
    lock: threading.Lock = field(default_factory=threading.Lock)
    events: list[dict[str, Any]] = field(default_factory=list)
    status: str = "running"  # running | awaiting_approval | completed | error
    pending_approval: dict[str, Any] | None = None
    approval_gate: threading.Event = field(default_factory=threading.Event)
    approval_decision: bool | None = None
    final_markdown: str | None = None
    error: str | None = None
    # SSE idle-timeout for this run. Widened at creation time when a tool with
    # a longer worst-case timeout (e.g. Nuclei) is enabled, so a genuinely slow
    # single tool call doesn't outlive the stream's idle window.
    idle_timeout_seconds: float = _MAX_IDLE_SECONDS

    def __post_init__(self) -> None:
        self.cv = threading.Condition(self.lock)


def _emit(session: RunSession, event: dict[str, Any]) -> None:
    with session.lock:
        session.events.append(event)
        session.cv.notify_all()


def _make_approval_callback(session: RunSession) -> ApprovalCallback:
    """Build an approval callback that pauses the worker thread on a real gate.

    A fresh :class:`threading.Event` is created for *each* approval request
    (never reused via ``.clear()``) so a stale ``.set()`` from a duplicate/late
    ``POST /approve`` can never resolve a later, unrelated approval.
    """

    def _callback(call: ToolCall) -> bool:
        gate = threading.Event()
        with session.lock:
            session.pending_approval = {"tool_name": call.tool_name, "arguments": call.arguments}
            session.status = "awaiting_approval"
            session.approval_gate = gate
            session.events.append(
                {
                    "type": "approval_requested",
                    "tool_name": call.tool_name,
                    "arguments": call.arguments,
                }
            )
            session.cv.notify_all()

        gate.wait()  # blocks the worker thread only; Flask keeps serving other requests

        with session.lock:
            decision = bool(session.approval_decision)
            session.pending_approval = None
            session.status = "running"
            session.events.append({"type": "approval_resolved", "approved": decision})
            session.cv.notify_all()
        return decision

    return _callback


def _heartbeat_ticker(session: RunSession, stop: threading.Event) -> None:
    """Emit a keep-alive event every _PROGRESS_HEARTBEAT_SECONDS while the run
    is active, so a long blocking tool call (real Nuclei/Nmap scan) still
    produces SSE traffic instead of sitting silent until it returns.
    """
    while not stop.wait(_PROGRESS_HEARTBEAT_SECONDS):
        with session.lock:
            status = session.status
        if status not in ("running", "awaiting_approval"):
            break
        _emit(session, {"type": "heartbeat"})


def _translate_event(node_name: str, update: dict[str, Any]) -> dict[str, Any]:
    """Turn one LangGraph ``{node: partial_state}`` update into a UI event."""
    event: dict[str, Any] = {"type": node_name}

    if node_name == "plan" or node_name == "replan":
        plan = update.get("plan")
        if plan is not None:
            event["step_count"] = len(plan.steps)
            event["rationale"] = plan.rationale
            event["steps"] = [
                {"id": s.id, "description": s.description, "tool_name": s.tool_name}
                for s in plan.steps
            ]
    elif node_name == "execute":
        tool_results = update.get("tool_results")
        if tool_results:
            result = tool_results[-1]
            event.update(
                {
                    "tool_name": result.tool_name,
                    "status": result.status.value,
                    "summary": result.summary,
                    "duration_ms": result.duration_ms,
                }
            )
            # ToolResult.fail() puts the actual reason in .error and leaves
            # .summary as a generic "<tool> failed" -- surface it here so a
            # failure (missing binary, blocked tag, out-of-scope target, ...)
            # is visible in the live log instead of only in server-side logs.
            if result.error:
                event["error"] = result.error
            # Attach browser-specific fields when a browser tool ran.
            if result.tool_name.startswith("browser_"):
                data = result.data or {}
                event["browser_tool"] = True
                event["current_url"] = data.get("current_url")
                event["page_title"] = data.get("title")
                screenshot_path = data.get("screenshot_path")
                if screenshot_path:
                    event["screenshot_filename"] = Path(screenshot_path).name
                event["forms_count"] = len(data.get("forms") or [])
                event["links_count"] = len(data.get("links") or [])
                event["network_count"] = len(data.get("network_requests") or [])
                event["api_endpoints"] = (data.get("potential_api_endpoints") or [])[:10]
                event["auth_indicators"] = data.get("auth_indicators") or []
                event["browser_errors"] = data.get("errors") or ([result.error] if result.error else [])
                event["browser_success"] = bool(
                    data.get("success", result.status.value == "success")
                )
        else:
            history = update.get("reasoning_history") or []
            event["message"] = history[-1] if history else "no runnable step"
    elif node_name == "evaluate":
        if update:
            findings = update.get("findings") or []
            event["findings"] = [f.model_dump(mode="json") for f in findings]
            event["needs_replan"] = bool(update.get("needs_replan"))
            event["replan_reason"] = update.get("replan_reason", "")
        else:
            event["message"] = "nothing to evaluate (synthesis step)"
    elif node_name == "human_checkpoint":
        event["message"] = "Awaiting human approval checkpoint."
    elif node_name == "report":
        report_markdown = update.get("report_markdown") or ""
        event["stopped_reason"] = update.get("stopped_reason")
        event["next_actions"] = update.get("next_actions", [])
        event["report_markdown"] = report_markdown
        event["report_html"] = _render_report_html(report_markdown)

    return event


def _run_graph(session: RunSession, graph: Any, initial_state: AgentState, config: dict) -> None:
    stop_heartbeat = threading.Event()
    ticker = threading.Thread(
        target=_heartbeat_ticker, args=(session, stop_heartbeat), daemon=True
    )
    ticker.start()
    try:
        _emit(
            session,
            {"type": "start", "objective": initial_state.objective, "target": initial_state.target},
        )
        for update in graph.stream(initial_state, config=config, stream_mode="updates"):
            for node_name, partial in update.items():
                _emit(session, _translate_event(node_name, partial))
                if node_name == "report":
                    with session.lock:
                        session.final_markdown = partial.get("report_markdown")
        with session.lock:
            session.status = "completed"
        _emit(session, {"type": "done"})
    except Exception as exc:  # noqa: BLE001 - surface any failure to the UI instead of hanging it
        logger.exception("Workflow run %s failed", session.run_id)
        with session.lock:
            session.status = "error"
            session.error = str(exc)
        _emit(session, {"type": "error", "message": str(exc)})
    finally:
        stop_heartbeat.set()


@app.get("/")
def index() -> str:
    return render_template("index.html")


@app.get("/health")
def health():
    return jsonify({"status": "ok", "service": "hexagent"})


@app.post("/run")
def start_run():
    form = request.form
    objective = (form.get("objective") or "").strip()
    target = (form.get("target") or "").strip()
    if not objective or not target:
        return jsonify({"error": "objective and target are required"}), 400

    try:
        max_iterations = int(form.get("max_iterations") or 12)
    except ValueError:
        max_iterations = 12

    enable_playwright = bool(form.get("enable_playwright"))
    browser_username = (form.get("browser_username") or "").strip()
    browser_password = form.get("browser_password") or ""

    settings = Settings(
        mock_mode=bool(form.get("mock")),
        enable_nmap=bool(form.get("enable_nmap")),
        enable_playwright=enable_playwright,
        enable_nuclei=bool(form.get("enable_nuclei")),
        max_iterations=max_iterations,
        require_human_approval=bool(form.get("human_approval")),
        require_sensitive_approval=bool(form.get("require_sensitive_approval")),
    )

    session = RunSession(run_id=uuid.uuid4().hex)
    if settings.enable_nuclei:
        # A safe-default Nuclei scan can legitimately run for the full
        # configured timeout (default 600s) with no intermediate tool_result
        # events; give the stream enough idle headroom to outlast it even if
        # the heartbeat ticker below were ever disabled.
        session.idle_timeout_seconds = max(
            _MAX_IDLE_SECONDS, settings.nuclei_timeout_seconds + 60.0
        )
    RUNS[session.run_id] = session

    registry = default_registry(
        enable_nmap=settings.enable_nmap,
        enable_playwright=settings.enable_playwright,
        enable_nuclei=settings.enable_nuclei,
        settings=settings,
    )
    # Inject lab credentials into the shared BrowserManager so they never
    # appear in plan step arguments or LLM context.
    if enable_playwright and browser_username:
        login_tool = registry.get("browser_login")
        if login_tool is not None and hasattr(login_tool, "_mgr"):
            login_tool._mgr.set_credentials(browser_username, browser_password)  # type: ignore[union-attr]

    nodes = build_nodes(registry, settings, _make_approval_callback(session))
    graph = build_workflow(nodes)

    initial_state = AgentState(
        objective=objective,
        target=target,
        max_iterations=settings.max_iterations,
        require_human_approval=settings.require_human_approval,
    )
    config = {"recursion_limit": settings.max_iterations * 4 + 20}

    logger.info("Starting web run %s (target=%r)", session.run_id, target)
    thread = threading.Thread(
        target=_run_graph, args=(session, graph, initial_state, config), daemon=True
    )
    thread.start()

    return jsonify({"run_id": session.run_id}), 202


@app.get("/run/<run_id>")
def run_page(run_id: str):
    if run_id not in RUNS:
        return jsonify({"error": "unknown run_id"}), 404
    return render_template("run.html", run_id=run_id)


@app.get("/events/<run_id>")
def events(run_id: str):
    session = RUNS.get(run_id)
    if session is None:
        return jsonify({"error": "unknown run_id"}), 404

    def _stream():
        idx = 0
        idle_timeout = session.idle_timeout_seconds
        deadline = time.monotonic() + idle_timeout
        while True:
            with session.lock:
                while idx == len(session.events) and session.status in (
                    "running",
                    "awaiting_approval",
                ):
                    if time.monotonic() > deadline:
                        break
                    session.cv.wait(timeout=_HEARTBEAT_SECONDS)
                new_events = session.events[idx:]
                idx = len(session.events)
                terminal = session.status in ("completed", "error") and idx == len(session.events)

            for event in new_events:
                deadline = time.monotonic() + idle_timeout
                yield f"data: {json.dumps(event, default=str)}\n\n"

            if terminal:
                break
            if time.monotonic() > deadline:
                yield f"data: {json.dumps({'type': 'timeout'})}\n\n"
                break

    response = Response(_stream(), mimetype="text/event-stream")
    response.headers["Cache-Control"] = "no-cache"
    response.headers["X-Accel-Buffering"] = "no"
    return response


@app.post("/approve/<run_id>")
def approve(run_id: str):
    session = RUNS.get(run_id)
    if session is None:
        return jsonify({"error": "unknown run_id"}), 404

    payload = request.get_json(silent=True) or {}
    approved = bool(payload.get("approved"))

    with session.lock:
        if session.status != "awaiting_approval":
            return jsonify({"error": "no pending approval"}), 409
        session.approval_decision = approved
        gate = session.approval_gate

    gate.set()
    return jsonify({"ok": True, "approved": approved})


@app.get("/screenshots/<filename>")
def serve_screenshot(filename: str):
    """Serve a browser evidence screenshot (PNG only, no path traversal)."""
    if not filename.endswith(".png") or "/" in filename or ".." in filename:
        return jsonify({"error": "invalid filename"}), 400
    screenshot_dir = (Path.cwd() / "reports" / "screenshots").resolve()
    return send_from_directory(str(screenshot_dir), filename)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for the web UI (``hexagent-web``)."""
    parser = argparse.ArgumentParser(
        prog="hexagent-web", description="HexAgent web UI (local, no auth, lab use only)."
    )
    parser.add_argument("--host", default="127.0.0.1", help="Bind address (default: 127.0.0.1).")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--debug", action="store_true", help="Enable Flask debug mode.")
    args = parser.parse_args(argv)

    configure_logging("INFO")
    logger.warning(
        "HexAgent web UI has no authentication — bind to 127.0.0.1 only; never expose it "
        "to a network."
    )
    # threaded=True is required: without it the dev server serialises requests,
    # so a live SSE stream would starve the /approve endpoint for that run.
    app.run(host=args.host, port=args.port, debug=args.debug, threaded=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
