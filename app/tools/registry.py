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


def default_registry(
    enable_nmap: bool = False,
    enable_playwright: bool = False,
    settings: object | None = None,
) -> ToolRegistry:
    """Build a registry pre-populated with all built-in mock tools.

    Args:
        enable_nmap: When ``True``, also register :class:`NmapScanTool`, which
            shells out to a real ``nmap`` binary. Off by default so the
            standard registry stays fully mock/offline; wire it to
            ``Settings.enable_nmap`` to opt in.
        enable_playwright: When ``True``, register the six Playwright browser
            tools. Playwright must be installed (``pip install playwright &&
            playwright install chromium``). Off by default.
        settings: Optional :class:`~app.config.Settings` instance used to
            configure the Playwright browser tools (headless, timeouts, …).
            Ignored when ``enable_playwright`` is ``False``.
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

    if enable_playwright:
        from pathlib import Path

        from app.tools.browser_tools import build_browser_tools

        headless = True
        timeout_ms = 15_000
        max_requests = 200
        max_body_preview_bytes = 2_000
        screenshot_dir: Path | None = None

        if settings is not None:
            headless = getattr(settings, "playwright_headless", headless)
            timeout_ms = getattr(settings, "playwright_timeout_ms", timeout_ms)
            max_requests = getattr(settings, "playwright_max_requests", max_requests)
            max_body_preview_bytes = getattr(
                settings, "playwright_max_body_preview_bytes", max_body_preview_bytes
            )
            report_dir = getattr(settings, "report_dir", None)
            if report_dir is not None:
                screenshot_dir = Path(report_dir) / "screenshots"

        tools.extend(
            build_browser_tools(
                headless=headless,
                timeout_ms=timeout_ms,
                max_requests=max_requests,
                max_body_preview_bytes=max_body_preview_bytes,
                screenshot_dir=screenshot_dir,
            )
        )

    return ToolRegistry(tools)
