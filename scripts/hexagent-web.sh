#!/usr/bin/env bash
# Wrapper around the HexAgent web UI for macOS + uv environments.
#
# uv marks .venv with the BSD UF_HIDDEN flag; Python 3.12 site.py silently
# skips hidden .pth files, breaking the `hexagent-web` console script with
# ModuleNotFoundError: No module named 'app'.
#
# Fix: run via `python -m app.web` from the project root. The -m flag makes
# Python insert the CWD into sys.path[0] before site processing runs, so
# `app` is importable regardless of whether the .pth file was processed.
# `--directory` ensures CWD = project root no matter where this script is
# called from. The `web` and `browser` extras are requested explicitly so a
# clean uv environment has Flask/nh3/Playwright available for the UI.
#
# Usage: identical to `uv run hexagent-web`, e.g.:
#   ./scripts/hexagent-web.sh --port 8080 --debug
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

exec uv run --directory "$repo_root" --extra web --extra browser python -m app.web "$@"
