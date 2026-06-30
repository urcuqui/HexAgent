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

## 5. Use it inside the full agent workflow

The heuristic (offline) planner follows a fixed recon recipe and never picks
`nmap_scan` on its own, so end-to-end testing requires either an LLM planner
that decides to use it, or manually building a `Plan` step that names it.

### 5a. Enable it for a real run via `.env`

```bash
# .env
HEXAGENT_ENABLE_NMAP=true
```

With an LLM configured (`AI_GATEWAY_API_KEY` set, `HEXAGENT_MOCK_MODE=false`),
`nmap_scan` will appear in the tool catalogue the planner prompt receives, and
the LLM may choose to call it for network-recon objectives. Run as usual:

```bash
uv run hexagent --objective "Scan the lab host for open ports" --target 127.0.0.1 --print
```

### 5b. Force a single nmap step without an LLM

To exercise the executor/evaluator path deterministically (no LLM needed),
build a one-step plan by hand:

```bash
uv run python -c "
from app.tools.registry import default_registry
from app.agents.executor import ExecutorAgent
from app.models.plan import PlanStep

registry = default_registry(enable_nmap=True)
executor = ExecutorAgent(registry, llm=None)  # llm=None -> heuristic, step-declared tool

step = PlanStep(id='s1', description='nmap scan', tool_name='nmap_scan',
                arguments={'top_ports': 50})
result = executor.execute(step, target='127.0.0.1', observations=[])
print(result.status, result.summary)
"
```

## 6. Lint and type-check

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
