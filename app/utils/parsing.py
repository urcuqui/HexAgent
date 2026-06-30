"""Helpers for coercing LLM text output into structured data.

LLMs frequently wrap JSON in prose or markdown fences. :func:`extract_json`
robustly recovers the first balanced JSON object from a string so callers can
validate it against a Pydantic model.
"""

from __future__ import annotations

import json
from typing import Any

from app.utils.logging import get_logger

logger = get_logger(__name__)


def _strip_fences(text: str) -> str:
    """Remove leading/trailing markdown code fences if present."""
    stripped = text.strip()
    if stripped.startswith("```"):
        # Drop the first fence line and any trailing fence.
        lines = stripped.splitlines()
        lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        stripped = "\n".join(lines)
    return stripped.strip()


def extract_json(text: str) -> dict[str, Any]:
    """Return the first balanced JSON object found in ``text``.

    Tries a direct parse first, then locates the outermost ``{...}`` span.

    Raises:
        ValueError: If no valid JSON object can be recovered.
    """
    candidate = _strip_fences(text)
    try:
        parsed = json.loads(candidate)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    start = candidate.find("{")
    if start == -1:
        raise ValueError("No JSON object found in LLM output")

    depth = 0
    for end in range(start, len(candidate)):
        char = candidate[end]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                snippet = candidate[start : end + 1]
                try:
                    return json.loads(snippet)
                except json.JSONDecodeError as exc:
                    logger.debug("Failed to parse JSON snippet: %s", exc)
                    break
    raise ValueError("Could not extract balanced JSON object from LLM output")
