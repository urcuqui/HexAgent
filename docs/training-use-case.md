# Training Use Case & Team Walkthrough

This note connects the HexAgent POC to the original objective — *"Build a proof
of concept for a web application penetration testing agent ... and how it
could support the professional training module"* — and proposes an agenda for
walking the team through it.

## Why this POC fits a training context

HexAgent was built to make agentic reasoning **visible and inspectable**,
which is exactly what a training module needs:

- **The plan is an artifact, not a black box.** `Plan`/`PlanStep` objects show
  *what* the agent intends to do and *why* (`rationale`) before any tool runs.
  Trainees can read a plan and critique it the way they'd critique a junior
  pentester's methodology, before seeing any output.
- **Tool calls are structured and explainable.** Every `ToolResult` carries a
  `summary`, `data`, and `duration_ms` — useful for teaching "what does this
  recon technique actually return and why does it matter," without needing a
  live target or worrying about scope/safety.
- **Replanning demonstrates adaptive methodology.** The `robots.txt → notable
  path found → insert a targeted request` replan is a small, concrete example
  of "react to what you find," which is a core pentesting skill that's hard to
  teach from a static slide.
- **The markdown report is itself training material.** Each run produces a
  structured report (objective, plan, findings, human-validation points,
  suggested next actions) that mirrors the shape of a real engagement
  deliverable — useful as a fill-in-the-blanks or critique exercise.
- **It's safe by default.** Because every tool is a deterministic mock unless
  explicitly opted in (`HEXAGENT_ENABLE_NMAP`), trainees can run it freely
  without a lab network, without legal/scope concerns, and get the same
  illustrative output every time (`build_profile()` is a stable hash of the
  target string).

## What it does *not* yet do (relevant to training scope)

See [`README.md` → Limitations](../README.md#limitations) for the full list.
The ones most relevant to a training program:

- Findings/severity are illustrative, not calibrated — don't present them as
  "this is how real CVSS scoring works."
- There's no authenticated-flow testing, so the training narrative is limited
  to passive/unauthenticated recon unless real tools are added.
- Only one real tool exists (`nmap_scan`); going further (e.g. `nuclei`,
  `ffuf`, a Burp/proxy integration) is the documented extension path in
  [`docs/architecture.md`](architecture.md#extending-toward-real-tooling), not
  something already built.

## Suggested walkthrough agenda (~45 min)

1. **Live demo (10 min)** — run `uv run hexagent -o "..." -t demo.thm.local
   --mock --print` and read the generated report top to bottom.
2. **Architecture (15 min)** — walk the LangGraph diagram in
   [`README.md`](../README.md#graph-workflow): intake → plan → execute →
   evaluate → (replan/human) → report. Show one real agent class (e.g.
   `EvaluatorAgent`) to ground "agent" in actual code.
3. **Limitations & safety model (10 min)** — present the Limitations section
   above; be explicit that this is mock-by-default and that real-tool
   extension (`nmap_scan`) is opt-in and unauthenticated/un-scoped by design,
   so it's the operator's job to enforce authorisation.
4. **Training-module fit — open discussion (10 min)** — decide as a team:
   - Should training exercises stay mock-only, or do we want a sandboxed lab
     network where `nmap_scan` (and future real tools) run against
     intentionally vulnerable VMs?
   - Do we want trainees to *read* generated reports, or also *edit* plans/
     prompts (`app/prompts/templates/*.txt`) as an exercise in steering agent
     behaviour?
   - What's the next real tool to add for the curriculum — `ffuf` (directory
     brute force) and `nuclei` (template-based vuln scanning) are the most
     teachable next additions given the existing `BaseTool` pattern.

## Open questions to resolve with the team

- Who owns scope/authorisation enforcement once real tools beyond `nmap_scan`
  are added — code-level allowlists, or a process/policy control?
- Should the training module track a "maturity ladder" (mock recon → real
  recon → authenticated testing → exploitation) mapped to POC milestones?
