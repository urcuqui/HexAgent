# Testing the Nuclei Tools

`NucleiScanUrlTool` / `NucleiScanUrlsTool` / `NucleiCheckInstallationTool`
(`app/tools/nuclei_tool.py`) shell out to a real `nuclei` binary the same way
`NmapScanTool` shells out to `nmap` — see
[`docs/nmap-testing.md`](nmap-testing.md) for the sibling guide. Every match
is returned as an unverified *candidate*; the planner hands it to the existing
`http_get` tool for confirmation before it's ever reported as validated.

> ⚠️ **Authorisation required.** Only ever point this tool at hosts you own or
> are explicitly authorised to test (localhost, a lab VM, a CTF target). Do not
> scan third-party hosts.

## 0. Prerequisites

- `nuclei` installed and on `PATH`:

  ```bash
  nuclei -version
  ```

  Install via Go:

  ```bash
  go install -v github.com/projectdiscovery/nuclei/v3/cmd/nuclei@latest
  ```

  Or your package manager (e.g. `brew install nuclei` on macOS). HexAgent
  never updates templates automatically — run this yourself when you want the
  latest set:

  ```bash
  nuclei -update-templates
  ```

- Project dependencies synced:

  ```bash
  uv sync --extra dev
  ```

## 1. Run the mocked unit tests (fast, no network, no nuclei required)

This is the default, CI-safe test path. `subprocess.run` and `shutil.which`
are monkeypatched, and nuclei's JSONL output is faked by writing fixture lines
to the `-o` path the tool passes — no real scan happens and the binary doesn't
even need to be installed:

```bash
uv run pytest tests/test_nuclei_tool.py -v
```

Covers (non-exhaustive):

| Test | What it checks |
|---|---|
| `test_nuclei_tools_not_registered_by_default` | the registry stays fully mock unless `enable_nuclei=True` |
| `test_scan_url_rejects_blocked_tag` / `..._unknown_severity` / `..._flag_injection_via_tag` | the allow/block lists and argv-injection guard reject unsafe input before nuclei is ever invoked |
| `test_scan_url_critical_severity_disabled_by_default` | `critical` is refused unless `NUCLEI_ALLOW_CRITICAL=true` |
| `test_scan_url_successful_scan_parses_jsonl` | JSONL output is parsed into `NucleiFinding`s, secrets in `curl_command` are redacted, and the command uses list args (no `shell=True`) |
| `test_scan_url_malformed_jsonl_line_is_skipped_not_fatal` | a corrupt JSONL line doesn't fail the whole scan |
| `test_scan_url_caps_max_results` | `NUCLEI_MAX_RESULTS` truncates findings with a note, not silently |
| `test_scan_url_cleans_up_temp_output_file` | the temporary `-o` file is always removed |
| `test_scan_urls_dedupes_and_filters_out_of_scope` | batch mode dedupes URLs and drops out-of-scope entries into `targets_skipped` instead of crashing |
| `test_scan_urls_enforces_max_targets` | oversized batches are capped, with the overflow recorded as skipped |
| `test_default_profile_call_is_not_sensitive` / `test_high_severity_call_is_sensitive` / `test_custom_templates_call_is_always_sensitive` / `test_raised_rate_limit_call_is_sensitive` / `test_oversized_batch_call_is_sensitive` | the safe-default profile runs unattended; anything escalating is flagged for approval via `is_call_sensitive()` |
| `test_specialist_gates_escalated_call_but_not_safe_default` | `ReconAgent` (the existing approval gate) lets the safe default through and denies an escalated call when no approval callback is wired (fail-closed) |
| `test_explicit_templates_*` | explicit `templates=[...]` require `NUCLEI_TEMPLATES_DIR`, reject path traversal, reject missing files, and reject a template whose metadata declares a blocked tag/`critical` severity |

A final test, `test_real_scan_against_localhost`, is skipped here — see step 3.

## 2. Call the tool directly against localhost (real scan, no LLM/workflow)

```bash
uv run python -c "
from app.tools.nuclei_tool import NucleiScanUrlTool

tool = NucleiScanUrlTool(timeout=60)
result = tool.run(target='127.0.0.1', tags=['tech', 'headers'])
print(result.status)
print(result.summary)
print(result.data['result_count'], 'candidate finding(s)')
for f in result.data['findings']:
    print(' -', f['template_id'], f['severity'], f['matched_at'])
"
```

Try a blocked tag to see the guardrail without ever touching the network:

```bash
uv run python -c "
from app.tools.nuclei_tool import NucleiScanUrlTool
result = NucleiScanUrlTool().run(target='127.0.0.1', tags=['bruteforce'])
print(result.status, result.error)
"
```

Verify the installation diagnostic:

```bash
uv run python -c "
from app.tools.nuclei_tool import check_installation
result = check_installation()
print(result.status, result.summary)
"
```

## 3. Run the opt-in real-scan integration test

`tests/test_nuclei_tool.py::test_real_scan_against_localhost` is skipped by
default to keep the main suite offline and deterministic. Opt in explicitly:

```bash
HEXAGENT_TEST_REAL_NUCLEI=1 uv run pytest tests/test_nuclei_tool.py -v -k real
```

## 4. Use it through the `ToolRegistry`

The Nuclei tools are **not** registered by default — `default_registry()`
stays fully mock unless you opt in, either via the `enable_nuclei` argument or
`HEXAGENT_ENABLE_NUCLEI`:

```bash
uv run python -c "
from app.tools.registry import default_registry

registry = default_registry(enable_nuclei=True)
print(sorted(n for n in registry.names() if n.startswith('nuclei')))
"
```

## 5. Use it inside the full agent workflow — a single CLI prompt

The heuristic planner queues `nuclei_scan_url` alongside the HTTP-analysis
phase whenever `nuclei_scan_url` is registered — see `_on_open_web_ports` in
`app/planners/planner.py`. Combine it with `HEXAGENT_ENABLE_NMAP=true` to
drive the whole graph with real data end to end:

```bash
HEXAGENT_ENABLE_NMAP=true HEXAGENT_ENABLE_NUCLEI=true uv run hexagent \
  -o "Map the attack surface" -t 127.0.0.1 --mock --print --no-save
```

If Nuclei finds a candidate, the report's `## Tool Outputs` section shows the
raw `NucleiScanResult`, and `## Findings` shows the candidate plus its
validation outcome (the planner automatically queues `http_get` against the
matched URL — see the "Nuclei: candidate discovery" section of the top-level
README).

### 5a. Add the sensitive-approval gate to the same prompt

Both Nuclei tools are `sensitive = True`, gated the same way as `nmap_scan`.
Unlike `nmap_scan`, though, the gate is *call-aware*
(`BaseTool.is_call_sensitive()`): a safe-default call runs automatically even
with the gate enabled, and only an escalated call (custom templates, high/
critical severity, a raised rate limit, an oversized batch) is actually paused
for approval.

```bash
# Safe-default profile: runs even with the gate enabled, no prompt.
HEXAGENT_ENABLE_NUCLEI=true uv run hexagent \
  -o "Map the attack surface" -t 127.0.0.1 --mock --require-sensitive-approval --no-save

# Force an escalated call (e.g. via the programmatic API) to see the prompt:
uv run python -c "
from app.tools.registry import default_registry
from app.agents.specialists import ReconAgent
from app.models.tool_io import ToolCall

registry = default_registry(enable_nuclei=True)

gated = ReconAgent(registry, require_sensitive_approval=True)  # no callback -> fail-closed
print('safe default:', gated.run(ToolCall(tool_name='nuclei_scan_url', arguments={'target': '127.0.0.1'})).status)
print('escalated (critical):', gated.run(ToolCall(tool_name='nuclei_scan_url', arguments={'target': '127.0.0.1', 'severity': ['critical']})).status)

approved = ReconAgent(registry, approval_callback=lambda call: True, require_sensitive_approval=True)
print('escalated, approved:', approved.run(ToolCall(tool_name='nuclei_scan_url', arguments={'target': '127.0.0.1', 'severity': ['critical']})).status)
"
```

## 6. Point-to-point: candidate -> validate -> confirm

`tests/test_nuclei_integration.py` exercises the full loop without a live
binary or the LangGraph runtime — useful reading if you want to see exactly
how a Nuclei match becomes a validated (or false-positive) `Finding`:

1. `port_scan` finds 80/443 open -> planner queues the HTTP phase + `nuclei_scan_url`.
2. `nuclei_scan_url` (mocked binary) returns one JSONL match.
3. The evaluator stores it as `Finding(validation_status="candidate")` and
   requests a replan (`ReplanReason.NUCLEI_CANDIDATE_FOUND`).
4. The planner queues `http_get` against the matched URL
   (`HeuristicPlanner._on_nuclei_candidate`).
5. `http_get` (deterministic mock) responds; the evaluator classifies the
   outcome: `200` -> `validated`, `401`/`403`/`404` -> `false_positive`,
   anything else -> `needs_validation`.

```bash
uv run pytest tests/test_nuclei_integration.py -v
```

## 7. Lint and type-check

```bash
uv run ruff check app/tools/nuclei_tool.py tests/test_nuclei_tool.py tests/test_nuclei_integration.py
uv run mypy app/tools/nuclei_tool.py app/models/nuclei.py
```

---

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `'nuclei' not found on PATH` | Install nuclei (see Prerequisites) or check `which nuclei` resolves inside your shell/venv |
| `Refusing to run blocked tag(s): [...]` | A requested tag is in `BLOCKED_TAGS` (bruteforce/fuzz/dos/rce/intrusive/destructive/...) — not permitted regardless of approval |
| `Severity 'critical' is disabled by configuration` | Set `NUCLEI_ALLOW_CRITICAL=true` (still gated behind approval if `HEXAGENT_REQUIRE_SENSITIVE_APPROVAL=true`) |
| `Explicit templates require NUCLEI_TEMPLATES_DIR to be configured` | Set `NUCLEI_TEMPLATES_DIR` to a local template directory before passing `templates=[...]` |
| `Template '...' escapes NUCLEI_TEMPLATES_DIR` | The template path resolved outside the configured directory (path traversal guard) |
| Result status `skipped` from `ReconAgent`/`ExecutorAgent` | The call was sensitive (escalated) and no approval was granted — this is fail-closed by design |
| Scan hangs / times out | Lower `NUCLEI_TIMEOUT_SECONDS` or the per-call `timeout`, or narrow `tags`/`severity` |
| `nuclei exited 1: ...` | Read `result.error` — it includes nuclei's own (redacted) stderr |
