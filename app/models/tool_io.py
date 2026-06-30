"""Structured input/output contracts for tools.

Every tool consumes a :class:`ToolCall` and returns a :class:`ToolResult` whose
``data`` payload is a tool-specific Pydantic model. Keeping a uniform envelope
lets the executor and evaluator reason about heterogeneous tools generically.
"""

from __future__ import annotations

import time
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class ToolStatus(StrEnum):
    """Outcome of a tool invocation."""

    SUCCESS = "success"
    ERROR = "error"
    SKIPPED = "skipped"


class ToolCall(BaseModel):
    """A request to execute a named tool with arbitrary keyword arguments."""

    tool_name: str = Field(..., description="Registered name of the tool to run.")
    arguments: dict[str, Any] = Field(
        default_factory=dict, description="Keyword arguments forwarded to the tool."
    )
    rationale: str | None = Field(
        default=None, description="Why the agent selected this tool (for the report)."
    )


class ToolResult(BaseModel):
    """Uniform envelope returned by every tool."""

    tool_name: str
    status: ToolStatus = ToolStatus.SUCCESS
    summary: str = Field(..., description="Human-readable one-line summary of the result.")
    data: dict[str, Any] = Field(
        default_factory=dict, description="Structured tool-specific payload."
    )
    error: str | None = Field(default=None, description="Error message when status is ERROR.")
    duration_ms: float = Field(default=0.0, description="Simulated execution time in ms.")
    timestamp: float = Field(default_factory=time.time)

    @classmethod
    def ok(
        cls,
        tool_name: str,
        summary: str,
        data: BaseModel | dict[str, Any] | None = None,
        duration_ms: float = 0.0,
    ) -> ToolResult:
        """Convenience constructor for a successful result."""
        payload = data.model_dump(mode="json") if isinstance(data, BaseModel) else (data or {})
        return cls(
            tool_name=tool_name,
            status=ToolStatus.SUCCESS,
            summary=summary,
            data=payload,
            duration_ms=duration_ms,
        )

    @classmethod
    def fail(cls, tool_name: str, error: str) -> ToolResult:
        """Convenience constructor for a failed result."""
        return cls(
            tool_name=tool_name,
            status=ToolStatus.ERROR,
            summary=f"{tool_name} failed",
            error=error,
        )
