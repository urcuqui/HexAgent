"""Integration test: Playwright browser tools against a local mock application.

The test spins up a minimal Flask application in a background thread that exposes:

  GET  /           Landing page with a link to /login
  GET  /login      Login form (username + password)
  POST /login      Authenticate; redirect to /dashboard on valid credentials
  GET  /dashboard  Authenticated page that JS-fetches /api/profile; has logout link
  GET  /api/profile  JSON API endpoint (returns a user object)
  GET  /logout     Clear session and redirect to /

The test validates the end-to-end flow:

  1. browser_open discovers the login form.
  2. browser_login authenticates successfully.
  3. /api/profile appears in captured network requests.
  4. browser_analyze_page reads the dashboard.
  5. browser_screenshot saves evidence.
  6. browser_close cleans up.

Skipped automatically when Playwright is not installed or when the ``flask``
package is not available.
"""

from __future__ import annotations

import threading
import time
from typing import Generator

import pytest

# Skip the entire module if Playwright is unavailable.
pytest.importorskip("playwright", reason="playwright not installed; skipping integration tests")
pytest.importorskip("flask", reason="flask not installed; skipping integration tests")

from flask import Flask, jsonify, redirect, render_template_string, request, session, url_for

from app.tools.browser_tools import (
    BrowserAnalyzePageTool,
    BrowserCloseTool,
    BrowserLoginTool,
    BrowserOpenTool,
    BrowserScreenshotTool,
    build_browser_tools,
)
from app.models.tool_io import ToolStatus

# ---------------------------------------------------------------------------
# Mock Flask application
# ---------------------------------------------------------------------------

_VALID_USERNAME = "admin"
_VALID_PASSWORD = "password123"

_LANDING_HTML = """
<!doctype html>
<html>
<head><title>HexAgent Test App</title></head>
<body>
  <h1>Welcome to HexAgent Test App</h1>
  <p>Please <a href="/login">log in</a> to continue.</p>
</body>
</html>
"""

_LOGIN_HTML = """
<!doctype html>
<html>
<head><title>Login - HexAgent Test App</title></head>
<body>
  <h1>Login</h1>
  {% if error %}
  <p style="color:red">{{ error }}</p>
  {% endif %}
  <form method="post" action="/login">
    <label>Username: <input type="text" name="username" id="username"></label><br>
    <label>Password: <input type="password" name="password" id="password"></label><br>
    <button type="submit">Login</button>
  </form>
</body>
</html>
"""

_DASHBOARD_HTML = """
<!doctype html>
<html>
<head><title>Dashboard - HexAgent Test App</title></head>
<body>
  <h1>Dashboard</h1>
  <p>Welcome, {{ username }}!</p>
  <div id="profile"></div>
  <a href="/logout">Logout</a>
  <script>
    fetch('/api/profile')
      .then(r => r.json())
      .then(data => { document.getElementById('profile').textContent = JSON.stringify(data); });
  </script>
</body>
</html>
"""


def _create_mock_app() -> Flask:
    """Build and return the Flask mock application."""
    app = Flask(__name__)
    app.secret_key = "hexagent-test-secret"

    @app.route("/")
    def landing():
        return render_template_string(_LANDING_HTML)

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            username = request.form.get("username", "")
            password = request.form.get("password", "")
            if username == _VALID_USERNAME and password == _VALID_PASSWORD:
                session["user"] = username
                return redirect(url_for("dashboard"))
            return render_template_string(_LOGIN_HTML, error="Invalid credentials"), 401
        return render_template_string(_LOGIN_HTML, error=None)

    @app.route("/dashboard")
    def dashboard():
        username = session.get("user")
        if not username:
            return redirect(url_for("login"))
        return render_template_string(_DASHBOARD_HTML, username=username)

    @app.route("/api/profile")
    def api_profile():
        username = session.get("user")
        if not username:
            return jsonify({"error": "Unauthorized"}), 401
        return jsonify({"id": 1, "username": username, "role": "admin"})

    @app.route("/logout")
    def logout():
        session.clear()
        return redirect(url_for("landing"))

    return app


# ---------------------------------------------------------------------------
# Thread-based server fixture
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def mock_server() -> Generator[str, None, None]:
    """Start the mock Flask app in a daemon thread; yield its base URL."""
    import socket

    # Find a free port.
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]

    app = _create_mock_app()

    def run():
        import logging

        log = logging.getLogger("werkzeug")
        log.setLevel(logging.ERROR)
        app.run(host="127.0.0.1", port=port, use_reloader=False, threaded=True)

    t = threading.Thread(target=run, daemon=True)
    t.start()

    # Wait for the server to become reachable.
    base_url = f"http://127.0.0.1:{port}"
    deadline = time.time() + 5
    import urllib.request

    while time.time() < deadline:
        try:
            urllib.request.urlopen(base_url, timeout=1)
            break
        except Exception:
            time.sleep(0.1)

    yield base_url


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestBrowserIntegration:
    """Full browser-automation flow against the local mock application."""

    @pytest.fixture(autouse=True)
    def tools(self, tmp_path):
        """Build a fresh set of browser tools for each test method."""
        all_tools = build_browser_tools(
            headless=True,
            timeout_ms=15_000,
            screenshot_dir=tmp_path / "screenshots",
        )
        self.open_tool: BrowserOpenTool = next(t for t in all_tools if t.name == "browser_open")
        self.login_tool: BrowserLoginTool = next(t for t in all_tools if t.name == "browser_login")
        self.analyze_tool: BrowserAnalyzePageTool = next(
            t for t in all_tools if t.name == "browser_analyze_page"
        )
        self.screenshot_tool: BrowserScreenshotTool = next(
            t for t in all_tools if t.name == "browser_screenshot"
        )
        self.close_tool: BrowserCloseTool = next(t for t in all_tools if t.name == "browser_close")
        yield
        # Clean up after the test.
        self.close_tool.run(target="127.0.0.1")

    def test_open_landing_page(self, mock_server):
        """browser_open should return 200 and detect the login link."""
        target = "127.0.0.1"
        result = self.open_tool.run(target=target, url=mock_server + "/", screenshot=False)

        assert result.status == ToolStatus.SUCCESS
        data = result.data
        assert data["status_code"] == 200
        assert "HexAgent Test App" in (data.get("title") or "")
        links = data.get("links") or []
        assert any("/login" in lnk.get("href", "") for lnk in links)

    def test_open_login_page_detects_form(self, mock_server):
        """browser_open on the login page should find the login form with a password field."""
        target = "127.0.0.1"
        result = self.open_tool.run(target=target, url=mock_server + "/login", screenshot=False)

        assert result.status == ToolStatus.SUCCESS
        forms = result.data.get("forms") or []
        assert len(forms) >= 1
        fields = forms[0].get("fields") or []
        field_types = [f.get("type") for f in fields]
        assert "password" in field_types

    def test_login_succeeds_with_valid_credentials(self, mock_server):
        """browser_login should authenticate and redirect to /dashboard."""
        target = "127.0.0.1"
        # Navigate to login page first.
        self.open_tool.run(target=target, url=mock_server + "/login", screenshot=False)

        result = self.login_tool.run(
            target=target,
            url=mock_server + "/login",
            username=_VALID_USERNAME,
            password=_VALID_PASSWORD,
            screenshot=False,
        )

        assert result.status == ToolStatus.SUCCESS
        assert result.data.get("success") is True
        assert "dashboard" in (result.data.get("current_url") or "")

    def test_login_fails_with_wrong_credentials(self, mock_server):
        """browser_login result should indicate failure for bad credentials."""
        target = "127.0.0.1"
        self.open_tool.run(target=target, url=mock_server + "/login", screenshot=False)

        result = self.login_tool.run(
            target=target,
            url=mock_server + "/login",
            username="wrong",
            password="wrong",
            screenshot=False,
        )

        assert result.status == ToolStatus.SUCCESS  # tool ran without error
        assert result.data.get("success") is False

    def test_api_profile_captured_after_login(self, mock_server):
        """After login, the browser JS-fetches /api/profile; it should appear in network events."""
        target = "127.0.0.1"
        # Open landing, then login.
        self.open_tool.run(target=target, url=mock_server + "/login", screenshot=False)
        login_result = self.login_tool.run(
            target=target,
            url=mock_server + "/login",
            username=_VALID_USERNAME,
            password=_VALID_PASSWORD,
            screenshot=False,
        )
        assert login_result.data.get("success") is True

        # The dashboard JS fetches /api/profile; analyze_page flushes those events.
        analyze_result = self.analyze_tool.run(target=target)
        assert analyze_result.status == ToolStatus.SUCCESS

        # /api/profile may appear in the login or analyze network buffers.
        all_network_reqs = (
            (login_result.data.get("network_requests") or [])
            + (analyze_result.data.get("network_requests") or [])
        )
        api_urls = [r.get("url", "") for r in all_network_reqs]
        assert any("/api/profile" in u for u in api_urls), (
            f"Expected /api/profile in captured requests, got: {api_urls}"
        )

    def test_screenshot_is_saved(self, mock_server, tmp_path):
        """browser_screenshot should save a .png file."""
        target = "127.0.0.1"
        self.open_tool.run(target=target, url=mock_server + "/", screenshot=False)

        result = self.screenshot_tool.run(target=target, name="landing_evidence")

        assert result.status == ToolStatus.SUCCESS
        path = result.data.get("screenshot_path") or ""
        assert path.endswith(".png")
        assert (tmp_path / "screenshots").exists()
        png_files = list((tmp_path / "screenshots").glob("*.png"))
        assert len(png_files) >= 1

    def test_browser_close_cleans_up(self, mock_server):
        """browser_close should leave the manager inactive."""
        target = "127.0.0.1"
        self.open_tool.run(target=target, url=mock_server + "/", screenshot=False)
        assert self.open_tool._mgr.active is True

        close_result = self.close_tool.run(target=target)
        assert close_result.status == ToolStatus.SUCCESS
        assert self.open_tool._mgr.active is False

    def test_full_pentest_flow(self, mock_server, tmp_path):
        """
        End-to-end validation of the extended workflow:

          planner -> browser_open -> login form detected
          -> browser_login authenticated
          -> /api/profile in network capture
          -> report would include browser evidence
        """
        target = "127.0.0.1"

        # Step 1: open the landing page.
        open_result = self.open_tool.run(
            target=target, url=mock_server + "/", screenshot=False
        )
        assert open_result.status == ToolStatus.SUCCESS

        # Step 2: open the login page — evaluator would trigger BROWSER_LOGIN_FORM_FOUND here.
        login_page_result = self.open_tool.run(
            target=target, url=mock_server + "/login", screenshot=True
        )
        assert login_page_result.status == ToolStatus.SUCCESS
        forms = login_page_result.data.get("forms") or []
        has_password = any(
            f.get("type") == "password"
            for form in forms
            for f in form.get("fields", [])
        )
        assert has_password, "Login form must contain a password field"

        # Step 3: authenticate.
        login_result = self.login_tool.run(
            target=target,
            url=mock_server + "/login",
            username=_VALID_USERNAME,
            password=_VALID_PASSWORD,
            screenshot=True,
        )
        assert login_result.data.get("success") is True

        # Step 4: analyze the dashboard page.
        analyze_result = self.analyze_tool.run(target=target)
        assert analyze_result.status == ToolStatus.SUCCESS

        # Step 5: verify /api/profile was captured somewhere.
        all_reqs = (
            (login_result.data.get("network_requests") or [])
            + (analyze_result.data.get("network_requests") or [])
        )
        api_urls = [r.get("url", "") for r in all_reqs]
        assert any("/api/profile" in u for u in api_urls)

        # Step 6: take an evidence screenshot.
        shot_result = self.screenshot_tool.run(target=target, name="dashboard_evidence")
        assert shot_result.status == ToolStatus.SUCCESS
        assert (tmp_path / "screenshots").exists()

        # Step 7: close the browser.
        close_result = self.close_tool.run(target=target)
        assert close_result.status == ToolStatus.SUCCESS
        assert not self.open_tool._mgr.active

        # Existing reporting still works: screenshots paths are in results.
        screenshot_paths = [
            login_page_result.data.get("screenshot_path"),
            login_result.data.get("screenshot_path"),
            shot_result.data.get("screenshot_path"),
        ]
        for path in screenshot_paths:
            if path:
                from pathlib import Path as PPath

                assert PPath(path).exists(), f"Screenshot missing: {path}"
