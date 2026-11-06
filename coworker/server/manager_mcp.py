"""MCP cluster split out of manager.py (manager-split refactor).

Mixin methods only; shared state lives on the SessionManager facade (self.*). Module-level
imports are limited to what the moved code references; everything else resolves via self.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Optional

from ..mcp import (
    build_callables,
    delete_global_server,
    load_mcp_servers,
    patch_global_server,
    put_global_server,
    read_global,
)
from .manager_helpers import _redact, _stable_error

logger = logging.getLogger("coworker.manager")


class McpMixin:
    # -- MCP --------------------------------------------------------------------
    async def prepare_mcp_tools(
        self, session_id: str, *, workspace: Optional[str] = None, agent: str = "code"
    ) -> list[Any]:
        """Connect enabled MCP servers (global + workspace) and return their tool callables.

        Called from the async WS handler before `get_engine`; no-op if the engine is already
        built (its MCP tools are attached). Servers that fail to connect are skipped.
        """
        if session_id in self._engines:
            return []
        from ..connectors.descriptors import get_descriptor
        from ..connectors.tool_defs import (
            approval_for_tool,
            mcp_tool_defs,
            tool_enabled,
        )

        from ..mcp import oauth as mcp_oauth

        ws = self.engine_workspace(session_id, workspace=workspace, agent=agent)
        loop = asyncio.get_running_loop()
        effective: Optional[set[str]] = None  # computed lazily, once
        out: list[Any] = []
        # Persona `mcp:` wiring (OPE-58 sibling stub): a persona that declares an `mcp:`
        # list SCOPES its sessions to those servers — the consent screen already presents
        # that list as what the persona uses, so honoring it keeps consent truthful. It
        # only ever shrinks: the user's enabled/configured/authed gates all still apply,
        # and a persona with no list changes nothing. Connector-backed servers keep their
        # own per-persona connector gating instead.
        persona_mcp = self.persona_mcp_scope(agent)
        for server in load_mcp_servers(
            ws,
            secrets=self.secrets,
            workspace_trusted=self._mcp_workspace_trusted(ws),
        ):
            if not server.enabled:
                continue
            if server.auth == "oauth" and not mcp_oauth.has_tokens(
                server.name, self.secrets
            ):
                # NEVER start an interactive OAuth flow from a turn: a token-less
                # server here would open a browser and block every session for the
                # full flow timeout (owner-hit 2026-07-20 — a failed one-click's
                # leftover config froze all new sessions). Flows start only from an
                # explicit connect in Settings/Connectors.
                continue
            descriptor = get_descriptor(server.name)
            backed = descriptor is not None and bool(descriptor.mcp_url)
            if backed:
                # Connector-backed server: obey the same gates as connector tools —
                # the session's effective connector set and the per-tool toggles.
                # The descriptor's PIN is authoritative over whatever the config
                # file says (drift can only ever shrink the surface).
                if effective is None:
                    effective = self.effective_connectors(session_id, agent)
                if server.name not in effective:
                    continue
                prefix = f"mcp__{server.name}__"
                server.include_tools = [
                    t.name.removeprefix(prefix)
                    for t in mcp_tool_defs(server.name)
                    if tool_enabled(self.secrets, server.name, t.name)
                ]
            elif persona_mcp is not None and server.name not in persona_mcp:
                # Raw servers outside the persona's declared scope stay off its sessions.
                continue
            try:
                conn = await self.mcp.ensure(server)
                self._mcp_errors.pop(server.name, None)
                # Recovery resets the notice dedupe: if this server breaks again
                # later, the next session gets a fresh transcript notice.
                self._clear_mcp_notified(server.name)
            except Exception as exc:
                if mcp_oauth.is_auth_required(exc):
                    # Stored tokens no longer refresh (vendor rotated/expired
                    # them) — the non-interactive connect refused to open a
                    # browser. Record it so the MCP page shows WHY the server is
                    # dark; the session just runs without its tools.
                    self._mcp_errors[server.name] = (
                        "sign-in required — reconnect this server from its page"
                    )
                    logger.info(
                        "mcp %s needs re-auth; skipped for this session", server.name
                    )
                else:
                    # Bad command / crashed child / unreachable url — the session
                    # still runs without the tools, but the failure must not be
                    # silent (three-for-three silent failures in the 2026-08-20
                    # drill): record it for the MCP page and the session notice.
                    msg = str(exc) or exc.__class__.__name__
                    tail = self.mcp.last_stderr(server.name)
                    if tail:
                        msg = f"{msg} — {tail}"
                    self._mcp_errors[server.name] = msg[:500]
                    logger.warning(
                        "mcp %s failed to connect: %s", server.name, msg[:500]
                    )
                # Transcript notice on state CHANGE, not state (owner ruling
                # 2026-08-21): a continuously-broken server stamps only the first
                # session after it breaks (or breaks differently) — the Connectors
                # page carries the standing error. Personas that DECLARE the server
                # in their manifest keep the every-session notice: for them the
                # missing tools are material every time (the 2026-08-20 drill case).
                declared = persona_mcp is not None and server.name in persona_mcp
                if declared or self._should_notify_mcp_failure(
                    server.name, self._mcp_errors.get(server.name, "")
                ):
                    self._mcp_session_failures.setdefault(session_id, []).append(
                        server.name
                    )
                continue
            callables = build_callables(
                server,
                conn.tools,
                lambda tool, args, name=server.name: self.mcp.call(name, tool, args),
                loop,
            )
            if backed:
                # Per-tool approval from the pinned read/write classification
                # (server-level requires_approval is off for backed servers);
                # anything unclassified stays approval-gated — fail closed.
                for fn in callables:
                    fn.__aisuite_tool_metadata__.requires_approval = approval_for_tool(
                        fn.__aisuite_tool_metadata__.name, default=True
                    )
            out.extend(callables)
        return out

    def _should_notify_mcp_failure(self, name: str, error: str) -> bool:
        """True once per failure episode: the first session after `name` starts
        failing (or its error text changes) notices; unchanged-broken stays quiet.
        Persisted in prefs so an app relaunch doesn't re-stamp the same complaint.
        Compared on a NORMALIZED error: stderr often embeds per-process values
        (0x… object addresses, pids), which made "the same" failure look new on
        every relaunch and re-stamp every session (owner-hit 2026-08-21)."""
        stable = _stable_error(error)
        notified = self._prefs.setdefault("mcp_notified_errors", {})
        if notified.get(name) == stable:
            return False
        notified[name] = stable
        self._save_prefs()
        return True

    def _clear_mcp_notified(self, name: str) -> None:
        if self._prefs.get("mcp_notified_errors", {}).pop(name, None) is not None:
            self._save_prefs()

    def pop_mcp_failures(self, session_id: str) -> list[tuple[str, Optional[str]]]:
        """Drain (name, error) for servers that failed while preparing this session's
        tools — consumed once by the WS handler to append a transcript notice."""
        names = self._mcp_session_failures.pop(session_id, [])
        return [(n, self._mcp_errors.get(n)) for n in names]

    def list_mcp(self) -> list[dict[str, Any]]:
        """Servers from the global config + connection status (does not connect)."""
        from ..mcp import oauth as mcp_oauth

        from ..connectors.descriptors import get_descriptor

        out = []
        for name, raw in read_global().items():
            d = get_descriptor(name)
            if d is not None and d.mcp_url:
                # Connector-backed server: surfaced on the Connectors page (its
                # connect/disconnect lifecycle lives there), not in the MCP tab.
                continue
            connected = name in self.mcp._conns
            is_oauth = str(raw.get("auth", "")).lower() == "oauth"
            if connected:
                status = "connected"
            elif not raw.get("enabled", True):
                status = "disabled"
            elif name in self._mcp_authorizing:
                status = "authorizing"
            elif is_oauth and not mcp_oauth.has_tokens(name, self.secrets):
                status = "needs_auth"
            elif name in self._mcp_errors and not is_oauth:
                # Startup/connection failure (stdio crash, unreachable url) — the
                # drill class. OAuth servers keep their softer statuses: acquiring
                # tokens supersedes a stale sign-in error (the GUI still prints
                # last_error under the row either way).
                status = "error"
            else:
                status = "configured"
            out.append(
                {
                    "name": name,
                    "enabled": bool(raw.get("enabled", True)),
                    "transport": (
                        "http"
                        if (
                            raw.get("url")
                            or str(raw.get("type", "")).lower()
                            in {"http", "sse", "streamable-http"}
                        )
                        else "stdio"
                    ),
                    "requires_approval": bool(raw.get("requires_approval", True)),
                    "auth": "oauth" if is_oauth else None,
                    "status": status,
                    "auth_hint": name in self._mcp_auth_hints,
                    "last_test_at": self._prefs.get("mcp_last_test", {}).get(name),
                    "last_error": self._mcp_errors.get(name),
                    "tool_count": (
                        len(self.mcp._conns[name].tools) if connected else None
                    ),
                    "config": _redact(raw),
                }
            )
        return out

    def begin_mcp_connect(self, name: str) -> None:
        """Flag `authorizing` BEFORE the background connect task starts. The GUI's
        fast poll keys off this status; the first refresh used to outpace the task,
        so a failing Test showed nothing until the lazy 5s tick (owner-hit
        2026-08-21 — the button looked dead). Known names only, so an unknown
        server can't wedge the flag (connect_mcp only clears it on a match)."""
        if name in read_global():
            self._mcp_authorizing.add(name)

    async def connect_mcp(self, name: str) -> dict[str, Any]:
        """Connect one server NOW — for OAuth servers this may open the browser and wait
        for the loopback callback, so callers run it as a background task and watch
        list_mcp for the status flip."""
        from ..mcp import oauth as mcp_oauth

        for server in load_mcp_servers(
            self.default_workspace,
            secrets=self.secrets,
            workspace_trusted=self._mcp_workspace_trusted(self.default_workspace),
        ):
            if server.name != name:
                continue
            self._mcp_authorizing.add(name)
            self._mcp_errors.pop(name, None)
            self._mcp_auth_hints.discard(name)
            try:
                # The ONE place a browser sign-in may start: an explicit connect.
                # verify (not ensure): an already-live server gets a real round-trip
                # and a refreshed tool list instead of a cached yes.
                conn = await self.mcp.verify(server, interactive=True)
                # The Connectors row says "Ready · tested ⟨when⟩" — the claim must
                # survive an app restart, so it lives in prefs, not memory.
                self._prefs.setdefault("mcp_last_test", {})[name] = int(time.time())
                self._save_prefs()
                self._clear_mcp_notified(name)
                return {"ok": True, "tools": len(conn.tools)}
            except Exception as exc:
                if (
                    server.transport == "http"
                    and server.auth != "oauth"
                    and mcp_oauth.is_http_auth_error(exc)
                ):
                    # Anonymous probe of a guarded server (the add-by-URL flow):
                    # the answer is sign-in, not a raw 401 dump.
                    self._mcp_auth_hints.add(name)
                    msg = "authentication required — sign in to connect"
                else:
                    msg = str(exc) or exc.__class__.__name__
                    tail = self.mcp.last_stderr(name)
                    if tail:
                        msg = f"{msg} — {tail}"
                self._mcp_errors[name] = msg[:500]
                return {"ok": False, "error": self._mcp_errors[name]}
            finally:
                self._mcp_authorizing.discard(name)
        self._mcp_authorizing.discard(name)  # begin_mcp_connect flagged a name we never matched
        return {"ok": False, "error": f"unknown MCP server: {name}"}

    async def mcp_connect_connector(self, name: str) -> dict[str, Any]:
        """One-click connect for an MCP-BACKED connector (descriptor.mcp_url): seed
        the global server entry pinned to the curated allowlist, run the browser
        OAuth flow, and mark the connector profile `mode: "mcp"` on success."""
        from ..connectors.descriptors import get_descriptor
        from ..connectors.tool_defs import mcp_pinned_tools

        d = get_descriptor(name)
        if d is None or not d.mcp_url:
            return {"ok": False, "error": f"{name} has no MCP connect path"}
        put_global_server(
            name,
            {
                "url": d.mcp_url,
                "auth": "oauth",
                # Server-level approval off: writes gate per-tool via the pinned
                # read/write classification (prepare_mcp_tools); unknown vendor
                # tools never load at all (include_tools).
                "requires_approval": False,
                "include_tools": mcp_pinned_tools(name),
                "enabled": True,
            },
        )
        result = await self.connect_mcp(name)
        if result.get("ok"):
            profile = self.secrets.get(f"{name}:default") or {}
            self.secrets.put(
                f"{name}:default", {**profile, "mode": "mcp", "enabled": True}
            )
        else:
            # A failed connect must take its seeded config with it: an enabled
            # oauth entry with no tokens lingers forever (nothing owns it once
            # the descriptor's mcp_url is gone) and re-arms at every session
            # start — the owner-hit asana leftover, 2026-07-20.
            delete_global_server(name)
        return result

    async def signout_mcp(self, name: str) -> dict[str, Any]:
        """Drop the live connection (if any) and forget the stored OAuth tokens."""
        from ..mcp import oauth as mcp_oauth

        conn = self.mcp._conns.get(name)
        if conn is not None:
            conn.shutdown.set()
        self._mcp_errors.pop(name, None)
        removed = mcp_oauth.sign_out(name, self.secrets)
        return {"ok": True, "had_tokens": removed}

    def add_mcp(self, name: str, config: dict[str, Any]) -> dict[str, Any]:
        from ..mcp.config import parse_server
        from ..plugins import lifecycle
        from ..plugins.consent import install_payload
        from ..plugins.manifest import KIND_MCP

        consent = lifecycle.install(
            KIND_MCP,
            parse_server(name, config),
            commit=True,
            commit_payload=(name, config),
        )
        payload = install_payload(consent)
        return {"ok": True, "name": name, "consent": payload}

    def patch_mcp(
        self, name: str, changes: dict[str, Any], *, confirm: bool = False
    ) -> dict[str, Any]:
        from ..mcp.config import parse_server
        from ..plugins import lifecycle
        from ..plugins.consent import install_payload
        from ..plugins.manifest import KIND_MCP

        old = read_global().get(name)
        if old is None:
            return {"ok": False, "name": name}
        merged = {**old, **changes}
        consent = lifecycle.update(
            KIND_MCP,
            parse_server(name, merged),
            existing_native=parse_server(name, old),
            commit=False,
        )
        payload = install_payload(consent)
        if consent["permission_diff"]["widened"] and not confirm:
            return {
                "ok": False,
                "status": 409,
                "name": name,
                "requires_consent": True,
                "consent": payload,
            }
        patch_global_server(name, changes)
        return {"ok": True, "name": name, "consent": payload}

    def delete_mcp(self, name: str) -> dict[str, Any]:
        ok = delete_global_server(name)
        if ok:
            # A later re-add under the same name starts clean, not pre-failed —
            # and not pre-trusted (the old entry's test says nothing about the new).
            self._mcp_errors.pop(name, None)
            self._mcp_auth_hints.discard(name)
            if self._prefs.get("mcp_last_test", {}).pop(name, None) is not None:
                self._save_prefs()
            self._clear_mcp_notified(name)
            # Removing a server must not leave its connection running until the next
            # restart, nor its OAuth tokens + DCR registration in the secret store —
            # "Remove" is the user saying this server is GONE (owner review 2026-08-21).
            # The route runs in a threadpool; the shutdown event belongs to the loop.
            conn = self.mcp._conns.get(name)
            if conn is not None:
                if self._loop is not None:
                    self._loop.call_soon_threadsafe(conn.shutdown.set)
                else:
                    conn.shutdown.set()
            from ..mcp import oauth as mcp_oauth

            mcp_oauth.sign_out(name, self.secrets)
        return {"ok": ok, "name": name}

    async def mcp_tools(self, name: str) -> dict[str, Any]:
        """Connect one server and list its tools (name + description)."""
        for server in load_mcp_servers(
            self.default_workspace,
            secrets=self.secrets,
            workspace_trusted=self._mcp_workspace_trusted(self.default_workspace),
        ):
            if server.name == name:
                try:
                    conn = await self.mcp.ensure(server)
                except Exception as exc:
                    return {"name": name, "ok": False, "error": str(exc), "tools": []}
                return {
                    "name": name,
                    "ok": True,
                    "tools": [
                        {"name": t.name, "description": getattr(t, "description", "")}
                        for t in conn.tools
                    ],
                }
        return {"name": name, "ok": False, "error": "unknown server", "tools": []}

    async def reload_mcp(self) -> dict[str, Any]:
        """Drop live MCP connections so new sessions reconnect with fresh config."""
        await self.mcp.aclose()
        return {"ok": True}
