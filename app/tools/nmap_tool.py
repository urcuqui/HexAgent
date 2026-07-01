"""Real Nmap wrapper.

Unlike the rest of ``app/tools``, this tool shells out to the locally installed
``nmap`` binary and performs an actual TCP scan. It is registered only when
explicitly enabled (``HEXAGENT_ENABLE_NMAP=true``) so the default registry stays
fully mock/offline. Use only against hosts you are authorised to test.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import xml.etree.ElementTree as ET
from typing import Any

from app.models.tool_io import ToolResult
from app.tools.base import BaseTool
from app.tools.fixtures import normalise_target
from app.utils.logging import get_logger

logger = get_logger(__name__)

# Conservative charset for a hostname/IPv4/IPv6 literal. Rejecting anything else
# (notably a leading '-') stops a crafted target from being parsed as an extra
# nmap flag.
_HOST_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9.\-:%]*[A-Za-z0-9])?$")
_PORTS_RE = re.compile(r"^[0-9]+(-[0-9]+)?(,[0-9]+(-[0-9]+)?)*$")

_MAX_TOP_PORTS = 1000


class NmapScanTool(BaseTool):
    """Run a real, unprivileged TCP connect scan via the local ``nmap`` binary."""

    name = "nmap_scan"
    description = (
        "Real TCP connect scan (nmap -sT) against an explicitly authorised host. "
        "Requires the nmap binary on PATH."
    )
    sensitive = True
    argument_help = {
        "target": "host, IP or URL to scan (lab/authorised targets only)",
        "ports": "port spec, e.g. '22,80,443' or '1-1000' (overrides top_ports)",
        "top_ports": f"top ports to scan when 'ports' omitted, default 100, max {_MAX_TOP_PORTS}",
    }

    def __init__(self, binary: str = "nmap", timeout: float = 120.0) -> None:
        self._binary = binary
        self._timeout = timeout

    def _run(
        self, target: str, ports: str | None = None, top_ports: int = 100, **_: Any
    ) -> ToolResult:
        _base_url, host = normalise_target(target)
        if not _HOST_RE.match(host):
            return ToolResult.fail(self.name, f"Refusing to scan invalid/unsafe target {host!r}")

        binary_path = shutil.which(self._binary)
        if binary_path is None:
            return ToolResult.fail(
                self.name, f"'{self._binary}' not found on PATH; install nmap or use port_scan"
            )

        command = [binary_path, "-Pn", "-sT", "-oX", "-"]
        if ports:
            if not _PORTS_RE.match(ports):
                return ToolResult.fail(self.name, f"Invalid ports spec {ports!r}")
            command += ["-p", ports]
        else:
            clamped = max(1, min(int(top_ports), _MAX_TOP_PORTS))
            command += ["--top-ports", str(clamped)]
        command.append(host)

        logger.info("Executing: %s", " ".join(command))
        try:
            proc = subprocess.run(
                command, capture_output=True, text=True, timeout=self._timeout, check=False
            )
        except subprocess.TimeoutExpired:
            return ToolResult.fail(
                self.name, f"nmap scan of {host} timed out after {self._timeout}s"
            )
        except OSError as exc:
            return ToolResult.fail(self.name, f"Failed to execute nmap: {exc}")

        if proc.returncode != 0:
            return ToolResult.fail(
                self.name, f"nmap exited {proc.returncode}: {proc.stderr.strip()}"
            )

        ports_found = self._parse_ports(proc.stdout)
        open_ports = [p for p in ports_found if p["state"] == "open"]
        data = {
            "host": host,
            "command": " ".join(command),
            "ports": ports_found,
            "open_ports": open_ports,
        }
        summary = (
            f"{host}: {len(open_ports)} open port(s) of {len(ports_found)} scanned"
            if ports_found
            else f"{host}: no port data returned"
        )
        return ToolResult.ok(self.name, summary, data)

    @staticmethod
    def _parse_ports(xml_output: str) -> list[dict[str, Any]]:
        try:
            root = ET.fromstring(xml_output)
        except ET.ParseError:
            return []

        results: list[dict[str, Any]] = []
        for host_el in root.findall("host"):
            ports_el = host_el.find("ports")
            if ports_el is None:
                continue
            for port_el in ports_el.findall("port"):
                state_el = port_el.find("state")
                service_el = port_el.find("service")
                results.append(
                    {
                        "port": int(port_el.attrib["portid"]),
                        "protocol": port_el.attrib.get("protocol", "tcp"),
                        "state": state_el.attrib.get("state", "unknown")
                        if state_el is not None
                        else "unknown",
                        "service": service_el.attrib.get("name")
                        if service_el is not None
                        else None,
                        "product": service_el.attrib.get("product")
                        if service_el is not None
                        else None,
                        "version": service_el.attrib.get("version")
                        if service_el is not None
                        else None,
                    }
                )
            # Nmap collapses runs of same-state ports into <extraports> (e.g. "all
            # 50 scanned ports are closed") instead of listing each individually.
            # Without this, an all-closed/all-filtered scan would look like it
            # returned no data at all.
            for extra_el in ports_el.findall("extraports"):
                state = extra_el.attrib.get("state", "unknown")
                reasons_el = extra_el.find("extrareasons")
                spec = reasons_el.attrib.get("ports", "") if reasons_el is not None else ""
                for port in NmapScanTool._expand_port_spec(spec):
                    results.append(
                        {
                            "port": port,
                            "protocol": "tcp",
                            "state": state,
                            "service": None,
                            "product": None,
                            "version": None,
                        }
                    )
        return results

    @staticmethod
    def _expand_port_spec(spec: str) -> list[int]:
        """Expand a comma/range port spec (e.g. "21-23,25,80") into ints."""
        ports: list[int] = []
        for chunk in spec.split(","):
            chunk = chunk.strip()
            if not chunk:
                continue
            try:
                if "-" in chunk:
                    start, end = chunk.split("-", 1)
                    ports.extend(range(int(start), int(end) + 1))
                else:
                    ports.append(int(chunk))
            except ValueError:
                continue
        return ports
