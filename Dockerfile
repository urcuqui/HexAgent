# syntax=docker/dockerfile:1.9
#
# HexAgent container image (educational POC).
#
# Multi-stage build:
#   1. builder  - install all dependencies (incl. dev) using the uv lockfile.
#   2. test     - run ruff + the pytest unit suite; a failure aborts the build.
#   3. runtime  - lean image with prod-only, non-editable install, non-root user.
#
# Build the production image (runs the unit tests as a build gate):
#   docker build -t hexagent:latest .
# Run only the test stage explicitly:
#   docker build --target test -t hexagent:test .
#
# The image holds no secrets. Provide the Vercel AI Gateway key at run time:
#   docker run --rm --env-file .env hexagent:latest -o "Recon" -t demo.thm.local --print
# Or run fully offline (no key needed) using the default mock CMD:
#   docker run --rm hexagent:latest

ARG PYTHON_VERSION=3.12

# --------------------------------------------------------------------------- #
# Base: uv + CPython on a slim Debian layer.
# --------------------------------------------------------------------------- #
FROM ghcr.io/astral-sh/uv:python${PYTHON_VERSION}-bookworm-slim AS base
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
WORKDIR /app

# --------------------------------------------------------------------------- #
# Builder: resolve and install the full dependency set (including dev extras).
# Dependencies are installed before the source is copied so the layer is cached
# and only re-run when uv.lock / pyproject.toml change.
# --------------------------------------------------------------------------- #
FROM base AS builder
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --frozen --no-install-project --extra dev --extra web

COPY . /app
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --extra dev --extra web

# --------------------------------------------------------------------------- #
# Test: run linting and the unit test suite. If anything fails the whole image
# build fails, so a green image is always test-validated.
# --------------------------------------------------------------------------- #
FROM builder AS test
RUN uv run ruff check . \
 && uv run ruff format --check . \
 && uv run pytest -q \
 && touch /tmp/tests.passed

# --------------------------------------------------------------------------- #
# Runtime: prod-only dependencies, non-editable project install (so `app` is a
# real installed package, independent of CWD), running as an unprivileged user.
# --------------------------------------------------------------------------- #
FROM base AS runtime
ENV PATH="/app/.venv/bin:$PATH"

# nmap is a system binary, not a Python dependency: NmapScanTool shells out to
# it (see app/tools/nmap_tool.py). It's only ever *used* if HEXAGENT_ENABLE_NMAP
# is set to true at `docker run` time (off by default), but the binary must be
# present in the image for that opt-in to work at all.
RUN apt-get update \
 && apt-get install -y --no-install-recommends nmap \
 && rm -rf /var/lib/apt/lists/*

RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --frozen --no-install-project --no-dev --extra web

COPY . /app
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --extra web --no-editable

# Gate: force the `test` stage to build (and pass) before the runtime image.
COPY --from=test /tmp/tests.passed /tmp/tests.passed

RUN useradd --create-home --uid 10001 hexagent \
 && mkdir -p /app/reports \
 && chown -R hexagent:hexagent /app
USER hexagent

ENTRYPOINT ["hexagent"]
CMD ["--objective", "Recon the lab box", "--target", "demo.thm.local", "--mock", "--print"]
