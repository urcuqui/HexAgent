"""Tests for the real Nuclei wrapper.

Mirrors tests/test_nmap_tool.py: every test here monkeypatches
subprocess.run/shutil.which so the suite stays offline and deterministic. No
real nuclei binary or network access is required.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess

import pytest

from app.agents.specialists import ReconAgent
from app.models.tool_io import ToolCall, ToolStatus
from app.tools.nuclei_tool import (
    ALLOWED_DEFAULT_TAGS,
    NucleiCheckInstallationTool,
    NucleiScanUrlsTool,
    NucleiScanUrlTool,
    check_installation,
    resolve_templates,
)
from app.tools.registry import default_registry

_SAMPLE_RECORD = {
    "template-id": "exposed-panel",
    "template": "http/exposures/panels/exposed-panel.yaml",
    "info": {
        "name": "Exposed Admin Panel",
        "severity": "info",
        "description": "An admin panel was detected.",
        "tags": ["panel", "exposure"],
        "reference": ["https://example.com/ref"],
    },
    "matched-at": "https://127.0.0.1/admin",
    "matcher-name": "panel-detect",
    "extracted-results": [],
    "curl-command": "curl -X GET https://127.0.0.1/admin -H 'Authorization: Bearer secret123'",
}

_SAMPLE_LINE = json.dumps(_SAMPLE_RECORD)


def _fake_run_factory(jsonl_lines, returncode=0, stderr=""):
    """Return (fake_run, captured) mimicking nuclei writing JSONL to '-o'."""
    captured: dict = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        if "-o" in command:
            out_path = command[command.index("-o") + 1]
            with open(out_path, "w") as fh:
                if jsonl_lines:
                    fh.write("\n".join(jsonl_lines) + "\n")
        return subprocess.CompletedProcess(command, returncode=returncode, stdout="", stderr=stderr)

    return fake_run, captured


# ---------------------------------------------------------------------------
# Registration / default-off behaviour
# ---------------------------------------------------------------------------


def test_nuclei_tools_not_registered_by_default():
    registry = default_registry()
    assert "nuclei_scan_url" not in registry.names()
    assert "nuclei_scan_urls" not in registry.names()
    assert "nuclei_check_installation" not in registry.names()


def test_nuclei_tools_registered_when_enabled():
    registry = default_registry(enable_nuclei=True)
    expected = {"nuclei_scan_url", "nuclei_scan_urls", "nuclei_check_installation"}
    assert expected <= set(registry.names())


def test_recon_agent_owns_nuclei_tools():
    recon = ReconAgent(default_registry(enable_nuclei=True))
    assert recon.owns("nuclei_scan_url")
    assert recon.owns("nuclei_scan_urls")
    assert recon.owns("nuclei_check_installation")


# ---------------------------------------------------------------------------
# check_installation / NucleiCheckInstallationTool
# ---------------------------------------------------------------------------


def test_check_installation_missing_binary(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    result = check_installation()
    assert result.status is ToolStatus.ERROR
    assert "not found" in result.error.lower()


def test_check_installation_success(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/nuclei")

    def fake_run(command, **kwargs):
        return subprocess.CompletedProcess(
            command, returncode=0, stdout="Nuclei 3.2.0\n", stderr=""
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = check_installation()
    assert result.status is ToolStatus.SUCCESS
    assert "3.2.0" in result.data["version_output"]


def test_check_installation_tool_delegates(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    result = NucleiCheckInstallationTool().run()
    assert result.status is ToolStatus.ERROR


# ---------------------------------------------------------------------------
# NucleiScanUrlTool: scope, profile validation, command construction
# ---------------------------------------------------------------------------


def test_scan_url_rejects_blocked_tag(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("must not exec nuclei"))
    tool = NucleiScanUrlTool()
    result = tool.run(target="127.0.0.1", tags=["bruteforce"])
    assert result.status is ToolStatus.ERROR
    assert "blocked tag" in result.error.lower()


def test_scan_url_rejects_unknown_severity(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("must not exec nuclei"))
    tool = NucleiScanUrlTool()
    result = tool.run(target="127.0.0.1", severity=["apocalyptic"])
    assert result.status is ToolStatus.ERROR
    assert "severity" in result.error.lower()


def test_scan_url_rejects_flag_injection_via_tag(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("must not exec nuclei"))
    tool = NucleiScanUrlTool()
    result = tool.run(target="127.0.0.1", tags=["-t"])
    assert result.status is ToolStatus.ERROR
    assert "invalid" in result.error.lower()


def test_scan_url_critical_severity_disabled_by_default(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("must not exec nuclei"))
    tool = NucleiScanUrlTool()
    result = tool.run(target="127.0.0.1", severity=["critical"])
    assert result.status is ToolStatus.ERROR
    assert "critical" in result.error.lower()


def test_scan_url_missing_binary_returns_error(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    tool = NucleiScanUrlTool()
    result = tool.run(target="127.0.0.1")
    assert result.status is ToolStatus.ERROR
    assert "not found" in result.error.lower()


def test_scan_url_successful_scan_parses_jsonl(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/nuclei")
    fake_run, captured = _fake_run_factory([_SAMPLE_LINE])
    monkeypatch.setattr(subprocess, "run", fake_run)

    tool = NucleiScanUrlTool()
    result = tool.run(target="127.0.0.1", path="/admin")

    assert result.status is ToolStatus.SUCCESS
    assert result.data["result_count"] == 1
    finding = result.data["findings"][0]
    assert finding["template_id"] == "exposed-panel"
    assert finding["severity"] == "info"
    assert finding["confidence"] == "candidate"
    assert finding["validation_required"] is True
    # Secrets in curl-command must be redacted before ever reaching state/LLM.
    assert "secret123" not in finding["curl_command"]
    assert "[redacted]" in finding["curl_command"]

    command = captured["command"]
    assert isinstance(command, list)
    assert "kwargs" in captured and "shell" not in captured["kwargs"]
    assert "-jsonl" in command
    assert "-u" in command and "https://127.0.0.1/admin" in command
    assert "-tags" in command
    assert sorted(command[command.index("-tags") + 1].split(",")) == sorted(ALLOWED_DEFAULT_TAGS)


def test_scan_url_malformed_jsonl_line_is_skipped_not_fatal(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/nuclei")
    fake_run, _ = _fake_run_factory([_SAMPLE_LINE, "{not valid json", ""])
    monkeypatch.setattr(subprocess, "run", fake_run)

    result = NucleiScanUrlTool().run(target="127.0.0.1")
    assert result.status is ToolStatus.SUCCESS
    assert result.data["result_count"] == 1
    assert any("malformed" in e.lower() for e in result.data["errors"])


def test_scan_url_caps_max_results(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/nuclei")
    two_lines = [_SAMPLE_LINE, _SAMPLE_LINE]
    fake_run, _ = _fake_run_factory(two_lines)
    monkeypatch.setattr(subprocess, "run", fake_run)

    result = NucleiScanUrlTool(max_results=1).run(target="127.0.0.1")
    assert result.data["result_count"] == 1
    assert any("capped" in e.lower() for e in result.data["errors"])


def test_scan_url_nonzero_exit_returns_error(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/nuclei")
    fake_run, _ = _fake_run_factory([], returncode=1, stderr="boom")
    monkeypatch.setattr(subprocess, "run", fake_run)

    result = NucleiScanUrlTool().run(target="127.0.0.1")
    assert result.status is ToolStatus.ERROR
    assert "boom" in result.error


def test_scan_url_timeout_returns_error(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/nuclei")

    def fake_run(command, **kwargs):
        raise subprocess.TimeoutExpired(cmd=command, timeout=1)

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = NucleiScanUrlTool(timeout=1).run(target="127.0.0.1")
    assert result.status is ToolStatus.ERROR
    assert "timed out" in result.error.lower()


def test_scan_url_cleans_up_temp_output_file(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/nuclei")
    fake_run, captured = _fake_run_factory([_SAMPLE_LINE])
    monkeypatch.setattr(subprocess, "run", fake_run)

    NucleiScanUrlTool().run(target="127.0.0.1")
    out_path = captured["command"][captured["command"].index("-o") + 1]
    assert not os.path.exists(out_path)


# ---------------------------------------------------------------------------
# NucleiScanUrlsTool: batch dedupe / scope / cap / temp target file
# ---------------------------------------------------------------------------


def test_scan_urls_empty_list_returns_error(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("must not exec nuclei"))
    result = NucleiScanUrlsTool().run(urls=[])
    assert result.status is ToolStatus.ERROR


def test_scan_urls_dedupes_and_filters_out_of_scope(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/nuclei")
    fake_run, captured = _fake_run_factory([_SAMPLE_LINE])
    monkeypatch.setattr(subprocess, "run", fake_run)

    urls = [
        "https://127.0.0.1/a",
        "https://127.0.0.1/a",  # exact duplicate
        "https://127.0.0.1/b",
        "https://evil.example.com/c",  # out of scope
    ]
    result = NucleiScanUrlsTool().run(urls=urls, target="127.0.0.1")

    assert result.status is ToolStatus.SUCCESS
    assert sorted(result.data["targets_scanned"]) == ["https://127.0.0.1/a", "https://127.0.0.1/b"]
    assert len(result.data["targets_skipped"]) == 1
    assert result.data["targets_skipped"][0]["url"] == "https://evil.example.com/c"

    list_path = captured["command"][captured["command"].index("-list") + 1]
    assert not os.path.exists(list_path)  # temp target file cleaned up


def test_scan_urls_enforces_max_targets(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/nuclei")
    fake_run, _ = _fake_run_factory([])
    monkeypatch.setattr(subprocess, "run", fake_run)

    urls = [f"https://127.0.0.1/{i}" for i in range(5)]
    result = NucleiScanUrlsTool(max_targets=2).run(urls=urls, target="127.0.0.1")

    assert result.status is ToolStatus.SUCCESS
    assert len(result.data["targets_scanned"]) == 2
    assert len(result.data["targets_skipped"]) == 3
    assert all("exceeds" in s["reason"] for s in result.data["targets_skipped"])


def test_scan_urls_all_out_of_scope_returns_ok_with_no_scan(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("must not exec nuclei"))
    result = NucleiScanUrlsTool().run(urls=["https://evil.example.com/x"], target="127.0.0.1")
    assert result.status is ToolStatus.SUCCESS
    assert result.data["result_count"] == 0
    assert result.data["targets_skipped"]


# ---------------------------------------------------------------------------
# is_call_sensitive: safe default runs unattended, escalation needs approval
# ---------------------------------------------------------------------------


def test_default_profile_call_is_not_sensitive():
    tool = NucleiScanUrlTool()
    assert tool.is_call_sensitive(target="127.0.0.1") is False
    call = {"target": "127.0.0.1", "tags": ["exposure"], "severity": ["info", "low"]}
    assert tool.is_call_sensitive(**call) is False


def test_high_severity_call_is_sensitive():
    tool = NucleiScanUrlTool()
    assert tool.is_call_sensitive(target="127.0.0.1", severity=["high"]) is True


def test_custom_templates_call_is_always_sensitive():
    tool = NucleiScanUrlTool()
    assert tool.is_call_sensitive(target="127.0.0.1", templates=["http/foo.yaml"]) is True


def test_raised_rate_limit_call_is_sensitive():
    tool = NucleiScanUrlTool(rate_limit=5)
    assert tool.is_call_sensitive(target="127.0.0.1", rate_limit=50) is True


def test_non_default_tag_call_is_sensitive():
    tool = NucleiScanUrlTool()
    assert tool.is_call_sensitive(target="127.0.0.1", tags=["cves"]) is True


def test_oversized_batch_call_is_sensitive():
    tool = NucleiScanUrlsTool(max_targets=2)
    urls = [f"https://127.0.0.1/{i}" for i in range(3)]
    assert tool.is_call_sensitive(urls=urls) is True
    assert tool.is_call_sensitive(urls=urls[:2]) is False


def test_specialist_gates_escalated_call_but_not_safe_default(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/nuclei")
    fake_run, _ = _fake_run_factory([])
    monkeypatch.setattr(subprocess, "run", fake_run)

    registry = default_registry(enable_nuclei=True)
    gated = ReconAgent(registry, require_sensitive_approval=True)  # no callback -> fail-closed

    safe = gated.run(ToolCall(tool_name="nuclei_scan_url", arguments={"target": "127.0.0.1"}))
    assert safe.status is ToolStatus.SUCCESS

    escalated_args = {"target": "127.0.0.1", "severity": ["critical"]}
    escalated = gated.run(ToolCall(tool_name="nuclei_scan_url", arguments=escalated_args))
    assert escalated.status is ToolStatus.SKIPPED


# ---------------------------------------------------------------------------
# Explicit template mode: containment + metadata guard
# ---------------------------------------------------------------------------


def test_explicit_templates_require_templates_dir(tmp_path):
    resolved, error = resolve_templates(["git-config.yaml"], None)
    assert resolved is None
    assert "NUCLEI_TEMPLATES_DIR" in error


def test_explicit_templates_path_traversal_rejected(tmp_path):
    templates_dir = tmp_path / "templates"
    templates_dir.mkdir()
    resolved, error = resolve_templates(["../outside.yaml"], str(templates_dir))
    assert resolved is None
    assert "escapes" in error.lower()


def test_explicit_templates_missing_file_rejected(tmp_path):
    templates_dir = tmp_path / "templates"
    templates_dir.mkdir()
    resolved, error = resolve_templates(["missing.yaml"], str(templates_dir))
    assert resolved is None
    assert "not found" in error.lower()


def test_explicit_templates_blocked_tag_metadata_rejected(tmp_path):
    templates_dir = tmp_path / "templates"
    templates_dir.mkdir()
    template_file = templates_dir / "bad.yaml"
    template_file.write_text("info:\n  name: Bad\n  severity: high\n  tags: exposure,bruteforce\n")
    resolved, error = resolve_templates(["bad.yaml"], str(templates_dir))
    assert resolved is None
    assert "blocked tag" in error.lower()


def test_explicit_templates_valid_template_accepted(tmp_path):
    templates_dir = tmp_path / "templates"
    templates_dir.mkdir()
    template_file = templates_dir / "good.yaml"
    template_file.write_text("info:\n  name: Good\n  severity: low\n  tags: exposure,misconfig\n")
    resolved, error = resolve_templates(["good.yaml"], str(templates_dir))
    assert error is None
    assert resolved == [str((templates_dir / "good.yaml").resolve())]


def test_scan_url_uses_resolved_template_path(monkeypatch, tmp_path):
    templates_dir = tmp_path / "templates"
    templates_dir.mkdir()
    template_file = templates_dir / "good.yaml"
    template_file.write_text("info:\n  name: Good\n  severity: low\n  tags: exposure\n")

    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/nuclei")
    fake_run, captured = _fake_run_factory([])
    monkeypatch.setattr(subprocess, "run", fake_run)

    tool = NucleiScanUrlTool(templates_dir=str(templates_dir))
    result = tool.run(target="127.0.0.1", templates=["good.yaml"])

    assert result.status is ToolStatus.SUCCESS
    command = captured["command"]
    assert "-t" in command
    assert str(template_file.resolve()) in command


@pytest.mark.skipif(
    not shutil.which("nuclei") or not os.environ.get("HEXAGENT_TEST_REAL_NUCLEI"),
    reason="opt-in: set HEXAGENT_TEST_REAL_NUCLEI=1 with nuclei installed to run a real scan",
)
def test_real_scan_against_localhost():
    result = NucleiScanUrlTool(timeout=30).run(target="127.0.0.1", tags=["tech"])
    assert result.status is ToolStatus.SUCCESS
    assert "result_count" in result.data
