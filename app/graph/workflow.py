"""Assembly and execution of the LangGraph workflow.

``build_workflow`` wires nodes and conditional edges into a compiled graph;
``run_workflow`` is a convenience entry point that constructs the agents from
settings, runs the graph for an objective/target, and returns the final state.
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from app.agents.evaluator import EvaluatorAgent
from app.agents.executor import ExecutorAgent
from app.agents.planner_agent import PlannerAgent
from app.agents.reporter import ReporterAgent
from app.agents.specialists import ApprovalCallback
from app.config import Settings, get_settings
from app.graph.nodes import WorkflowNodes
from app.graph.router import EXECUTE, HUMAN, REPLAN, REPORT, route_after_evaluate
from app.graph.state import AgentState
from app.tools.registry import ToolRegistry, default_registry
from app.utils.llm import build_llm
from app.utils.logging import get_logger

logger = get_logger(__name__)


def build_workflow(nodes: WorkflowNodes):
    """Construct and compile the LangGraph state machine.

    Args:
        nodes: The node bundle providing the agent-backed callables.

    Returns:
        A compiled LangGraph runnable.
    """
    graph = StateGraph(AgentState)

    graph.add_node("intake", nodes.intake)
    graph.add_node("plan", nodes.plan)
    graph.add_node("execute", nodes.execute)
    graph.add_node("evaluate", nodes.evaluate)
    graph.add_node("replan", nodes.replan)
    graph.add_node("human", nodes.human_checkpoint)
    graph.add_node("report", nodes.report)

    graph.add_edge(START, "intake")
    graph.add_edge("intake", "plan")
    graph.add_edge("plan", "execute")
    graph.add_edge("execute", "evaluate")
    graph.add_conditional_edges(
        "evaluate",
        route_after_evaluate,
        {EXECUTE: "execute", REPLAN: "replan", HUMAN: "human", REPORT: "report"},
    )
    graph.add_edge("replan", "execute")
    graph.add_edge("human", "report")
    graph.add_edge("report", END)

    return graph.compile()


def build_nodes(
    registry: ToolRegistry | None = None,
    settings: Settings | None = None,
    approval_callback: ApprovalCallback | None = None,
) -> WorkflowNodes:
    """Create the agent node bundle, wiring in an LLM when configured.

    Args:
        approval_callback: Consulted before any tool marked ``sensitive`` runs
            (e.g. ``http_post``, ``nmap_scan``) when
            ``settings.require_sensitive_approval`` is set. Without one, such
            actions are denied by default (fail-closed).
    """
    settings = settings or get_settings()
    registry = registry or default_registry(enable_nmap=settings.enable_nmap)
    llm = build_llm(settings)
    return WorkflowNodes(
        planner=PlannerAgent(registry, llm),
        executor=ExecutorAgent(
            registry,
            llm,
            approval_callback=approval_callback,
            require_sensitive_approval=settings.require_sensitive_approval,
        ),
        evaluator=EvaluatorAgent(llm),
        reporter=ReporterAgent(llm),
    )


def run_workflow(
    objective: str,
    target: str,
    settings: Settings | None = None,
    registry: ToolRegistry | None = None,
    approval_callback: ApprovalCallback | None = None,
) -> AgentState:
    """Run the full workflow for an objective/target and return the final state.

    Args:
        objective: Natural-language goal for the session.
        target: Host or URL to (mock) assess.
        settings: Optional settings override; defaults to :func:`get_settings`.
        registry: Optional tool registry override; defaults to all mock tools.
        approval_callback: See :func:`build_nodes`.
    """
    settings = settings or get_settings()
    nodes = build_nodes(registry, settings, approval_callback)
    app = build_workflow(nodes)

    initial = AgentState(
        objective=objective,
        target=target,
        max_iterations=settings.max_iterations,
        require_human_approval=settings.require_human_approval,
    )
    logger.info("Invoking workflow (mock_mode=%s, llm=%s)", settings.mock_mode, settings.use_llm)
    # A recursion limit comfortably above max_iterations covers the extra
    # plan/evaluate/replan/report transitions per execute step.
    raw = app.invoke(initial, config={"recursion_limit": settings.max_iterations * 4 + 20})
    return AgentState.model_validate(raw)
