"""The final structured report model."""

from __future__ import annotations

import datetime as _dt

from pydantic import BaseModel, Field

from app.models.findings import Finding
from app.models.plan import Plan
from app.models.tool_io import ToolResult


class ExecutedStep(BaseModel):
    """Record of a step that was executed, pairing the step with its tool result."""

    step_id: str
    description: str
    tool_name: str
    status: str
    result_summary: str


class Report(BaseModel):
    """Aggregated outcome of a workflow run, rendered to markdown by the reporter."""

    objective: str
    generated_at: str = Field(
        default_factory=lambda: _dt.datetime.now(_dt.UTC).isoformat(timespec="seconds")
    )
    plan: Plan
    executed_steps: list[ExecutedStep] = Field(default_factory=list)
    tool_results: list[ToolResult] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)
    next_actions: list[str] = Field(default_factory=list)
    human_validation_points: list[str] = Field(default_factory=list)
    iterations: int = 0
    stopped_reason: str = ""
