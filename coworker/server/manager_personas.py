"""Personas cluster split out of manager.py (manager-split refactor).

Mixin methods only; shared state lives on the SessionManager facade (self.*). Module-level
imports are limited to what the moved code references; everything else resolves via self.
"""

from __future__ import annotations

from typing import Any, Optional

from ..connections import effective as effective_connections
from ..connectors import connector_list


class PersonasMixin:
    # -- signed persona distribution (coworker/personas/distribution.py) --------

    def persona_catalog(self, *, force: bool = False) -> dict[str, Any]:
        from ..config import load_config
        from ..personas.distribution import CatalogError, fetch_catalog

        url = (load_config().persona_catalog_url or "").strip()
        if not url:
            return {"ok": False, "error": "no community catalog configured"}
        try:
            data = fetch_catalog(
                url,
                self.data_dir / "persona-catalog-cache.json",
                force_fresh=force,
            )
        except CatalogError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, **data, "url": url}

    def install_persona_from_catalog(self, slug: str) -> dict[str, Any]:
        """Catalog install: pinned-key verification is FAIL CLOSED (registry refuses
        an unsigned/mismatched bundle), and a verifying install auto-trusts the
        entry's key (the user chose the catalog entry — that IS the trust act)."""
        from ..personas.distribution import catalog_entry

        cat = self.persona_catalog()
        if not cat.get("ok"):
            return cat
        entry = catalog_entry(cat, slug)
        if entry is None:
            return {"ok": False, "error": f"no catalog entry '{slug}'"}
        source = entry.get("git_url") or entry.get("dir")
        if not source:
            return {"ok": False, "error": "catalog entry has no source"}
        result = self.install_from_git(str(source), expected_pubkey=str(entry["pubkey"]))
        if result and result[0].get("ok") is False:
            return result[0]
        if result:
            result[0]["catalog"] = {"slug": slug, "author": entry.get("author", "")}
        return result

    def list_trusted_keys(self) -> list[dict[str, Any]]:
        from ..personas.distribution import TrustedKeyStore

        return TrustedKeyStore(self.data_dir).list()

    def add_trusted_key(self, pubkey_path: str, label: str = "") -> dict[str, Any]:
        from ..personas.distribution import TrustedKeyStore

        return TrustedKeyStore(self.data_dir).add(pubkey_path, label)

    def remove_trusted_key(self, key_id: str) -> dict[str, Any]:
        from ..personas.distribution import TrustedKeyStore

        return TrustedKeyStore(self.data_dir).remove(key_id)

    def _routing_targets(self, session_id: str, agent: str) -> list[str]:
        """The channel address(es) this session's Inbox routes OUT to — used to warn when a
        subscription (inbound) collides with Inbox routing (outbound) on the same channel.
        """
        binding = self.inbox_routing.binding_for(
            self.inbox_routing.route_for(session_id, agent)
        )
        return [f"{binding.channel}:{binding.target}"] if binding.channel else []

    # -- connection hierarchy (UI-REFRESH §4) -----------------------------------
    def _persona_of(self, session_id: str, persona_id: Optional[str] = None) -> str:
        if persona_id:
            return persona_id
        # The live engine is the freshest truth — a brand-new session has no record row
        # until its first send, but its socket already knows the persona.
        engine = self._engines.get(session_id)
        live = getattr(engine, "agent_name", None) if engine is not None else None
        if live:
            return live
        record = self.session_store.load(session_id)
        return (record.agent if record else None) or self.personas.default_id()

    def _persona_connector_grant(self, persona_id: str) -> Optional[set[str]]:
        """The persona's declared connector allowlist (OPE-93). None = unrestricted (the
        `all` sentinel of general builtins); a set = only these ids can ever be effective
        for its sessions — the empty set means no connector access at all."""
        entry = self.personas.get(persona_id)
        if entry is None or entry.manifest is None:
            # Builder-based builtins (Chat/Code/Cowork/Ops) predate the allowlist: their
            # `connectors` trait gates TOOLS only, while their sessions legitimately use
            # the drawer/inbound path (channel bindings). No manifest → no restriction.
            return None
        declared = entry.manifest.connectors
        if declared is True:
            return None
        return set(declared or ())

    def effective_connectors(
        self, session_id: str, persona_id: Optional[str] = None
    ) -> set[str]:
        """The connectors effectively enabled for this session (§4.1): connected AND not muted by
        the session override / persona default AND within the persona's declared grant (OPE-93).
        Drives the engine's connector-tool gating and the inbound delivery gate; seeds the
        persona defaults from the manifest on first read using the full connected set.
        """
        persona = self._persona_of(session_id, persona_id)
        connected = {c["name"] for c in connector_list(self.secrets) if c["connected"]}
        entry = self.personas.get(persona)
        manifest = entry.manifest if entry else None
        persona_defaults = self.persona_connections.defaults_for(
            persona, manifest, connected=connected
        )
        session_overrides = self.session_connections.get(session_id)
        effective = set(
            effective_connections(
                connected=connected,
                persona_defaults=persona_defaults,
                session_overrides=session_overrides,
            )
        )
        grant = self._persona_connector_grant(persona)
        return effective if grant is None else effective & grant

    def _inbound_connector_allowed(self, session_id: str, connector: str) -> bool:
        """Whether an inbound message on `connector` should be DELIVERED to `session_id` (§4.3).

        Uses the SAME effective set as the engine's connector-tool gating so the inbound gate and the
        tool gate can never disagree (a muted connector is muted both ways, from the first message).
        """
        return connector in self.effective_connectors(session_id)

    # -- persona + session connection surfaces (UI-REFRESH §5/§6) ----------------
    def _connected_connectors(self) -> set[str]:
        """The account-connected connector names (the first layer of the §4 hierarchy)."""
        return {c["name"] for c in connector_list(self.secrets) if c["connected"]}

    def _persona_default_connections(
        self, persona_id: str, manifest, connected: set[str]
    ) -> list[dict[str, Any]]:
        """The persona's default connector map (seeded from the manifest's connector recommends on
        first read, then user-editable) as a list, each annotated with account-connectedness.
        """
        defaults = self.persona_connections.defaults_for(
            persona_id, manifest, connected=connected
        )
        return [
            {"connector": c, "enabled": bool(enabled), "connected": c in connected}
            for c, enabled in defaults.items()
        ]

    def persona_detail(self, persona_id: str) -> Optional[dict[str, Any]]:
        """Identity + capabilities + recommends(+connected) + default connections for one persona
        (UI-REFRESH §5). Returns None for an unknown id (the route maps that to an error).
        """
        entry = self.personas.get(persona_id)
        if entry is None:
            return None
        manifest = entry.manifest
        connected = self._connected_connectors()
        recommends = [
            {
                "kind": rec.kind,
                "ref": rec.ref,
                "reason": rec.reason,
                "tier": rec.tier,
                "connected": rec.ref in connected,
            }
            for rec in (manifest.recommends if manifest else [])
        ]
        media_dir = self.personas.media_dir(persona_id)
        media = (
            sorted(
                f.name
                for f in media_dir.iterdir()
                if f.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".webp"}
            )
            if media_dir
            else []
        )
        return {
            "id": entry.id,
            "name": entry.name,
            "icon": entry.icon,
            "tagline": entry.tagline,
            "description": manifest.description if manifest else "",
            "media": media,
            "builtin": entry.builtin,
            "group": entry.group,
            "enabled": self.personas.is_enabled(entry.id),
            "surfaced": self.personas.is_surfaced(entry.id),
            "default": entry.id == self.personas.default_id(),
            "tools": list(entry.tools),
            "recommended_models": list(manifest.recommended_models) if manifest else [],
            "default_permission_mode": (
                manifest.default_permission_mode if manifest else "interactive"
            ),
            "requires_folder": entry.requires_folder,
            "recommends": recommends,
            "default_connections": self._persona_default_connections(
                persona_id, manifest, connected
            ),
        }

    def set_persona_connection(
        self, persona_id: str, connector: str, enabled: bool
    ) -> dict[str, Any]:
        """Set a persona-default connector on/off (UI-REFRESH §5). Seeds the manifest defaults
        first so the stored row stays complete (the edit overlays the full seed rather than
        collapsing the row to this one connector), then returns the refreshed default_connections
        so the client can re-render without a second GET."""
        entry = self.personas.get(persona_id)
        if entry is None:
            return {"ok": False, "error": f"unknown persona: {persona_id}"}
        manifest = entry.manifest
        connected = self._connected_connectors()
        self.persona_connections.defaults_for(persona_id, manifest, connected=connected)
        self.persona_connections.set(persona_id, connector, bool(enabled))
        return {
            "ok": True,
            "default_connections": self._persona_default_connections(
                persona_id, manifest, connected
            ),
        }

    def set_persona_enabled(self, persona_id: str, enabled: bool) -> dict[str, Any]:
        """Flip a persona's enabled flag. Disabling also archives its real (unarchived,
        non-internal) sessions — disable means "put this coworker and its history away", so
        the persona's sidebar section disappears with it (owner call, 2026-07-04). Re-enabling
        never unarchives: that would overwrite the user's archive state; history returns one
        click at a time via the Show-archived disclosure. Raises KeyError for unknown ids.
        """
        self.personas.set_enabled(persona_id, enabled)
        archived = 0
        if not enabled:
            for r in self.session_store.list():
                if (
                    r.agent == persona_id
                    and not r.archived
                    and not r.session_id.startswith("__")
                ):
                    self.session_store.set_flags(r.session_id, archived=True)
                    archived += 1
        return {"ok": True, "archived_sessions": archived}

    def _connection_detail(
        self, session_id: str, connector: str, info: Optional[dict[str, Any]]
    ) -> str:
        """A short human description of WHY a connector is live for a session: the chat ids it's
        subscribed to on that platform, plus "DMs" if this is the designated DM session. Channel
        *names* would need the live adapter's resolve cache (not cheap here), so we show the chat
        ids; with no subscription/DM tie we fall back to the connector's title."""
        prefix = f"{connector}:"
        parts = [
            s.channel.split(":", 1)[1]
            for s in self.subscriptions.for_session(session_id)
            if s.channel.startswith(prefix)
        ]
        if self.dm_session() == session_id:
            parts.append("DMs")
        if parts:
            return " · ".join(parts)
        return (info or {}).get("title") or connector

    def session_connections_view(
        self, session_id: str, persona_id: Optional[str] = None
    ) -> dict[str, Any]:
        """The per-session connections drawer payload (UI-REFRESH §6): every account-connected
        connector with its effective on/off state (muted ones stay VISIBLE as off — a §4.2 toggle
        must never make a row vanish), the persona's connector recommends that aren't yet
        account-connected, and the attention count (= those unconnected recommends).

        ``persona_id`` is the caller's hint (the GUI knows the active persona). It matters for a
        brand-new session: no SessionRecord exists until the first turn persists, so without the
        hint the view would resolve to the DEFAULT persona and show its defaults/recommends —
        the owner's 2026-07-03 finding (a fresh Project Manager session rendered cowork's view).
        """
        persona = self._persona_of(session_id, persona_id)
        entry = self.personas.get(persona)
        manifest = entry.manifest if entry else None
        connectors = connector_list(self.secrets)
        by_name = {c["name"]: c for c in connectors}
        connected_names = {c["name"] for c in connectors if c["connected"]}
        # OPE-93 (owner-hit 2026-08-15): the drawer must show the persona's world, not the
        # account's. An undeclared connector is not a mutable source of this session — it
        # was rendering as toggled-ON while the engine (correctly) refused its tools.
        grant = self._persona_connector_grant(persona)
        if grant is not None:
            connected_names &= grant
        effective = self.effective_connectors(session_id, persona)
        connected = [
            {
                "connector": name,
                "enabled": name in effective,
                "detail": self._connection_detail(session_id, name, by_name.get(name)),
            }
            for name in sorted(connected_names)
        ]
        recommended = [
            {
                "connector": rec.ref,
                "reason": rec.reason,
                "tier": rec.tier,
                "connected": False,
            }
            for rec in (manifest.recommends if manifest else [])
            if rec.kind == "connector" and rec.ref not in connected_names
        ]
        return {
            "connected": connected,
            "recommended": recommended,
            "attention": sum(1 for r in recommended if not r["connected"]),
        }
