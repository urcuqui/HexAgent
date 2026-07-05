"""Shared pytest fixtures."""

from __future__ import annotations

import pytest

from app.config import Settings
from app.tools.registry import ToolRegistry, default_registry


@pytest.fixture
def registry() -> ToolRegistry:
    """A registry populated with all built-in mock tools."""
    return default_registry()


@pytest.fixture
def offline_settings(tmp_path) -> Settings:
    """Deterministic, offline settings writing reports to a temp dir.

    ``_env_file=None`` disables loading the developer's local ``.env`` so the
    suite can't accidentally pick up e.g. ``HEXAGENT_ENABLE_NMAP=true`` and
    start running real scans in tests — every field is explicit here.
    """
    return Settings(
        _env_file=None,
        openai_api_key=None,
        mock_mode=True,
        enable_nmap=False,
        enable_playwright=False,
        enable_nuclei=False,
        require_sensitive_approval=False,
        max_iterations=12,
        require_human_approval=False,
        report_dir=tmp_path / "reports",
    )
