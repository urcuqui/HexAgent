"""Minimal programmatic example of running the HexAgent workflow.

Run with:

    uv run python examples/basic_recon.py

This runs fully offline (deterministic mock tools) and prints the generated
markdown report to stdout without writing any files.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Allow running this script directly (``python examples/basic_recon.py``) by
# ensuring the project root is importable regardless of the current directory.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from app.graph.workflow import run_workflow  # noqa: E402
from app.utils.logging import configure_logging  # noqa: E402


def main() -> None:
    """Execute a single recon session and print the report."""
    configure_logging("INFO")
    settings = get_settings()
    settings.mock_mode = True  # ensure offline determinism for the example

    state = run_workflow(
        objective="Perform passive reconnaissance and summarise the attack surface",
        target="demo.thm.local",
        settings=settings,
    )

    print("\n" + "=" * 80)
    print(f"Stopped: {state.stopped_reason} | iterations: {state.iterations}")
    print("=" * 80 + "\n")
    print(state.report_markdown)


if __name__ == "__main__":
    main()
