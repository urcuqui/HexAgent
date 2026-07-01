"""Graph node implementations.

Nodes are methods on :class:`WorkflowNodes`, which receives its collaborating
agents via constructor injection. Each node takes the current
:class:`~app.graph.state.AgentState` and returns a partial ``dict`` of updates.
"""

from __future__ import annotations

from app.agents.evaluator import EvaluatorAgent
from app.agents.executor import ExecutorAgent
from app.agents.planner_agent import PlannerAgent
from app.agents.reporter import ReporterAgent
from app.graph.state import AgentState
from app.models.plan import StepStatus
from app.models.report import ExecutedStep, Report
from app.models.tool_io import ToolStatus
from app.utils.logging import get_logger

logger = get_logger(__name__)

# Bumped from the original 2: a single reactive run can now legitimately
# replan multiple times (open web ports -> HTTP phase, robots.txt -> targeted
# GET, login endpoint -> controlled POST).
MAX_REPLANS = 5

_STEP_STATUS_BY_TOOL_STATUS = {
    ToolStatus.SUCCESS: StepStatus.DONE,
    ToolStatus.SKIPPED: StepStatus.SKIPPED,
    ToolStatus.ERROR: StepStatus.FAILED,
}


class WorkflowNodes:
    """Bundles the agents and exposes them as LangGraph node callables."""

    def __init__(
        self,
        planner: PlannerAgent,
        executor: ExecutorAgent,
        evaluator: EvaluatorAgent,
        reporter: ReporterAgent,
    ) -> None:
        self._planner = planner
        self._executor = executor
        self._evaluator = evaluator
        self._reporter = reporter

    # -- nodes --------------------------------------------------------------
    def intake(self, state: AgentState) -> dict:
        """Record the user objective and seed the reasoning history."""
        msg = f"Objective received: {state.objective} (target: {state.target})"
        logger.info(msg)
        return {"reasoning_history": [*state.reasoning_history, msg]}

    def plan(self, state: AgentState) -> dict:
        """Create the initial plan."""
        plan = self._planner.plan(state.objective, state.target)
        note = f"Planned {len(plan.steps)} step(s): {plan.rationale}"
        return {"plan": plan, "reasoning_history": [*state.reasoning_history, note]}

    def execute(self, state: AgentState) -> dict:
        """Execute the next runnable plan step and record its tool result."""
        assert state.plan is not None
        step = state.plan.next_runnable()
        if step is None:
            return {}
        step.status = StepStatus.IN_PROGRESS
        if step.tool_name is None:
            # Synthesis/summary step: no tool is run; just mark it complete.
            step.status = StepStatus.DONE
            note = f"Completed synthesis step {step.id}: {step.description}"
            return {
                "plan": state.plan,
                "completed_step_ids": [*state.completed_step_ids, step.id],
                "iterations": state.iterations + 1,
                "reasoning_history": [*state.reasoning_history, note],
            }
        result = self._executor.execute(step, state.target, state.observation_texts())
        step.status = _STEP_STATUS_BY_TOOL_STATUS.get(result.status, StepStatus.FAILED)
        executed = ExecutedStep(
            step_id=step.id,
            description=step.description,
            tool_name=result.tool_name,
            status=result.status.value,
            result_summary=result.summary,
        )
        note = f"Executed {step.id} -> {result.tool_name}: {result.summary}"
        return {
            "plan": state.plan,
            "completed_step_ids": [*state.completed_step_ids, step.id],
            "tool_results": [*state.tool_results, result],
            "executed_steps": [*state.executed_steps, executed],
            "iterations": state.iterations + 1,
            "reasoning_history": [*state.reasoning_history, note],
        }

    def evaluate(self, state: AgentState) -> dict:
        """Interpret the most recent tool result into observations/findings."""
        if not state.tool_results or not state.completed_step_ids:
            return {}
        step = state.plan.get(state.completed_step_ids[-1]) if state.plan else None
        # Synthesis steps (no tool) produce no new result to evaluate.
        if step is not None and step.tool_name is None:
            return {}
        result = state.tool_results[-1]
        evaluation = self._evaluator.evaluate(state.objective, step, result)  # type: ignore[arg-type]
        human_points = [
            f"{f.title}: {f.description}"
            for f in evaluation.findings
            if f.requires_human_validation
        ]
        note = f"Evaluated {result.tool_name}: {len(evaluation.findings)} finding(s)"
        return {
            "observations": [*state.observations, *evaluation.observations],
            "findings": [*state.findings, *evaluation.findings],
            "human_validation_points": [*state.human_validation_points, *human_points],
            "needs_replan": evaluation.needs_replan and state.replans < MAX_REPLANS,
            "replan_reason": evaluation.replan_reason,
            "reasoning_history": [*state.reasoning_history, note],
        }

    def replan(self, state: AgentState) -> dict:
        """Revise the plan in response to new information."""
        assert state.plan is not None
        last_result = state.tool_results[-1] if state.tool_results else None
        plan = self._planner.replan(
            state.plan, state.replan_reason, state.observation_texts(), last_result
        )
        note = f"Replanned ({state.replan_reason}); now {len(plan.steps)} step(s)"
        return {
            "plan": plan,
            "replans": state.replans + 1,
            "needs_replan": False,
            "reasoning_history": [*state.reasoning_history, note],
        }

    def human_checkpoint(self, state: AgentState) -> dict:
        """Mark the run as awaiting human approval (a terminal pause)."""
        note = "Human approval checkpoint reached; report generated for review."
        logger.info(note)
        return {"awaiting_human": True, "reasoning_history": [*state.reasoning_history, note]}

    def report(self, state: AgentState) -> dict:
        """Assemble and render the final markdown report."""
        next_actions = self._derive_next_actions(state)
        plan_complete = state.plan.is_complete() if state.plan else False
        if state.awaiting_human:
            stopped = "awaiting human approval"
        elif state.iterations >= state.max_iterations and not plan_complete:
            stopped = "maximum iterations reached"
        else:
            stopped = "objective completed"
        report = Report(
            objective=state.objective,
            plan=state.plan,  # type: ignore[arg-type]
            executed_steps=state.executed_steps,
            tool_results=state.tool_results,
            findings=state.findings,
            next_actions=next_actions,
            human_validation_points=state.human_validation_points,
            iterations=state.iterations,
            stopped_reason=stopped,
        )
        markdown = self._reporter.render(report)
        return {
            "report": report,
            "report_markdown": markdown,
            "next_actions": next_actions,
            "stopped_reason": stopped,
        }

    @staticmethod
    def _derive_next_actions(state: AgentState) -> list[str]:
        actions = [f.recommendation for f in state.findings if f.recommendation]
        # Deduplicate while preserving order.
        seen: set[str] = set()
        unique = [a for a in actions if not (a in seen or seen.add(a))]
        if not unique:
            unique = ["Review the gathered reconnaissance data and define follow-up tests."]
        return unique
