"""Top-level launcher for HexAgent.

Delegates to :func:`app.cli.main` so the project can be run either as
``uv run python main.py ...`` or via the installed ``hexagent`` console script.
"""

from __future__ import annotations

import sys

from app.cli import main

if __name__ == "__main__":
    sys.exit(main())
