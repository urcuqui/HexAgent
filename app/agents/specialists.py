"""Domain-specialist agents: Recon and HTTP analysis.

Rather than one generic executor running every tool identically, each tool
belongs to a specialist scoped to its domain. Both specialists share the same
gating behaviour: a tool marked ``sensitive`` (e.g. a real scan or a POST) is
only run after an approval callback grants it, so "pause before any sensitive
action" is enforced in one place regardless of which specialist owns the tool.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import ClassVar

from app.models.tool_io import ToolCall, ToolResult
from app.tools.registry import ToolRegistry
from app.utils.logging import get_logger

logger = get_logger(__name__)

ApprovalCallback = Callable[[ToolCall], bool]


class SpecialistAgent:
    """Runs tools from a scoped domain, gating sensitive ones on approval."""

    TOOL_NAMES: ClassVar[frozenset[str]] = frozenset()

    def __init__(
        self,
        registry: ToolRegistry,
        approval_callback: ApprovalCallback | None = None,
        require_sensitive_approval: bool = False,
    ) -> None:
        self._registry = registry
        self._approval_callback = approval_callback
        self._require_sensitive_approval = require_sensitive_approval

    def owns(self, tool_name: str) -> bool:
        """True if this specialist is responsible for ``tool_name``."""
        return tool_name in self.TOOL_NAMES

    def run(self, call: ToolCall) -> ToolResult:
        """Run ``call``, pausing for approval first if the tool is sensitive."""
        tool = self._registry.get(call.tool_name)
        if tool is None:
            return ToolResult.fail(call.tool_name, f"Unknown tool '{call.tool_name}'")

        if self._require_sensitive_approval and tool.sensitive and not self._approved(call):
            logger.info("Sensitive action '%s' not approved; skipping", call.tool_name)
            return ToolResult.skipped(
                call.tool_name, "requires human approval, which was not granted"
            )

        return self._registry.run(call.tool_name, **call.arguments)

    def _approved(self, call: ToolCall) -> bool:
        if self._approval_callback is None:
            logger.warning(
                "Sensitive action '%s' requires approval but no approval_callback is "
                "configured; denying by default (fail-closed)",
                call.tool_name,
            )
            return False
        return bool(self._approval_callback(call))


class ReconAgent(SpecialistAgent):
    """Owns reconnaissance tools: port/service discovery and passive recon."""

    TOOL_NAMES: ClassVar[frozenset[str]] = frozenset(
        {
            "port_scan",
            "nmap_scan",
            "tech_fingerprint",
            "robots_txt",
            "url_crawler",
            "security_headers",
        }
    )


class HttpAnalysisAgent(SpecialistAgent):
    """Owns direct HTTP interaction tools: requests and header analysis."""

    TOOL_NAMES: ClassVar[frozenset[str]] = frozenset(
        {"http_get", "http_post", "http_header_inspect"}
    )
