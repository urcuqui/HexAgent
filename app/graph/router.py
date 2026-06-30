"""Conditional routing logic for the workflow graph.

Separating the routing predicate from node side-effects keeps each piece small
and independently testable. The router is a pure function of the current state.
"""

from __future__ import annotations

from app.graph.state import AgentState

# Routing destinations returned by :func:`route_after_evaluate`.
EXECUTE = "execute"
REPLAN = "replan"
HUMAN = "human"
REPORT = "report"


def route_after_evaluate(state: AgentState) -> str:
    """Decide the next node after an evaluation step.

    Priority order:
      1. Stop if the iteration budget is exhausted.
      2. Replan when the evaluator requested it.
      3. Continue executing while runnable steps remain.
      4. Pause for human approval if configured.
      5. Otherwise produce the report.
    """
    if state.iterations >= state.max_iterations:
        return REPORT
    if state.needs_replan:
        return REPLAN
    if state.plan is not None and state.plan.next_runnable() is not None:
        return EXECUTE
    if state.require_human_approval:
        return HUMAN
    return REPORT
