"""Unified plugin manifest — the single capability/decoration shape all extension kinds
project onto.

The four existing extension kinds (persona, skill, connector plugin, MCP server) each have
their own native format and store. ``PluginManifest`` is a *read-side projection*: adapters
map a native object into this frozen dataclass so consent, capability diffing, and signature
verification can be expressed once instead of four times. The native stores are NOT rewritten —
``lifecycle`` delegates install/update/uninstall to them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


# 统一的扩展类型枚举（字符串常量，避免 import cycle）。
KIND_PERSONA = "persona"
KIND_SKILL = "skill"
KIND_CONNECTOR = "connector"
KIND_MCP = "mcp"

PLUGIN_KINDS = (KIND_PERSONA, KIND_SKILL, KIND_CONNECTOR, KIND_MCP)


@dataclass(frozen=True)
class PluginManifest:
    """统一的插件清单。

    ``capability_set`` 是扁平可比对集合（同 persona 的 capability_set 思路），用于
    判断一次 update 是否能力增长（grew 非空 → 需重新 consent）。

    ``permissions`` 承载各 kind 的权限语义：
      - persona: connectors grant + default_permission_mode + messaging + team
      - skill:   allowed_tools
      - connector: auth + two_way + mcp_backed
      - mcp:     requires_approval + include_tools + exclude_tools + transport

    ``signature`` 是 signing.verify_directory() 的结果字典（未签名则为空 dict）。
    """

    kind: str
    id: str
    name: str
    version: str = ""
    deps: list[str] = field(default_factory=list)
    capability_set: frozenset[str] = field(default_factory=frozenset)
    permissions: dict[str, Any] = field(default_factory=dict)
    signature: dict[str, Any] = field(default_factory=dict)
    source: Optional[str] = None
    description: str = ""
