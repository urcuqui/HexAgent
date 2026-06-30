"""Prompt loading utilities.

Prompt text lives in standalone ``*.txt`` files within this package so that
prompt engineering is decoupled from agent code (no hardcoded prompts).
"""

from app.prompts.loader import PromptLibrary, load_prompt

__all__ = ["PromptLibrary", "load_prompt"]
