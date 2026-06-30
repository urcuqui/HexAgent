"""Loads prompt templates from standalone text files.

Prompts live as ``*.txt`` files in the ``templates`` subdirectory so that prompt
engineering is fully decoupled from Python code. Templates use ``str.format``
style ``{placeholders}`` and are read lazily and cached.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

_TEMPLATE_DIR = Path(__file__).parent / "templates"


@lru_cache
def load_prompt(name: str) -> str:
    """Return the raw text of the prompt named ``name`` (without extension).

    Args:
        name: File stem, e.g. ``"planner"`` -> ``templates/planner.txt``.

    Raises:
        FileNotFoundError: If the corresponding template does not exist.
    """
    path = _TEMPLATE_DIR / f"{name}.txt"
    if not path.is_file():
        raise FileNotFoundError(f"Prompt template not found: {path}")
    return path.read_text(encoding="utf-8").strip()


class PromptLibrary:
    """Typed accessors for the project's named prompts.

    Centralising the names here gives a single source of truth and makes missing
    templates fail fast at construction rather than deep inside the graph.
    """

    PLANNER = "planner"
    EXECUTOR = "executor"
    EVALUATOR = "evaluator"
    REPORT_WRITER = "report_writer"

    def __init__(self) -> None:
        # Eagerly validate that every declared prompt exists.
        for name in (self.PLANNER, self.EXECUTOR, self.EVALUATOR, self.REPORT_WRITER):
            load_prompt(name)

    @property
    def planner(self) -> str:
        """System prompt for the planner agent."""
        return load_prompt(self.PLANNER)

    @property
    def executor(self) -> str:
        """System prompt for the executor agent."""
        return load_prompt(self.EXECUTOR)

    @property
    def evaluator(self) -> str:
        """System prompt for the evaluator agent."""
        return load_prompt(self.EVALUATOR)

    @property
    def report_writer(self) -> str:
        """System prompt for the report-writer agent."""
        return load_prompt(self.REPORT_WRITER)
