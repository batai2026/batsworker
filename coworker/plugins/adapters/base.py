"""Adapter protocol — each extension kind implements one to project its native object
into a ``PluginManifest``.

Adapters are pure read-side mappings: they never mutate the native object or the store.
"""

from __future__ import annotations

from typing import Any, Protocol

from ..manifest import PluginManifest


class PluginAdapter(Protocol):
    """把某一类扩展的原生对象投影成统一 PluginManifest。"""

    kind: str

    def to_manifest(self, native: Any) -> PluginManifest:
        """完整投影：kind/id/name/version/capability_set/permissions/source/description。"""
        ...

    def capability_set(self, native: Any) -> frozenset[str]:
        """扁平可比对的能力集合（用于 update 时的 grew/shrank diff）。"""
        ...

    def permissions(self, native: Any) -> dict:
        """该 kind 的权限语义字典。"""
        ...
