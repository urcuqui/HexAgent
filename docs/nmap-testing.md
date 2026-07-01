# Testing the Nmap Tool

`NmapScanTool` (`app/tools/nmap_tool.py`) is the one tool in HexAgent that is
**not** a deterministic mock — it shells out to a real `nmap` binary and scans
a real host. This guide walks through every level of testing it, from fast
mocked unit tests to an actual scan against `127.0.0.1`.

> ⚠️ **Authorisation required.** Only ever point this tool at hosts you own or
> are explicitly authorised to test (localhost, a lab VM, a CTF target). Do not
> scan third-party hosts.

## 0. Prerequisites

- `nmap` installed and on `PATH`:

  ```bash
  nmap --version
  ```

  On macOS: `brew install nmap`. On Debian/Ubuntu: `sudo apt install nmap`.

- Project dependencies synced:

  ```bash
  uv sync --extra dev
  ```

## 1. Run the mocked unit tests (fast, no network, no nmap required)

This is the default, CI-safe test path. `subprocess.run` and `shutil.which`
are monkeypatched, so no real scan happens and the binary doesn't even need to
be installed:

```bash
uv run pytest tests/test_nmap_tool.py -v
```

You should see 8 passed tests covering:

| Test | What it checks |
|---|---|
| `test_rejects_unsafe_target` | a target like `--script=vuln` is rejected before `nmap` is ever invoked |
| `test_rejects_invalid_ports` | a malformed `ports` argument (e.g. `"80; rm -rf /"`) is rejected |
| `test_missing_binary_returns_error` | a clean error when `nmap` isn't on `PATH` |
| `test_successful_scan_parses_open_ports` | XML output is parsed into structured port/service data |
| `test_ports_argument_overrides_top_ports` | `ports="22,80"` takes precedence over `top_ports` |
| `test_top_ports_is_clamped` | `top_ports` is capped at 1000 even if a larger value is requested |
| `test_nonzero_exit_returns_error` | a non-zero `nmap` exit code becomes a failed `ToolResult` |
| `test_timeout_returns_error` | a hung scan is killed and reported as a timeout error |

A ninth test, `test_real_scan_against_localhost`, is skipped here — see step 3.

## 2. Call the tool directly against localhost (real scan, no LLM/workflow)

The quickest way to see a *real* scan run is to instantiate the tool directly
in a Python shell:

```bash
uv run python -c "
from app.tools.nmap_tool import NmapScanTool

tool = NmapScanTool(timeout=30)
result = tool.run(target='127.0.0.1', top_ports=20)
print(result.status)
print(result.summary)
print(result.data)
"
```

Expected output: a `success` status, a one-line summary like `127.0.0.1: N
open port(s) of 20 scanned` (N depends on what's actually listening on your
machine — `0` is a perfectly valid result), and a `data` dict with the
executed `command`, the full `ports` list and the filtered `open_ports`.

Try a deliberately invalid target to see the guardrails kick in without ever
touching the network:

```bash
uv run python -c "
from app.tools.nmap_tool import NmapScanTool
result = NmapScanTool().run(target='--script=vuln')
print(result.status, result.error)
"
```

## 3. Run the opt-in real-scan integration test

`tests/test_nmap_tool.py::test_real_scan_against_localhost` is skipped by
default to keep the main suite offline and deterministic. Opt in explicitly:

```bash
HEXAGENT_TEST_REAL_NMAP=1 uv run pytest tests/test_nmap_tool.py -v -k real
```

This runs an actual `nmap -Pn -sT` scan against `127.0.0.1` on a narrow port
range and asserts the result is a successful `ToolResult`.

## 4. Use it through the `ToolRegistry`

`NmapScanTool` is **not** registered by default — `default_registry()` stays
fully mock unless you opt in, either via the `enable_nmap` argument or the
`HEXAGENT_ENABLE_NMAP` setting:

```bash
uv run python -c "
from app.tools.registry import default_registry

registry = default_registry(enable_nmap=True)
print('nmap_scan' in registry.names())  # True

result = registry.run('nmap_scan', target='127.0.0.1', ports='22,80,443')
print(result.summary)
print(result.data['open_ports'])
"
```

## 5. Use it inside the full agent workflow — a single CLI prompt

The heuristic (offline) planner's initial scan step prefers the real
`nmap_scan` over the mock `port_scan` automatically whenever `nmap_scan` is
registered (`HEXAGENT_ENABLE_NMAP=true`) — see `_SCAN_TOOL_PREFERENCE` in
`app/planners/planner.py`. This means a single, ordinary CLI invocation drives
the *entire* graph (plan → execute → evaluate → replan → report) with a real
scan, no LLM and no custom scripting required — this is the "prompt directo al
primer agente" way to test it end to end:

```bash
HEXAGENT_ENABLE_NMAP=true uv run hexagent \
  -o "Map the attack surface" -t 127.0.0.1 --mock --print --no-save
```

`--mock` keeps planning/evaluation on the deterministic heuristic path (no LLM
call), while `HEXAGENT_ENABLE_NMAP=true` still lets the real `nmap_scan` run
as step 1. Inspect the printed report: `## Executed Steps` shows `nmap_scan`
first, and if it found 80/443 open, replans queue the HTTP-analysis phase
exactly as with the mock — except now driven by real scan data instead of the
fixture.

### 5a. Add the sensitive-approval gate to the same prompt

Since `nmap_scan` is `sensitive`, layering `--require-sensitive-approval` onto
the exact same command pauses *before* that first real scan runs:

```bash
# Deny it — the scan never runs, the plan still completes.
echo "n" | HEXAGENT_ENABLE_NMAP=true uv run hexagent \
  -o "Map the attack surface" -t 127.0.0.1 --mock --require-sensitive-approval --no-save

# Approve it — nmap runs for real.
echo "y" | HEXAGENT_ENABLE_NMAP=true uv run hexagent \
  -o "Map the attack surface" -t 127.0.0.1 --mock --require-sensitive-approval --no-save
```

Drop the `echo | ` prefix to answer the `Approve this action? [y/N]:` prompt
interactively in a real terminal.

> If `uv run hexagent` raises `ModuleNotFoundError: No module named 'app'`,
> see the macOS troubleshooting note in the top-level `README.md` — swap in
> `uv run python -m app.cli` (same arguments) as a workaround.

### 5b. With an LLM instead of `--mock`

Without `--mock` (and `AI_GATEWAY_API_KEY` set), the LLM planner/evaluator
take over instead of the deterministic heuristic path — useful to see the
model reason about the same real scan data, but no longer bit-for-bit
reproducible run to run.

## 6. Point-to-point: ReconAgent -> nmap_scan -> approval gate

`nmap_scan` is marked `sensitive = True`, so it's owned by `ReconAgent`
(`app/agents/specialists.py`) and gated the same way `http_post` is: if
`require_sensitive_approval` is on, the specialist consults an
`approval_callback` *before* calling the registry; no callback means denied
(fail-closed).

### 6a. `ReconAgent` directly, gate on/off

```bash
uv run python -c "
from app.tools.registry import default_registry
from app.agents.specialists import ReconAgent
from app.models.tool_io import ToolCall

registry = default_registry(enable_nmap=True)
recon = ReconAgent(registry)
print('owns nmap_scan:', recon.owns('nmap_scan'))

# No gate: runs for real.
print(recon.run(ToolCall(tool_name='nmap_scan', arguments={'target': '127.0.0.1', 'top_ports': 20})).status)

# Gate on, no callback -> denied (fail-closed), not blocked.
gated = ReconAgent(registry, require_sensitive_approval=True)
print(gated.run(ToolCall(tool_name='nmap_scan', arguments={'target': '127.0.0.1'})).status)

# Gate on, callback approves -> runs for real.
approved = ReconAgent(registry, approval_callback=lambda call: True, require_sensitive_approval=True)
print(approved.run(ToolCall(tool_name='nmap_scan', arguments={'target': '127.0.0.1', 'top_ports': 20})).status)
"
```

### 6b. Through `ExecutorAgent` (select -> dispatch -> specialist)

```bash
uv run python -c "
from app.tools.registry import default_registry
from app.agents.executor import ExecutorAgent
from app.models.plan import PlanStep

registry = default_registry(enable_nmap=True)
step = PlanStep(id='s1', description='nmap scan', tool_name='nmap_scan', arguments={'top_ports': 20})

denied = ExecutorAgent(registry, require_sensitive_approval=True, approval_callback=lambda c: False)
print('denied:', denied.execute(step, target='127.0.0.1', observations=[]).status)

approved = ExecutorAgent(registry, require_sensitive_approval=True, approval_callback=lambda c: True)
print('approved:', approved.execute(step, target='127.0.0.1', observations=[]).status)
"
```

### 6c. Full LangGraph pipeline with nmap as the scan step

This no longer needs custom scripting — see **§5, "Use it inside the full
agent workflow"** above: because the planner prefers `nmap_scan` automatically
once it's registered, a plain `uv run hexagent ... --mock` (optionally with
`--require-sensitive-approval`) already drives the entire graph — plan →
execute → evaluate → replan → report — with the real tool.

On a host with nothing listening on 80/443 (e.g. plain `127.0.0.1`), that run
correctly executes only `nmap_scan` and stops (`Replans: 0`) because the
`open_web_ports_found` decision genuinely found none, using real scan data
instead of the mock fixture. Denying the approval prompt resolves the nmap
step to a `SKIPPED` tool result, a "Sensitive action skipped" finding, and the
run still completes normally (this exercises the `completed_ids()`
terminal-state fix — without it, a skipped/failed prerequisite would
permanently block the synthesis step).

## 7. Lint and type-check

```bash
uv run ruff check app/tools/nmap_tool.py tests/test_nmap_tool.py
```

---

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `'nmap' not found on PATH` | Install nmap or check `which nmap` resolves inside your shell/venv |
| `Refusing to scan invalid/unsafe target` | The host string failed the safety regex (e.g. contains a leading `-`) |
| `Invalid ports spec` | `ports` must look like `80`, `22,80,443`, or `1-1000` |
| Scan hangs / times out | A firewalled or unreachable host with `-Pn` will still try every requested port; lower `top_ports` or pass a narrow `ports` range |
| `nmap exited 1: ...` | Read `result.error` — it includes nmap's own stderr (e.g. permission or DNS resolution issues) |
