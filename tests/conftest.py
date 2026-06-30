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
    """Deterministic, offline settings writing reports to a temp dir."""
    return Settings(
        openai_api_key=None,
        mock_mode=True,
        max_iterations=12,
        require_human_approval=False,
        report_dir=tmp_path / "reports",
    )
