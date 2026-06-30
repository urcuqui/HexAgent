"""Centralised logging configuration backed by Rich.

Call :func:`configure_logging` once at process start (the CLI does this) and use
:func:`get_logger` everywhere else to obtain a module-scoped logger.
"""

from __future__ import annotations

import logging

from rich.logging import RichHandler

_CONFIGURED = False


def configure_logging(level: str = "INFO") -> None:
    """Install a Rich-formatted handler on the root logger (idempotent).

    Args:
        level: Logging level name, e.g. ``"DEBUG"`` or ``"INFO"``.
    """
    global _CONFIGURED
    if _CONFIGURED:
        logging.getLogger().setLevel(level.upper())
        return

    handler = RichHandler(rich_tracebacks=True, show_path=False, markup=True)
    logging.basicConfig(
        level=level.upper(),
        format="%(message)s",
        datefmt="[%X]",
        handlers=[handler],
    )
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    """Return a named logger, ensuring logging has been configured.

    Args:
        name: Usually ``__name__`` of the calling module.
    """
    if not _CONFIGURED:
        configure_logging()
    return logging.getLogger(name)
