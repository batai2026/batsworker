"""Persona adapter — projects a ``PersonaManifest`` into a ``PluginManifest``.

Reuses ``personas.loading.capability_set()`` (the existing flat comparable set) instead of
re-deriving it, so the diff semantics stay identical to persona's own update logic.
"""

from __future__ import annotations

from typing import Any

from ...personas.loading import capability_set as persona_capability_set
from ...personas.manifest import PersonaManifest
from ..manifest import KIND_PERSONA, PluginManifest


class PersonaAdapter:
    kind = KIND_PERSONA

    def to_manifest(self, native: PersonaManifest) -> PluginManifest:
        return PluginManifest(
            kind=KIND_PERSONA,
            id=native.id,
            name=native.name,
            version=native.version,
            capability_set=self.capability_set(native),
            permissions=self.permissions(native),
            source=native.source,
            description=native.description or native.tagline,
        )

    def capability_set(self, native: PersonaManifest) -> frozenset[str]:
        # 直接复用 persona 既有的 capability_set，保证 diff 语义与原生 update 一致。
        return frozenset(persona_capability_set(native))

    def permissions(self, native: PersonaManifest) -> dict[str, Any]:
        if native.connectors is True:
            connectors: Any = "all"
        else:
            connectors = list(native.connectors or ())
        return {
            "connectors": connectors,
            "default_permission_mode": native.default_permission_mode,
            "messaging": native.messaging,
            "team": native.team,
            "recommended_models": list(native.recommended_models),
        }
