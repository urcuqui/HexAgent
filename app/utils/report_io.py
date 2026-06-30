"""Persistence helpers for generated reports."""

from __future__ import annotations

import datetime as _dt
import re
from pathlib import Path

from app.config import Settings, get_settings
from app.utils.logging import get_logger

logger = get_logger(__name__)


def _slugify(value: str) -> str:
    """Return a filesystem-safe slug derived from ``value``."""
    slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", value.strip().lower()).strip("-")
    return slug or "target"


def save_report(markdown: str, target: str, settings: Settings | None = None) -> Path:
    """Write ``markdown`` to a timestamped file in the configured report dir.

    Args:
        markdown: The rendered report contents.
        target: Target identifier, used in the filename.
        settings: Optional settings override.

    Returns:
        The path the report was written to.
    """
    settings = settings or get_settings()
    report_dir = settings.ensure_report_dir()
    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    path = report_dir / f"recon-{_slugify(target)}-{stamp}.md"
    path.write_text(markdown, encoding="utf-8")
    logger.info("Report written to %s", path)
    return path
