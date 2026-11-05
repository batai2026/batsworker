"""Connector plugin adapter — projects a marketplace plugin dict into a ``PluginManifest``.

Community connector plugins are JSON-serializable ``ConnectorDescriptor``-like data
(name/title/auth/fields/instructions/mcp_url). They expose a credential UI; the actual tool
surface comes from a backed MCP server or pinned tool defs. The capability is the connector
itself.
"""

from __future__ import annotations

from typing import Any

from ..manifest import KIND_CONNECTOR, PluginManifest


class ConnectorPluginAdapter:
    kind = KIND_CONNECTOR

    def to_manifest(self, native: dict[str, Any]) -> PluginManifest:
        return PluginManifest(
            kind=KIND_CONNECTOR,
            id=str(native.get("name") or ""),
            name=str(native.get("title") or native.get("name") or ""),
            version=str(native.get("version", "")),
            capability_set=self.capability_set(native),
            permissions=self.permissions(native),
            description=str(native.get("blurb", "")),
        )

    def capability_set(self, native: dict[str, Any]) -> frozenset[str]:
        name = str(native.get("name") or "")
        return frozenset({f"connector:{name}"}) if name else frozenset()

    def permissions(self, native: dict[str, Any]) -> dict[str, Any]:
        return {
            "auth": native.get("auth", "token"),
            "two_way": bool(native.get("two_way", False)),
            "mcp_backed": bool(native.get("mcp_url")),
        }
