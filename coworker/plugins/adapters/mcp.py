"""MCP server adapter — projects an ``MCPServerDef`` into a ``PluginManifest``.

An MCP server exposes remote tools; its capability surface is the server itself.
``requires_approval`` and include/exclude tool lists are the permission knobs.
"""

from __future__ import annotations

from typing import Any

from ...mcp.config import MCPServerDef
from ..manifest import KIND_MCP, PluginManifest


class MCPAdapter:
    kind = KIND_MCP

    def to_manifest(self, native: MCPServerDef) -> PluginManifest:
        return PluginManifest(
            kind=KIND_MCP,
            id=native.name,
            name=native.name,
            capability_set=self.capability_set(native),
            permissions=self.permissions(native),
            description=native.url or native.command or "",
        )

    def capability_set(self, native: MCPServerDef) -> frozenset[str]:
        return frozenset({f"mcp:{native.name}"})

    def permissions(self, native: MCPServerDef) -> dict[str, Any]:
        # Preserve None (= no filter / all tools) — coercing to [] would lose the
        # allowlist-vs-all distinction that permission_diff relies on.
        return {
            "requires_approval": native.requires_approval,
            "include_tools": (
                None if native.include_tools is None else list(native.include_tools)
            ),
            "exclude_tools": (
                None if native.exclude_tools is None else list(native.exclude_tools)
            ),
            "transport": native.transport,
            "auth": native.auth,
        }
