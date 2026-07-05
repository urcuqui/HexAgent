"""Structured payloads produced by the Nuclei tools.

Mirrors the shape of ``app/models/tool_io.py``: a Nuclei-specific payload that
gets dropped into ``ToolResult.data`` (via ``ToolResult.ok``) so the rest of
the system (evaluator, planner, reporter) keeps reasoning about a uniform
``ToolResult`` envelope instead of a bespoke Nuclei type.

Every :class:`NucleiFinding` is a candidate observation, never a confirmed
vulnerability — ``confidence``/``validation_required`` make that explicit so
downstream consumers (the evaluator, the report) can't mistake scanner output
for a validated finding.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class NucleiFinding(BaseModel):
    """A single matched template, treated as an unverified candidate."""

    template_id: str | None = None
    template_name: str | None = None
    severity: str | None = None
    matched_at: str | None = None
    matcher_name: str | None = None
    extracted_results: list[str] = Field(default_factory=list)
    description: str | None = None
    tags: list[str] = Field(default_factory=list)
    references: list[str] = Field(default_factory=list)
    curl_command: str | None = None
    confidence: str = "candidate"
    validation_required: bool = True


class NucleiScanResult(BaseModel):
    """Uniform payload for both ``nuclei_scan_url`` and ``nuclei_scan_urls``."""

    success: bool
    action: str
    targets_scanned: list[str] = Field(default_factory=list)
    targets_skipped: list[dict] = Field(default_factory=list)
    command_summary: str = ""
    findings: list[NucleiFinding] = Field(default_factory=list)
    result_count: int = 0
    duration_seconds: float = 0.0
    errors: list[str] = Field(default_factory=list)
