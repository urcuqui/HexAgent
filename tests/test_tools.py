"""Tests for the mock tools and registry."""

from __future__ import annotations

import pytest

from app.models.tool_io import ToolStatus
from app.tools.fixtures import build_profile, normalise_target

EXPECTED_TOOLS = {
    "http_get",
    "http_post",
    "http_header_inspect",
    "robots_txt",
    "security_headers",
    "tech_fingerprint",
    "port_scan",
    "url_crawler",
}


def test_registry_contains_all_tools(registry):
    assert set(registry.names()) == EXPECTED_TOOLS


def test_catalogue_is_non_empty(registry):
    catalogue = registry.catalogue()
    assert all(name in catalogue for name in EXPECTED_TOOLS)


@pytest.mark.parametrize("name", sorted(EXPECTED_TOOLS))
def test_each_tool_succeeds(registry, name):
    result = registry.run(name, target="example.com")
    assert result.status is ToolStatus.SUCCESS
    assert result.summary
    assert result.duration_ms >= 0


def test_unknown_tool_returns_error(registry):
    result = registry.run("does_not_exist", target="example.com")
    assert result.status is ToolStatus.ERROR


def test_profile_is_deterministic():
    a = build_profile("example.com")
    b = build_profile("example.com")
    assert a == b


def test_normalise_target_handles_bare_host_and_url():
    assert normalise_target("example.com") == ("https://example.com", "example.com")
    base, host = normalise_target("http://foo.test/path")
    assert base == "http://foo.test" and host == "foo.test"


def test_security_headers_reports_missing(registry):
    result = registry.run("security_headers", target="example.com")
    assert "grade" in result.data
    assert isinstance(result.data["missing"], list)


def test_http_get_known_path_is_200(registry):
    result = registry.run("http_get", target="example.com", path="/")
    assert result.data["status_code"] == 200
