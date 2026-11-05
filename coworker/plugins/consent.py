"""Unified consent surface — one output shape for all four extension kinds.

``consent_summary(pm)`` is what the install/preview screen shows.
``capability_diff(old, new)`` drives the "did this update grow capabilities?" decision:
a non-empty ``grew`` set means the user must re-consent (same semantics as persona's
``_replaces_of`` / ``capabilities_grew``).
"""

from __future__ import annotations

from typing import Any

from .manifest import (
    KIND_CONNECTOR,
    KIND_MCP,
    KIND_PERSONA,
    KIND_SKILL,
    PluginManifest,
)

# 各 capability 前缀到风险标签的粗略映射（consent 展示用，不做安全决策）。
_RISK_BY_PREFIX = {
    "tool:": "tools",
    "mcp:": "mcp",
    "connector:": "connectors",
    "skill:": "skills",
    "messaging": "messaging",
    "team:": "team",
}


def _risks(capability_set: frozenset[str]) -> list[str]:
    """从 capability_set 推导风险标签列表（去重、排序）。"""
    risks: set[str] = set()
    for cap in capability_set:
        if cap == "messaging":
            risks.add("messaging")
            continue
        for prefix, label in _RISK_BY_PREFIX.items():
            if cap.startswith(prefix):
                risks.add(label)
                break
    return sorted(risks)


def consent_summary(pm: PluginManifest) -> dict[str, Any]:
    """统一 consent 输出结构，四种 kind 共用。"""
    return {
        "kind": pm.kind,
        "id": pm.id,
        "name": pm.name,
        "version": pm.version,
        "description": pm.description,
        "source": pm.source,
        "capability_set": sorted(pm.capability_set),
        "risks": _risks(pm.capability_set),
        "permissions": pm.permissions,
        "deps": list(pm.deps),
        "signature": pm.signature,
    }


def capability_diff(old: PluginManifest, new: PluginManifest) -> dict[str, Any]:
    """比较两版 manifest 的能力集。

    返回 {"grew": [...], "shrank": [...], "same": [...]}。
    grew 非空 → 能力增长 → 需重新 consent。
    """
    old_set = set(old.capability_set)
    new_set = set(new.capability_set)
    return {
        "grew": sorted(new_set - old_set),
        "shrank": sorted(old_set - new_set),
        "same": sorted(old_set & new_set),
    }


# kind 名 → 中文标签（consent 卡片显示用）。
KIND_LABELS = {
    KIND_PERSONA: "Coworker",
    KIND_SKILL: "Skill",
    KIND_CONNECTOR: "Connector",
    KIND_MCP: "MCP server",
}
