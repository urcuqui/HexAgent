"""Mock pentest tools and the tool registry.

All tools are deterministic simulations that return structured Pydantic models.
They never touch the network and never perform real exploitation.
"""

from app.tools.base import BaseTool
from app.tools.registry import ToolRegistry, default_registry

__all__ = ["BaseTool", "ToolRegistry", "default_registry"]
