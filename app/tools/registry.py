"""Tool registry.

A small dependency-injection container mapping tool names to instances. Agents
receive a registry rather than importing tools directly, so the available
toolset can be customised per run (and mocked in tests).
"""

from __future__ import annotations

from app.models.tool_io import ToolResult
from app.tools.base import BaseTool
from app.utils.logging import get_logger

logger = get_logger(__name__)


class ToolRegistry:
    """Holds the set of tools available to a workflow run."""

    def __init__(self, tools: list[BaseTool] | None = None) -> None:
        self._tools: dict[str, BaseTool] = {}
        for tool in tools or []:
            self.register(tool)

    def register(self, tool: BaseTool) -> None:
        """Add a tool, raising on duplicate names."""
        if tool.name in self._tools:
            raise ValueError(f"Tool '{tool.name}' is already registered")
        self._tools[tool.name] = tool

    def get(self, name: str) -> BaseTool | None:
        """Return the tool registered under ``name`` (or ``None``)."""
        return self._tools.get(name)

    def names(self) -> list[str]:
        """Return all registered tool names."""
        return list(self._tools)

    def all(self) -> list[BaseTool]:
        """Return all registered tool instances."""
        return list(self._tools.values())

    def catalogue(self) -> str:
        """Render the human/LLM-readable catalogue of all tools."""
        return "\n".join(tool.catalogue_entry() for tool in self._tools.values())

    def run(self, name: str, **kwargs: object) -> ToolResult:
        """Look up and execute a tool by name.

        Returns an error :class:`ToolResult` if the tool is unknown.
        """
        tool = self.get(name)
        if tool is None:
            logger.warning("Requested unknown tool '%s'", name)
            return ToolResult.fail(name, f"Unknown tool '{name}'")
        return tool.run(**kwargs)


def default_registry(enable_nmap: bool = False) -> ToolRegistry:
    """Build a registry pre-populated with all built-in mock tools.

    Args:
        enable_nmap: When ``True``, also register :class:`NmapScanTool`, which
            shells out to a real ``nmap`` binary. Off by default so the
            standard registry stays fully mock/offline; wire it to
            ``Settings.enable_nmap`` to opt in.
    """
    # Imported here to avoid a circular import at module load time.
    from app.tools.http_tools import (
        HeaderInspectionTool,
        HttpGetTool,
        HttpPostTool,
    )
    from app.tools.network_tools import PortScanTool
    from app.tools.recon_tools import (
        CrawlerTool,
        RobotsTxtTool,
        SecurityHeadersTool,
        TechFingerprintTool,
    )

    tools: list[BaseTool] = [
        HttpGetTool(),
        HttpPostTool(),
        HeaderInspectionTool(),
        RobotsTxtTool(),
        SecurityHeadersTool(),
        TechFingerprintTool(),
        PortScanTool(),
        CrawlerTool(),
    ]
    if enable_nmap:
        from app.tools.nmap_tool import NmapScanTool

        tools.append(NmapScanTool())
    return ToolRegistry(tools)
