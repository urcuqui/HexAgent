"""Tests for the real Nmap wrapper.

All tests mock ``subprocess.run``/``shutil.which`` so the default suite stays
offline and deterministic. A real scan against 127.0.0.1 is exercised only when
explicitly opted in via HEXAGENT_TEST_REAL_NMAP=1 and nmap is on PATH.
"""

from __future__ import annotations

import os
import shutil
import subprocess

import pytest

from app.models.tool_io import ToolStatus
from app.tools.nmap_tool import NmapScanTool

_SAMPLE_XML = """<?xml version="1.0"?>
<nmaprun>
<host>
<status state="up"/>
<address addr="127.0.0.1" addrtype="ipv4"/>
<ports>
<port protocol="tcp" portid="22"><state state="closed"/><service name="ssh"/></port>
<port protocol="tcp" portid="80"><state state="open"/>
  <service name="http" product="nginx" version="1.24.0"/></port>
</ports>
</host>
</nmaprun>
"""

_ALL_CLOSED_XML = """<?xml version="1.0"?>
<nmaprun>
<host>
<status state="up"/>
<address addr="127.0.0.1" addrtype="ipv4"/>
<ports>
<extraports state="closed" count="3">
<extrareasons reason="conn-refused" count="3" proto="tcp" ports="22,80,443"/>
</extraports>
</ports>
</host>
</nmaprun>
"""


def test_rejects_unsafe_target(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("must not exec nmap"))
    tool = NmapScanTool()
    result = tool.run(target="--script=vuln")
    assert result.status is ToolStatus.ERROR
    assert "unsafe" in result.error.lower()


def test_rejects_invalid_ports(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("must not exec nmap"))
    tool = NmapScanTool()
    result = tool.run(target="example.com", ports="80; rm -rf /")
    assert result.status is ToolStatus.ERROR
    assert "ports" in result.error.lower()


def test_missing_binary_returns_error(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    tool = NmapScanTool()
    result = tool.run(target="example.com")
    assert result.status is ToolStatus.ERROR
    assert "not found" in result.error.lower()


def test_successful_scan_parses_open_ports(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/nmap")
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        return subprocess.CompletedProcess(command, returncode=0, stdout=_SAMPLE_XML, stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    tool = NmapScanTool()
    result = tool.run(target="127.0.0.1", top_ports=100)

    assert result.status is ToolStatus.SUCCESS
    assert result.data["open_ports"] == [
        {
            "port": 80,
            "protocol": "tcp",
            "state": "open",
            "service": "http",
            "product": "nginx",
            "version": "1.24.0",
        }
    ]
    assert len(result.data["ports"]) == 2
    assert "--top-ports" in captured["command"]
    assert "100" in captured["command"]


def test_all_closed_extraports_are_not_lost(monkeypatch):
    """Nmap collapses same-state ports into <extraports>; we must still report them."""
    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/nmap")

    def fake_run(command, **kwargs):
        return subprocess.CompletedProcess(command, returncode=0, stdout=_ALL_CLOSED_XML, stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = NmapScanTool().run(target="127.0.0.1", ports="22,80,443")

    assert result.status is ToolStatus.SUCCESS
    assert result.data["open_ports"] == []
    assert {p["port"] for p in result.data["ports"]} == {22, 80, 443}
    assert all(p["state"] == "closed" for p in result.data["ports"])


def test_ports_argument_overrides_top_ports(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/nmap")
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        return subprocess.CompletedProcess(command, returncode=0, stdout=_SAMPLE_XML, stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    NmapScanTool().run(target="127.0.0.1", ports="22,80")

    assert "-p" in captured["command"]
    assert "22,80" in captured["command"]
    assert "--top-ports" not in captured["command"]


def test_top_ports_is_clamped(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/nmap")
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        return subprocess.CompletedProcess(command, returncode=0, stdout=_SAMPLE_XML, stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    NmapScanTool().run(target="127.0.0.1", top_ports=999_999)

    idx = captured["command"].index("--top-ports")
    assert captured["command"][idx + 1] == "1000"


def test_nonzero_exit_returns_error(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/nmap")

    def fake_run(command, **kwargs):
        return subprocess.CompletedProcess(command, returncode=1, stdout="", stderr="boom")

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = NmapScanTool().run(target="127.0.0.1")
    assert result.status is ToolStatus.ERROR
    assert "boom" in result.error


def test_timeout_returns_error(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/nmap")

    def fake_run(command, **kwargs):
        raise subprocess.TimeoutExpired(cmd=command, timeout=1)

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = NmapScanTool(timeout=1).run(target="127.0.0.1")
    assert result.status is ToolStatus.ERROR
    assert "timed out" in result.error.lower()


@pytest.mark.skipif(
    not shutil.which("nmap") or not os.environ.get("HEXAGENT_TEST_REAL_NMAP"),
    reason="opt-in: set HEXAGENT_TEST_REAL_NMAP=1 with nmap installed to run a real scan",
)
def test_real_scan_against_localhost():
    result = NmapScanTool(timeout=30).run(target="127.0.0.1", ports="65000-65001")
    assert result.status is ToolStatus.SUCCESS
    assert "host" in result.data
