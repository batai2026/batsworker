"""Adapters projecting each native extension object into a PluginManifest."""

from .connector import ConnectorPluginAdapter
from .mcp import MCPAdapter
from .persona import PersonaAdapter
from .skill import SkillAdapter

__all__ = [
    "ConnectorPluginAdapter",
    "MCPAdapter",
    "PersonaAdapter",
    "SkillAdapter",
]
