"""Findings and observations produced while executing the plan."""

from __future__ import annotations

import uuid
from enum import StrEnum

from pydantic import BaseModel, Field


class Severity(StrEnum):
    """Qualitative severity rating for a finding."""

    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class Observation(BaseModel):
    """A neutral fact gathered from a tool result, prior to interpretation."""

    source_tool: str = Field(..., description="Tool that produced the observation.")
    step_id: str | None = Field(default=None, description="Plan step that triggered the tool.")
    content: str = Field(..., description="What was observed.")


class Finding(BaseModel):
    """An interpreted, security-relevant conclusion drawn from observations."""

    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:8])
    title: str
    severity: Severity = Severity.INFO
    description: str
    evidence: list[str] = Field(
        default_factory=list, description="Observation snippets supporting the finding."
    )
    recommendation: str | None = Field(
        default=None, description="Suggested remediation or next investigative action."
    )
    requires_human_validation: bool = Field(
        default=False,
        description="True when a human should confirm before any follow-up action.",
    )
