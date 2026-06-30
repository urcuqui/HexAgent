"""Plan and plan-step models.

A :class:`Plan` is an ordered list of :class:`PlanStep` objects. Each step names
the tool it intends to use and may declare ``depends_on`` so that later steps can
consume the outputs of earlier ones.
"""

from __future__ import annotations

import uuid
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class StepStatus(StrEnum):
    """Lifecycle state of a single plan step."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"


class PlanStep(BaseModel):
    """A single actionable unit of the plan."""

    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:8])
    description: str = Field(..., description="Human-readable goal of this step.")
    tool_name: str | None = Field(
        default=None, description="Preferred tool; may be chosen at execution time if None."
    )
    arguments: dict[str, Any] = Field(
        default_factory=dict, description="Static arguments known at planning time."
    )
    depends_on: list[str] = Field(
        default_factory=list, description="IDs of steps whose output this step consumes."
    )
    status: StepStatus = StepStatus.PENDING

    def is_runnable(self, completed_ids: set[str]) -> bool:
        """Return True if the step is pending and all dependencies are complete."""
        return self.status == StepStatus.PENDING and set(self.depends_on).issubset(completed_ids)


class Plan(BaseModel):
    """An ordered, mutable collection of plan steps."""

    objective: str
    steps: list[PlanStep] = Field(default_factory=list)
    rationale: str | None = Field(default=None, description="Planner's reasoning for the plan.")

    def pending_steps(self) -> list[PlanStep]:
        """Steps that have not yet completed, failed or been skipped."""
        return [s for s in self.steps if s.status == StepStatus.PENDING]

    def completed_ids(self) -> set[str]:
        """IDs of steps that finished successfully."""
        return {s.id for s in self.steps if s.status == StepStatus.DONE}

    def next_runnable(self) -> PlanStep | None:
        """Return the first runnable step honouring declared dependencies."""
        done = self.completed_ids()
        for step in self.steps:
            if step.is_runnable(done):
                return step
        return None

    def get(self, step_id: str) -> PlanStep | None:
        """Look up a step by id."""
        return next((s for s in self.steps if s.id == step_id), None)

    def is_complete(self) -> bool:
        """True when no pending steps remain."""
        return len(self.pending_steps()) == 0
