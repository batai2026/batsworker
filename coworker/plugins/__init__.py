"""Unified PluginManifest adapter layer over the four existing extension kinds.

Public surface:
  - PluginManifest, KIND_* (manifest)
  - consent_summary, capability_diff (consent)
  - install, update, uninstall (lifecycle)
  - PersonaAdapter, SkillAdapter, ConnectorPluginAdapter, MCPAdapter (adapters)
"""

from .adapters import (
    ConnectorPluginAdapter,
    MCPAdapter,
    PersonaAdapter,
    SkillAdapter,
)
from .consent import capability_diff, consent_summary
from .lifecycle import install, uninstall, update
from .manifest import (
    KIND_CONNECTOR,
    KIND_MCP,
    KIND_PERSONA,
    KIND_SKILL,
    PLUGIN_KINDS,
    PluginManifest,
)

__all__ = [
    "ConnectorPluginAdapter",
    "KIND_CONNECTOR",
    "KIND_MCP",
    "KIND_PERSONA",
    "KIND_SKILL",
    "MCPAdapter",
    "PLUGIN_KINDS",
    "PersonaAdapter",
    "PluginManifest",
    "SkillAdapter",
    "capability_diff",
    "consent_summary",
    "install",
    "uninstall",
    "update",
]
