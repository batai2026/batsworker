"""Skill adapter — projects a ``Skill`` (parsed SKILL.md) into a ``PluginManifest``.

A skill is instruction text plus optional bundled resources; its capability surface is the
skill itself (the model loads it on demand via ``load_skill``). ``allowed_tools`` restricts
which tools the skill may invoke.
"""

from __future__ import annotations

from typing import Any

from ...skills.base import Skill
from ..manifest import KIND_SKILL, PluginManifest


class SkillAdapter:
    kind = KIND_SKILL

    def to_manifest(self, native: Skill) -> PluginManifest:
        return PluginManifest(
            kind=KIND_SKILL,
            id=native.name,
            name=native.name,
            capability_set=self.capability_set(native),
            permissions=self.permissions(native),
            source=native.path,
            description=native.description,
        )

    def from_row(self, row: dict[str, Any]) -> PluginManifest:
        """Project a Settings listing row (name/description/scope/source/enabled).

        The listing is already parsed (see ``SkillStore.rows``), so the plugin inventory
        can be built without re-reading every SKILL.md.
        """
        name = str(row.get("name") or "")
        return PluginManifest(
            kind=KIND_SKILL,
            id=name,
            name=name,
            capability_set=frozenset({f"skill:{name}"}) if name else frozenset(),
            permissions={"scope": row.get("scope", ""), "enabled": bool(row.get("enabled", True))},
            source=row.get("source"),
            description=str(row.get("description") or ""),
        )

    def capability_set(self, native: Skill) -> frozenset[str]:
        return frozenset({f"skill:{native.name}"})

    def permissions(self, native: Skill) -> dict[str, Any]:
        return {"allowed_tools": list(native.allowed_tools)}
