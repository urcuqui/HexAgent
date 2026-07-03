"""LangGraph workflow state.

The state is a Pydantic model so it is self-validating and serialisable. Nodes
return partial ``dict`` updates which LangGraph merges into the state. Lists are
accumulated explicitly inside the nodes (they read the current value and return
the extended list), keeping the reducer semantics simple and explicit.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.models.findings import Finding, Observation
from app.models.plan import Plan
from app.models.report import ExecutedStep, Report
from app.models.tool_io import ToolResult


class AgentState(BaseModel):
    """Mutable state threaded through every node of the workflow."""

    # Inputs
    objective: str
    target: str
    max_iterations: int = 12
    require_human_approval: bool = False

    # Plan & progress
    plan: Plan | None = None
    completed_step_ids: list[str] = Field(default_factory=list)

    # Accumulated knowledge
    observations: list[Observation] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)
    tool_results: list[ToolResult] = Field(default_factory=list)
    executed_steps: list[ExecutedStep] = Field(default_factory=list)
    reasoning_history: list[str] = Field(default_factory=list)

    # Control flow
    iterations: int = 0
    needs_replan: bool = False
    replan_reason: str = ""
    replans: int = 0
    awaiting_human: bool = False
    stopped_reason: str = ""

    # Browser session (populated by evaluator when Playwright tools are active)
    browser_session_active: bool = False
    browser_screenshots: list[str] = Field(default_factory=list)

    # Outputs
    human_validation_points: list[str] = Field(default_factory=list)
    next_actions: list[str] = Field(default_factory=list)
    report: Report | None = None
    report_markdown: str | None = None

    def observation_texts(self) -> list[str]:
        """Return observations as plain strings (handy for prompts)."""
        return [o.content for o in self.observations]
