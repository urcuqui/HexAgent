# HexAgent Architecture

This document expands on the design summarised in the top-level `README.md`.

## Design goals

1. **Educational clarity** — the flow (plan → execute → evaluate → replan →
   report) should be easy to read and reason about.
2. **Safety by construction** — tools are deterministic mocks; no network or
   exploitation code exists in the POC.
3. **Extensibility without refactoring** — new tools, agents, prompts and graph
   nodes can be added behind stable interfaces.
4. **Runs anywhere** — the system is fully functional offline; an LLM is an
   optional enhancement, not a hard dependency.

## Layered components

```
config ─┐
        ▼
utils (logging, llm factory, parsing, report io)
        ▼
models  ──►  tools  ──►  planners  ──►  agents  ──►  graph  ──►  cli
                          (prompts feed planners/agents)
```

### Models (`app/models`)
Pydantic contracts shared everywhere. `ToolResult` is a uniform envelope so the
executor/evaluator treat heterogeneous tools generically. `Plan`/`PlanStep`
encode dependencies via `depends_on`, enabling later steps to consume earlier
outputs. `Report` is the structured object the reporter renders.

### Tools (`app/tools`)
`BaseTool` (Template Method) handles timing and error-wrapping; subclasses only
implement `_run`. `ToolRegistry` is a small DI container that also renders a
catalogue string for prompts. `fixtures.py` derives a coherent, deterministic
`SiteProfile` from the target host so every tool describes the *same* imaginary
site — ideal for reproducible demos and tests.

### Planners (`app/planners`)
`BasePlanner` defines `create_plan` / `replan`. `HeuristicPlanner` emits the
canonical recon recipe; `LLMPlanner` asks a model for structured JSON and falls
back to the heuristic plan on any failure. `build_planner` selects the strategy.

### Agents (`app/agents`)
Thin reasoning units, each LLM-optional with a deterministic fallback:
- **PlannerAgent** wraps a `BasePlanner`.
- **ExecutorAgent** selects and runs a tool for one step.
- **EvaluatorAgent** turns a `ToolResult` into observations/findings and decides
  whether to replan.
- **ReporterAgent** renders the markdown report (LLM adds an optional summary).

### Prompts (`app/prompts`)
Prompt text lives in `templates/*.txt`; `PromptLibrary` validates their presence
at construction and exposes typed accessors. No prompt strings are hardcoded in
agent code.

### Graph (`app/graph`)
- `state.py` — `AgentState` Pydantic model; nodes return partial dict updates and
  accumulate lists explicitly.
- `nodes.py` — `WorkflowNodes` bundles the agents (constructor injection) and
  exposes node callables (`intake`, `plan`, `execute`, `evaluate`, `replan`,
  `human_checkpoint`, `report`).
- `router.py` — a pure routing function, independently testable.
- `workflow.py` — assembles/compiles the graph and provides `run_workflow`.

## Control flow & termination

The graph loops `execute → evaluate` until one of three conditions holds:

- **Goal achieved** — no pending steps remain → `objective completed`.
- **Iteration budget reached** — `iterations >= max_iterations` →
  `maximum iterations reached`.
- **Human intervention** — when `require_human_approval` is set, the run pauses at
  the `human` checkpoint → `awaiting human approval` (a report is still produced).

Replanning is bounded by `MAX_REPLANS` to guarantee termination.

## Extending toward real tooling

| Future capability | Where it plugs in |
|-------------------|-------------------|
| Nmap / Nuclei / ffuf / SQLMap | New `BaseTool` subclasses + `default_registry()` |
| Burp / proxy integration | A tool wrapping the proxy API |
| Browser automation | A Playwright-backed tool |
| Multi-agent collaboration | Extra nodes/agents + edges in `workflow.py` |
| Human-in-the-loop approval | Replace the `human` node with a LangGraph interrupt |
| Memory / RAG | Inject a retriever into agents; add a checkpointer to `compile()` |

Because the orchestration depends only on the `BaseTool`, `BasePlanner` and agent
interfaces, these additions require **no changes** to the graph wiring beyond
optional new nodes/edges.
