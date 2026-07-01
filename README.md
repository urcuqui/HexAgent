# HexAgent

**HexAgent** is an educational proof-of-concept that demonstrates how an AI agent
can *orchestrate* web-application reconnaissance using
[LangGraph](https://github.com/langchain-ai/langgraph) and
[LangChain](https://github.com/langchain-ai/langchain). It plans, selects and runs
tools, evaluates results, replans when needed, and produces a markdown report.

> ⚠️ **Educational use only.** Every tool is a **deterministic mock** — HexAgent
> performs **no real network activity and no exploitation**. It is designed as a
> foundation for learning and for safely extending
> toward real, *authorised* tooling later. Only ever target systems you own or are
> explicitly permitted to test.

---

## Features

- 🧠 **Agentic LangGraph workflow** — objective → plan → execute → evaluate →
  (replan) → report, with iterative looping.
- 🎯 **Reactive plan/replan, not just a fixed recipe** — the plan starts minimal
  (scan for open ports) and *grows* based on what each result reveals: open web
  ports queue HTTP-layer analysis, a robots.txt disallow queues a targeted GET,
  a discovered login endpoint queues a controlled POST.
- 🧩 **Domain-specialist agents** — a `ReconAgent` and an `HttpAnalysisAgent` each
  own a scoped slice of the tool registry, alongside the Planner/Evaluator/
  Reporter pipeline agents.
- 🧷 **Human-in-the-loop, before *and* after** — sensitive tools (`http_post`,
  `nmap_scan`) pause for approval *before* running (fail-closed if unanswered),
  plus an optional end-of-run checkpoint before the report.
- 🧩 **Modular, SOLID architecture** — clean separation of models, tools, planners,
  agents, prompts, graph and utilities.
- 🛠️ **Pluggable tool registry** — nine tools (eight deterministic mocks plus an
  opt-in real `nmap_scan`) returning structured Pydantic models; add real tools
  without touching the orchestration layer.
- 🔁 **Iterative control flow** — stops when the goal is met, the iteration budget
  is exhausted, or human approval is required.
- 🤖 **LLM-optional** — runs fully offline with deterministic heuristic agents;
  drop in any OpenAI-compatible endpoint to enable LLM-driven planning/evaluation.
- 📄 **Markdown reports** — objective, plan, executed steps, tool outputs, findings,
  suggested next actions and human-validation points.
- ✅ **Tested & linted** — pytest suite plus ruff configuration.

---

## Architecture

HexAgent is layered so that each concern is independently testable and replaceable:

| Layer | Package | Responsibility |
|-------|---------|----------------|
| Models | `app/models` | Pydantic contracts: `Plan`, `PlanStep`, `ReplanReason`, `ToolCall`, `ToolResult`, `Finding`, `Report`. |
| Tools | `app/tools` | `BaseTool` abstraction (with a `sensitive` flag), `ToolRegistry`, and the tools. |
| Planners | `app/planners` | `HeuristicPlanner` (offline, reactive) and `LLMPlanner` behind a `BasePlanner` interface. |
| Agents | `app/agents` | Planner / Executor / Evaluator / Reporter pipeline agents, plus `ReconAgent` / `HttpAnalysisAgent` domain specialists (`app/agents/specialists.py`). |
| Prompts | `app/prompts` | Prompt **text files** + a loader (no hardcoded prompts). |
| Graph | `app/graph` | `AgentState`, nodes, router and the compiled LangGraph workflow. |
| Utils | `app/utils` | Logging, LLM factory, JSON parsing, report persistence. |

### Graph workflow

```mermaid
flowchart TD
    START([Start]) --> intake[Intake objective]
    intake --> plan[Planner: build plan]
    plan --> execute[Executor: select &amp; run tool]
    execute --> evaluate[Evaluator: observations &amp; findings]
    evaluate -->|more steps| execute
    evaluate -->|needs replan| replan[Replan]
    replan --> execute
    evaluate -->|human approval required| human[Human checkpoint]
    evaluate -->|done / max iterations| report[Reporter: render markdown]
    human --> report
    report --> END([End])
```

The routing predicate (`app/graph/router.py`) decides the next hop after each
evaluation in this priority order:

1. **Iteration budget exhausted** → report.
2. **Replan requested** by the evaluator → replan, then continue executing.
3. **Runnable steps remain** → execute the next step.
4. **Human approval configured** → human checkpoint (a terminal pause that still
   emits a report for review).
5. Otherwise → report.

### State

`AgentState` (a Pydantic model) is threaded through every node and tracks the
`objective`, `target`, current `plan`, `completed_step_ids`, accumulated
`observations`, `findings`, `tool_results`, `executed_steps`, `reasoning_history`,
plus control-flow fields (`iterations`, `needs_replan`, `replans`,
`awaiting_human`, `stopped_reason`) and outputs (`report`, `report_markdown`).

---

## Project structure

```
HexAgent/
├── app/
│   ├── agents/        # planner / executor / evaluator / reporter agents
│   ├── graph/         # state, nodes, router, compiled workflow
│   ├── models/        # Pydantic models (plan, tool IO, findings, report)
│   ├── planners/      # heuristic + LLM planners behind a base interface
│   ├── prompts/       # prompt loader + templates/*.txt
│   ├── tools/         # BaseTool, registry, mock tools, deterministic fixtures
│   ├── utils/         # logging, LLM factory, parsing, report IO
│   ├── templates/     # Jinja2 templates for the web UI
│   ├── static/        # CSS/JS for the web UI
│   ├── cli.py         # command-line interface
│   ├── web.py         # Flask web UI (optional; `uv sync --extra web`)
│   └── config.py      # pydantic-settings configuration
├── examples/          # runnable programmatic example
├── scripts/           # dev helper scripts (e.g. hexagent.sh)
├── tests/             # pytest suite
├── docs/              # architecture notes
├── main.py            # thin launcher -> app.cli:main
├── pyproject.toml
└── .env.example
```

---

## Installation

Requires **Python 3.12+** and [`uv`](https://github.com/astral-sh/uv).

```bash
uv sync --extra dev      # create the venv and install all dependencies
cp .env.example .env     # then paste your Vercel AI Gateway key (works without one)
```

Set `AI_GATEWAY_API_KEY` in `.env` to enable LLM-driven agents via the Vercel AI
Gateway; leave it empty to run fully offline in deterministic mock mode.

---

## Configuration

All settings are environment variables (see `.env.example`). HexAgent is
pre-configured for the [Vercel AI Gateway](https://vercel.com/docs/ai-gateway)
(an OpenAI-compatible endpoint). With **no API key**, it runs in deterministic
**mock mode**.

| Variable | Default | Description |
|----------|---------|-------------|
| `AI_GATEWAY_API_KEY` | _empty_ | Vercel AI Gateway key; enables LLM agents when set. `OPENAI_API_KEY` is also accepted. |
| `OPENAI_BASE_URL` | `https://ai-gateway.vercel.sh/v1` | OpenAI-compatible base URL (override for other hosts). |
| `HEXAGENT_MODEL` | `openai/gpt-4o-mini` | Model id in `provider/model` format (e.g. `anthropic/claude-sonnet-4.6`). |
| `HEXAGENT_MOCK_MODE` | `false` | Force offline determinism. |
| `HEXAGENT_ENABLE_NMAP` | `false` | Register the real `nmap_scan` tool (shells out to a local `nmap` binary) and make the heuristic planner prefer it over mock `port_scan` for the initial scan step. Only enable against explicitly authorised targets. |
| `HEXAGENT_MAX_ITERATIONS` | `12` | Iteration budget before halting. |
| `HEXAGENT_REQUIRE_HUMAN_APPROVAL` | `false` | Pause for human review before the report (end-of-run gate). |
| `HEXAGENT_REQUIRE_SENSITIVE_APPROVAL` | `false` | Pause *before* running any tool marked `sensitive` (`http_post`, `nmap_scan`). Fails closed (denies) if no approval callback is wired. |
| `HEXAGENT_LOG_LEVEL` | `INFO` | Logging level. |
| `HEXAGENT_REPORT_DIR` | `reports` | Output directory for reports. |

---

## Usage

### CLI

> On macOS, `uv run hexagent` can intermittently fail with
> `ModuleNotFoundError: No module named 'app'` (see the troubleshooting note
> below). If that happens, swap in `./scripts/hexagent.sh` — same arguments,
> self-healing. Every example below works with either.

```bash
# Offline, deterministic run; print the report to stdout
uv run hexagent --objective "Recon the lab box" --target demo.thm.local --mock --print

# Cap iterations and require a human approval checkpoint
uv run hexagent -o "Map the attack surface" -t example.thm --max-iterations 6 --human-approval
```

#### Flags

| Flag | Default | What it does |
|---|---|---|
| `--objective` / `-o` | _required_ | Natural-language goal passed to the planner (e.g. `"Map the attack surface"`). |
| `--target` / `-t` | _required_ | Host or URL to assess. Only ever an authorised lab/test target — see the Disclaimer. |
| `--mock` | off | Forces `mock_mode=True` for this run regardless of `.env`/API key: no LLM calls, heuristic planner/evaluator only. **Does not disable `nmap_scan`** — if `HEXAGENT_ENABLE_NMAP=true` is set, `--mock` is exactly how you get a real scan with fully deterministic planning/evaluation around it (see `docs/nmap-testing.md`). To guarantee nothing real runs at all, also set `HEXAGENT_ENABLE_NMAP=false`. |
| `--max-iterations N` | `12` (or `HEXAGENT_MAX_ITERATIONS`) | Caps `execute` steps before the run force-stops with `stopped_reason = "maximum iterations reached"`. Lower it for a quick smoke test or to bound cost/time on an LLM-driven run. |
| `--human-approval` | off | End-of-run gate: once the plan has nothing left to run, pause at the `human` checkpoint (`stopped_reason = "awaiting human approval"`) before finishing. A report is still generated either way — this only affects whether the run "waits" at the end. |
| `--require-sensitive-approval` | off | Pre-action gate: before running any tool marked `sensitive` (`http_post`, `nmap_scan`), print the pending call and prompt `Approve this action? [y/N]`. Answering anything but `y`/`yes` — or piping no input at all — skips that action rather than blocking forever. |
| `--no-save` | off (report *is* saved) | Skip writing the markdown report to `HEXAGENT_REPORT_DIR` (`reports/` by default). Use for throwaway/repeated test runs so `reports/` doesn't fill up. |
| `--print` | off | Print the rendered markdown report to stdout. Independent of `--no-save` — combine both to see the report without persisting it anywhere. |

#### Common combinations

```bash
# Fast, fully mock, no side effects — good for iterating while developing/demoing.
# HEXAGENT_ENABLE_NMAP=false is explicit here so this never depends on your .env.
HEXAGENT_ENABLE_NMAP=false uv run hexagent -o "Recon" -t demo.thm.local --mock --no-save --print

# Exercise the real nmap tool deterministically (see docs/nmap-testing.md) —
# --mock here means "no LLM", NOT "no real nmap".
HEXAGENT_ENABLE_NMAP=true uv run hexagent -o "Map the attack surface" -t 127.0.0.1 --mock --print --no-save

# Full "educational" run: mock recon + both human checkpoints
HEXAGENT_ENABLE_NMAP=false uv run hexagent -o "Recon" -t demo.thm.local --mock --human-approval --require-sensitive-approval --print
```

> **macOS troubleshooting:** if `uv run hexagent` raises
> `ModuleNotFoundError: No module named 'app'`, uv's editable-install `.pth`
> file got written with the macOS hidden flag set, which Python 3.12+'s
> `site.py` silently skips. `pyproject.toml` pins `[tool.uv] link-mode =
> "copy"` to reduce how often this happens (uv's default hardlink mode shares
> an inode with a cached `.pth` that can carry the flag), but **this has
> recurred more than once even with that pin** — it is a mitigation, not a
> full fix. The most reliable option is the bundled wrapper, which clears the
> flag before every run automatically (same arguments as `uv run hexagent`):
>
> ```bash
> ./scripts/hexagent.sh --objective "Recon the lab box" --target demo.thm.local --mock --print
> ```
>
> Or fix it manually, one-off:
>
> ```bash
> chflags nohidden .venv/lib/python3.12/site-packages/*.pth   # fixes it until the next uv-triggered relink
> uv run python -m app.cli --objective "Recon the lab box" --target demo.thm.local --mock --print  # or run via the module — unaffected by the .pth either way
> ```

### Programmatic

```python
from app.graph.workflow import run_workflow

state = run_workflow("Passive reconnaissance", "demo.thm.local")
print(state.report_markdown)
```

### Example script

```bash
uv run python examples/basic_recon.py
```

### Web UI

A small Flask front-end lets you launch a run from a form and watch the
whole agent pipeline (plan → execute → evaluate → replan → report) stream
live in the browser — including a real, in-the-moment Approve/Deny prompt
when `--require-sensitive-approval` pauses before `http_post`/`nmap_scan`.

```bash
uv sync --extra web            # installs Flask + markdown/nh3 (kept optional; the CLI stays dependency-light)
uv run hexagent-web            # or: uv run python -m app.web
# then open http://127.0.0.1:5000
```

> **No authentication, local only.** This is an educational dev tool, not a
> hardened service — it binds to `127.0.0.1` by default and has no login.
> Never pass `--host 0.0.0.0` or otherwise expose it to a network.

The launch form exposes the same knobs as the CLI flags (`--mock`,
`--enable-nmap`, `--max-iterations`, `--human-approval`,
`--require-sensitive-approval`); the live console shows one log line per
graph node, findings as they're discovered, and the final report rendered as
formatted HTML (headings, tables, code blocks) with a "view raw markdown"
toggle. The approval pause is real: the background run thread genuinely
blocks until you click Approve or Deny, the same way the CLI's `input()`
prompt does.

**Report rendering is sanitised, not just escaped.** `objective`/`target` and
tool arguments are user-supplied and flow verbatim into the report, so the
server renders the markdown with `python-markdown` and then strips the
resulting HTML down to an explicit tag allow-list with `nh3` (a Rust-backed
HTML sanitiser) before sending it to the browser — a `<script>` in your
objective is dropped from the rendered view entirely (it's still visible,
inert, in the "view raw markdown" toggle). Escaping the source text before
rendering was tried first and rejected: it double-escapes fenced code blocks.

It reuses `build_nodes()`/`build_workflow()` from `app/graph/workflow.py`
directly (driving `.stream()` instead of `.invoke()`) rather than modifying
`run_workflow()`, so the CLI and test suite are unaffected by this feature.

---

## How it works

- **Planner** turns the objective into an ordered `Plan`. Offline it starts
  minimal — just a `port_scan` step plus a synthesis step — and *grows* the plan
  reactively via `replan()` as results come in; with an LLM it requests a
  structured JSON plan upfront and falls back to the heuristic planner on any
  error (whose `replan()` it also reuses).
- **Executor** picks the tool for the next runnable step (honouring step
  dependencies), then dispatches it to whichever specialist owns that tool
  (`ReconAgent` or `HttpAnalysisAgent`). The owning specialist is what actually
  calls the `ToolRegistry` — and gates the call behind human approval first if
  the tool is `sensitive` and `HEXAGENT_REQUIRE_SENSITIVE_APPROVAL` is set.
- **Evaluator** converts the raw `ToolResult` into neutral `Observation`s and
  interpreted `Finding`s, flags items needing human validation, and may request
  a replan with one of three machine-readable `ReplanReason`s: an open web port
  was found (→ queue HTTP-layer analysis), robots.txt disallowed a path (→
  queue a targeted GET), or a login endpoint was discovered (→ queue a
  controlled POST).
- **Reporter** renders the final markdown report. The deterministic renderer
  guarantees the required section structure; an LLM, if present, adds an executive
  summary on top.

### Decision logic example

A single run against a target with 80/443 open plays out like this:

```
port_scan (80, 443 open)
  -> replan: open_web_ports_found
     -> tech_fingerprint, http_header_inspect, security_headers, robots_txt, url_crawler queued
robots_txt (disallows /api/v1/debug)
  -> replan: robots_paths_found -> http_get /api/v1/debug queued
url_crawler (finds /login)
  -> replan: login_endpoint_found -> http_post /login queued (sensitive; gated on approval if enabled)
-> summarise
```

If the port scan finds no web ports, the HTTP-analysis phase is never queued at
all — the plan just completes after the scan.

The `port_scan` step above is the mock by default; with `HEXAGENT_ENABLE_NMAP=true`
the planner uses the real `nmap_scan` in its place automatically, so the same
decision logic runs end to end from a single CLI prompt against a real host
(see [`docs/nmap-testing.md`](docs/nmap-testing.md)):

```bash
HEXAGENT_ENABLE_NMAP=true uv run hexagent \
  -o "Map the attack surface" -t 127.0.0.1 --mock --require-sensitive-approval --print
```

---

## Extensibility

The architecture is intentionally open for extension without modifying the
orchestration layer:

- **Add a real tool** (Nuclei, ffuf, SQLMap, Burp): subclass `BaseTool`,
  implement `_run`, return a structured `ToolResult`, and register it in
  `default_registry()`. Assign it to `ReconAgent` or `HttpAnalysisAgent` (or add
  a new specialist) via `TOOL_NAMES`; mark it `sensitive = True` if it should be
  gated behind approval like `nmap_scan`/`http_post`.
- **More specialist agents**: `app/agents/specialists.py` already splits recon
  from HTTP analysis — add another `SpecialistAgent` subclass (e.g. an
  `AuthAgent` for authenticated flows) and register it in `ExecutorAgent`.
- **Browser automation**: wrap a Playwright session as a tool subclass.
- **Multi-agent workflows**: add new nodes/agents and wire extra edges in
  `app/graph/workflow.py`; the router pattern scales to more destinations.
- **Resumable human-in-the-loop**: today's sensitive-action gate is a
  synchronous callback (blocks the current process; fails closed with no
  callback wired). The `human` checkpoint node remains the seam for a real
  LangGraph `interrupt`/checkpointer if you need approval to survive a process
  restart or come from a separate UI.
- **Memory / RAG**: inject a retriever into the agents and persist
  `reasoning_history` to a vector store; add a checkpointer to `compile()`.

---

## Testing

```bash
uv run pytest            # run the suite (offline, deterministic)
uv run ruff check .      # lint
uv run ruff format .     # format
```

---

## Limitations

This is a proof-of-concept, not a production pentesting tool. Known boundaries:

- **Mock-by-default tooling.** All eight built-in tools return deterministic,
  simulated data derived from a per-target fixture profile — no real HTTP
  requests, port scans, crawling or fingerprinting occur. `nmap_scan` is the
  only real, network-touching tool, and it is opt-in (`HEXAGENT_ENABLE_NMAP`),
  off by default.
- **No exploitation capability.** There is no payload delivery, credential
  testing, injection, or post-exploitation logic of any kind — by design.
- **No authenticated/session-aware testing.** Tools operate statelessly per
  call; there's no cookie jar, login flow, or multi-step session handling.
- **Single target, single run.** No multi-host campaigns, asset inventory, or
  cross-run scope management; each invocation assesses one target in isolation.
- **No persistence across runs.** `AgentState` lives in memory for the
  duration of one `run_workflow()` call; there's no checkpointing, resumption,
  or run history (the `report_dir` only stores the rendered markdown).
- **Heuristic replanning covers three specific triggers, not arbitrary
  strategy change.** Offline, the planner reacts to exactly three
  `ReplanReason`s (open web ports, robots.txt disallow, login endpoint found) —
  a real methodology has far more branches than that. With an LLM, planning and
  tool selection can still fail or hallucinate; failures fall back to the
  heuristic planner silently.
- **The sensitive-action gate is synchronous, not resumable.** Approval is a
  blocking callback inside the current process (the CLI's is a plain
  `input()` prompt); there's no persisted "pending approval" state that
  survives a crash or that a separate UI/reviewer could act on asynchronously.
  Without a callback wired, sensitive actions are simply skipped (fail-closed),
  not queued.
- **Findings are illustrative, not a vulnerability scanner.** Severity grading
  (e.g. the security-headers letter grade) is a simple heuristic for teaching
  purposes, not a calibrated risk assessment, and isn't mapped to any
  standard (OWASP, CVSS, PTES).
- **No rate limiting or scope enforcement on real tools.** `nmap_scan`
  validates input against command-injection but does not restrict *which*
  hosts it may target (e.g. to a private-IP allowlist) — authorisation is the
  operator's responsibility, not something the code enforces. The same is true
  of the `sensitive` flag in general: it marks a tool as needing approval, it
  doesn't limit what that tool is allowed to do once approved.

## Further reading

See [`docs/architecture.md`](docs/architecture.md) for a deeper component
breakdown and design rationale.

## Disclaimer

This project is an educational proof-of-concept built around **simulated** tools.
It is **not** a penetration-testing product and must not be used against systems
without explicit authorisation. Always follow applicable laws and the rules of
engagement for any lab or target.
