"""HexAgent command-line interface.

Run an educational reconnaissance session against a (mock) target and write a
markdown report. Example:

    uv run hexagent --objective "Recon the lab box" --target demo.thm.local
"""

from __future__ import annotations

import argparse
import sys

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from app.config import get_settings
from app.graph.state import AgentState
from app.graph.workflow import run_workflow
from app.utils.logging import configure_logging
from app.utils.report_io import save_report

console = Console()


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="hexagent", description="Educational agentic web recon assistant (mock tools)."
    )
    parser.add_argument("--objective", "-o", required=True, help="Natural-language goal.")
    parser.add_argument("--target", "-t", required=True, help="Target host or URL (lab only).")
    parser.add_argument("--mock", action="store_true", help="Force deterministic offline mode.")
    parser.add_argument("--max-iterations", type=int, default=None, help="Override iteration cap.")
    parser.add_argument(
        "--human-approval", action="store_true", help="Pause for human approval before report."
    )
    parser.add_argument("--no-save", action="store_true", help="Do not write the report to disk.")
    parser.add_argument("--print", dest="print_report", action="store_true", help="Print report.")
    return parser.parse_args(argv)


def _render_summary(state: AgentState) -> None:
    """Print a concise Rich summary of the run."""
    table = Table(title="Findings", show_lines=False)
    table.add_column("Severity", style="bold")
    table.add_column("Title")
    table.add_column("Human?", justify="center")
    for finding in state.findings:
        table.add_row(
            finding.severity.value.upper(),
            finding.title,
            "✓" if finding.requires_human_validation else "",
        )
    console.print(
        Panel.fit(
            f"[bold]Objective:[/bold] {state.objective}\n"
            f"[bold]Target:[/bold] {state.target}\n"
            f"[bold]Iterations:[/bold] {state.iterations}  "
            f"[bold]Replans:[/bold] {state.replans}\n"
            f"[bold]Stopped:[/bold] {state.stopped_reason}",
            title="HexAgent Run",
        )
    )
    if state.findings:
        console.print(table)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns a process exit code."""
    args = _parse_args(argv)
    settings = get_settings()
    if args.mock:
        settings.mock_mode = True
    if args.max_iterations is not None:
        settings.max_iterations = args.max_iterations
    if args.human_approval:
        settings.require_human_approval = True

    configure_logging(settings.log_level)

    state = run_workflow(args.objective, args.target, settings=settings)
    _render_summary(state)

    markdown = state.report_markdown or "(no report generated)"
    if not args.no_save:
        path = save_report(markdown, args.target, settings)
        console.print(f"[green]Report saved to[/green] {path}")
    if args.print_report:
        console.rule("Report")
        console.print(markdown)
    return 0


if __name__ == "__main__":
    sys.exit(main())
