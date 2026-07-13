# SerPent-ester Architecture

This document expands on the design summarised in the top-level `README.md`.

## Design goals

1. **Educational clarity** — the flow (plan → execute → evaluate → replan →
   report) should be easy to read and reason about.
2. **Safety by construction** — tools are deterministic mocks by default; the
   one real, network-touching tool (`nmap_scan`) and any state-changing tool
   (`http_post`) are explicitly marked `sensitive` and opt-in, with no
   exploitation code anywhere in the POC.
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
implement `_run`. A `sensitive: bool = False` class attribute marks tools that
perform a real, state-changing or network-touching action (`http_post`,
`nmap_scan`) — specialist agents gate these behind approval. `ToolRegistry` is
a small DI container that also renders a catalogue string for prompts.
`fixtures.py` derives a coherent, deterministic `SiteProfile` from the target
host so every mock tool describes the *same* imaginary site — ideal for
reproducible demos and tests.

### Planners (`app/planners`)
`BasePlanner` defines `create_plan` / `replan(..., last_result=None)`.
`HeuristicPlanner` is **reactive**: `create_plan` emits a minimal plan (a
`port_scan` step plus a synthesis step), and `replan` grows it based on the
structured data in `last_result`, dispatching on one of three
`ReplanReason` codes (`app/models/plan.py`):

| Reason | Trigger | Effect |
|---|---|---|
| `open_web_ports_found` | port scan reveals 80/443 open | queues `tech_fingerprint`, `http_header_inspect`, `security_headers`, `robots_txt`, `url_crawler` |
| `robots_paths_found` | robots.txt disallows a path | queues a targeted `http_get` on that path |
| `login_endpoint_found` | crawler finds a URL containing "login" | queues a controlled `http_post` to it |

Each handler re-verifies its own trigger condition from `last_result.data`
(not just the reason code) and is idempotent (checks whether the step it would
add already exists) — see `_on_open_web_ports` / `_on_robots_paths` /
`_on_login_endpoint` in `planner.py`. `LLMPlanner` asks a model for structured
JSON upfront and falls back to (and reuses the `replan` of) the heuristic
planner. `build_planner` selects the strategy.

### Agents (`app/agents`)
Pipeline agents, each LLM-optional with a deterministic fallback:
- **PlannerAgent** wraps a `BasePlanner`.
- **ExecutorAgent** selects a tool for one step (`select`), then dispatches the
  call to whichever domain specialist owns that tool (`execute`).
- **EvaluatorAgent** turns a `ToolResult` into observations/findings, flags
  human-validation points, and decides whether to replan (see the
  `ReplanReason` table above).
- **ReporterAgent** renders the markdown report (LLM adds an optional summary).

Domain specialists (`app/agents/specialists.py`) own a scoped slice of the
registry and are where the sensitive-action gate actually lives:
- **`SpecialistAgent`** (base) — `run(call)` looks up the tool; if it's
  `sensitive` and `require_sensitive_approval` is set, it consults an
  `approval_callback` *before* calling the registry. No callback → denied
  (fail-closed), not blocked.
- **`ReconAgent`** — `port_scan`, `nmap_scan`, `tech_fingerprint`, `robots_txt`,
  `url_crawler`, `security_headers`.
- **`HttpAnalysisAgent`** — `http_get`, `http_post`, `http_header_inspect`.

`ExecutorAgent` holds one instance of each specialist and picks the one whose
`TOOL_NAMES` contains the selected tool; a tool owned by neither falls back to
calling the registry directly (forward-compatible with future tools that
haven't been assigned a specialist yet).

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
- **Human intervention (end-of-run)** — when `require_human_approval` is set,
  the run pauses at the `human` checkpoint → `awaiting human approval` (a
  report is still produced).

Replanning is bounded by `MAX_REPLANS` (5) to guarantee termination even with
the reactive planner's multiple trigger points.

There is a second, independent human-oversight mechanism that is *not* a graph
node: the sensitive-action gate inside `SpecialistAgent.run` (see Agents,
above). It fires *before* a `sensitive` tool call, not at a fixed point in the
graph, and only when `SERPENTESTER_REQUIRE_SENSITIVE_APPROVAL` is set. Because a
denied action resolves to a `ToolResult(status=SKIPPED)` rather than raising,
`Plan.completed_ids()` treats `SKIPPED` (and `FAILED`) the same as `DONE` for
dependency resolution — otherwise a single denied action would permanently
block the synthesis step from ever becoming runnable.

## Extending toward real tooling

| Future capability | Where it plugs in |
|-------------------|-------------------|
| Nuclei / ffuf / SQLMap | New `BaseTool` subclasses + `default_registry()`; already-built pattern: see `NmapScanTool` |
| A new domain specialist | New `SpecialistAgent` subclass in `specialists.py`, registered in `ExecutorAgent` |
| Burp / proxy integration | A tool wrapping the proxy API |
| Browser automation | A Playwright-backed tool |
| Multi-agent collaboration | Extra nodes/agents + edges in `workflow.py` |
| Resumable/async human-in-the-loop | Replace the synchronous `approval_callback` with a LangGraph `interrupt` + checkpointer so approval can survive a process restart |
| Memory / RAG | Inject a retriever into agents; add a checkpointer to `compile()` |

Because the orchestration depends only on the `BaseTool`, `BasePlanner` and agent
interfaces, these additions require **no changes** to the graph wiring beyond
optional new nodes/edges.
