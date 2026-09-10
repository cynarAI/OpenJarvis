"""Network + tailnet device discovery tool.

Talks to the local device-discovery service (see /srv/device-discovery on
evo, port 1245) which combines:
  - Tailscale peers (tailscale status --json)
  - A local-LAN nmap sweep

so the agent can answer "which devices are online" / "what's on the network"
/ "what's on the tailnet" without shelling out itself.
"""

from __future__ import annotations

import json
import urllib.request
from typing import Any

from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.tools._stubs import BaseTool, ToolSpec

_DISCOVERY_URL = "http://127.0.0.1:1245/devices"


def _fetch_devices() -> dict:
    with urllib.request.urlopen(_DISCOVERY_URL, timeout=15) as resp:
        return json.loads(resp.read().decode())


def _format_devices(data: dict, scope: str) -> str:
    lines: list[str] = []

    if scope in ("all", "tailnet"):
        lines.append("Tailnet devices:")
        for p in data.get("tailnet", []):
            if "error" in p:
                lines.append(f"  (tailnet lookup failed: {p['error']})")
                continue
            status = "online" if p.get("online") else "offline"
            marker = " (this machine)" if p.get("self") else ""
            ips = ", ".join(p.get("tailscale_ips", []))
            lines.append(
                f"  - {p.get('name', '?')}{marker}: {status}, {p.get('os', '?')}, {ips}"
            )

    if scope in ("all", "lan"):
        lines.append(f"Local network devices ({data.get('lan_cidr', '?')}):")
        for d in data.get("lan", []):
            if "error" in d:
                lines.append(f"  (LAN scan failed: {d['error']})")
                continue
            name = d.get("hostname") or d.get("vendor") or "unknown device"
            vendor = (
                f" [{d['vendor']}]" if d.get("vendor") and d.get("hostname") else ""
            )
            mac = f" ({d['mac']})" if d.get("mac") else ""
            lines.append(f"  - {d.get('ip', '?')}: {name}{vendor}{mac}")

    return "\n".join(lines) if lines else "No devices found."


@ToolRegistry.register("network_devices")
class NetworkDevicesTool(BaseTool):
    """Lists devices on the local network and/or the Tailscale tailnet."""

    tool_id = "network_devices"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="network_devices",
            description=(
                "List devices currently visible on the local network (LAN) "
                "and/or the Tailscale tailnet, with hostname, IP, vendor/OS "
                "and online status. Use this when asked what devices, "
                "computers, or machines are on the network or tailnet."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "scope": {
                        "type": "string",
                        "enum": ["all", "lan", "tailnet"],
                        "description": (
                            "Which set of devices to list: 'lan' for the "
                            "local network only, 'tailnet' for Tailscale "
                            "peers only, or 'all' (default) for both."
                        ),
                    },
                },
                "required": [],
            },
            category="system",
        )

    def execute(self, **params: Any) -> ToolResult:
        scope = params.get("scope") or "all"
        if scope not in ("all", "lan", "tailnet"):
            scope = "all"
        try:
            data = _fetch_devices()
        except Exception as exc:
            return ToolResult(
                tool_name="network_devices",
                content=(
                    "Device discovery service is unreachable "
                    f"(http://127.0.0.1:1245): {exc}. "
                    "It may not be running on this host."
                ),
                success=False,
            )
        return ToolResult(
            tool_name="network_devices",
            content=_format_devices(data, scope),
            success=True,
        )


__all__ = ["NetworkDevicesTool"]
