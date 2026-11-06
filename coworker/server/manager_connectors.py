"""Connectors cluster split out of manager.py (manager-split refactor).

Mixin methods only; shared state lives on the SessionManager facade (self.*). Module-level
imports are limited to what the moved code references; everything else resolves via self.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, Optional

from ..connectors import (
    Gateway,
    MessageSource,
    connect_connector,
    connector_list,
    disconnect_connector,
    load_settings,
    make_adapter,
    set_experimental_enabled,
    slack_split,
    update_connector_tools,
)
from ..connectors.browser_automation import (
    browser_close_session,
    browser_state,
    browser_take_screenshot,
)
from ..connectors.browser_recording import list_recordings as list_browser_recordings
from ..secrets import state_dir
from .manager_helpers import _inbound_epoch

logger = logging.getLogger("coworker.manager")


class ConnectorsMixin:
    # -- connectors -------------------------------------------------------------
    def list_connectors(self) -> list[dict[str, Any]]:
        # Enrich two-way connectors with the live gateway's recently-seen senders, so the Connectors
        # tab can manage the allow-list inline (each recent sender flagged authorized or not).
        connectors = connector_list(self.secrets)
        for c in connectors:
            if not (c.get("two_way") and c.get("connected")):
                continue
            allowed = set(c.get("allowed_users") or [])
            # Per-workspace allow-lists (managed relay) — a sender is judged against
            # ITS workspace's list; the flat list only governs team-less (socket) events.
            team_allowed = {
                w["team_id"]: set(w.get("allowed_users") or [])
                for w in (c.get("workspaces") or [])
            }
            recent = self.gateway.recent_senders(c["name"]) if self.gateway else []
            for r in recent:
                team = r.get("team_id")
                pool = team_allowed.get(team, set()) if team else allowed
                r["authorized"] = r.get("user_id") in pool
                # Backfill from the people directory (an event may predate name scopes).
                r["user_name"] = r.get("user_name") or self._people.get(
                    f"{c['name']}:{r.get('user_id')}"
                )
            c["recent"] = recent
            # Parked unauthorized messages (§19) — the connector page resolves them inline.
            c["unauthorized"] = self.parked.list(c["name"])
            # Allow-list display names from the people directory (ids stay the source of truth).
            c["allowed_user_names"] = {
                u: self._people.get(f"{c['name']}:{u}")
                for u in (c.get("allowed_users") or [])
            }
            c["approval_owner_names"] = {
                u: self._people.get(f"{c['name']}:{u}")
                for u in (c.get("approval_owner_ids") or [])
            }
            for w in c.get("workspaces") or []:
                w["allowed_user_names"] = {
                    u: self._people.get(f"{c['name']}:{u}")
                    for u in (w.get("allowed_users") or [])
                }
                w["approval_owner_names"] = {
                    u: self._people.get(f"{c['name']}:{u}")
                    for u in (w.get("approval_owner_ids") or [])
                }
        return connectors

    def connect_connector(
        self, name: str, fields: dict[str, Any], *, acknowledged: bool = False
    ) -> dict[str, Any]:
        # validates the token by a live API call (sync httpx) — run off the event loop
        return connect_connector(self.secrets, name, fields, acknowledged=acknowledged)

    def set_experimental_connectors(self, value: bool) -> dict[str, Any]:
        return set_experimental_enabled(self.secrets, value)

    def disconnect_connector(self, name: str) -> dict[str, Any]:
        # MCP-backed profile: drop the live server connection before the tokens go.
        conn = self.mcp._conns.get(name)
        if conn is not None:
            conn.shutdown.set()
        return disconnect_connector(self.secrets, name)

    def update_connector_tools(
        self, name: str, enabled: dict[str, Any]
    ) -> dict[str, Any]:
        return update_connector_tools(self.secrets, name, enabled)

    def browser_state(self) -> dict[str, Any]:
        return browser_state()

    def browser_screenshot(self) -> dict[str, Any]:
        return browser_take_screenshot()

    def browser_close(self) -> dict[str, Any]:
        return browser_close_session()

    def browser_recordings(self) -> dict[str, Any]:
        """Saved browser replay scripts (the demo→skill door's recording source)."""
        return {"recordings": list_browser_recordings(state_dir())}

    # -- connector/plugin marketplace ---------------------------------------------
    def connector_marketplace(self) -> dict[str, Any]:
        from ..connectors.marketplace import marketplace_catalog

        return marketplace_catalog()

    def install_connector_plugin(self, name: str) -> dict[str, Any]:
        from ..connectors.marketplace import install_plugin

        return install_plugin(name)

    def uninstall_connector_plugin(self, name: str) -> dict[str, Any]:
        from ..plugins import lifecycle
        from ..plugins.manifest import KIND_CONNECTOR

        return {**lifecycle.uninstall(KIND_CONNECTOR, name), "name": name}

    # -- gateway + connector allow-list (inbound messaging) ---------------------
    def allow_user(
        self,
        name: str,
        user_id: str,
        team_id: Optional[str] = None,
        *,
        display_name: str = "",
    ) -> dict[str, Any]:
        out = self._set_allowed(name, user_id, team_id=team_id, add=True)
        # Directory picks arrive with the name in hand — record it so the chip
        # is readable immediately (message-driven allows learn it on arrival).
        if out.get("ok") and display_name:
            self._note_person(name, user_id, display_name)
        return out

    def disallow_user(
        self, name: str, user_id: str, team_id: Optional[str] = None
    ) -> dict[str, Any]:
        if name == "slack" and user_id in self.slack_approval_owner_ids(team_id):
            return {
                "ok": False,
                "error": "Remove this person as an approval owner first.",
            }
        return self._set_allowed(name, user_id, team_id=team_id, add=False)

    def slack_approval_owner_ids(self, team_id: Optional[str] = None) -> set[str]:
        """Stable Slack user ids allowed to resolve consequential Inbox prompts.

        Managed relay installs are installer-owned. Manual Socket Mode has no
        human OAuth identity, so its owners are selected explicitly.
        """
        key = f"slack:team:{team_id}" if team_id else "slack:default"
        profile = self.secrets.get(key) or {}
        if team_id:
            installer = str(profile.get("slack_user_id") or "").strip()
            return {installer} if installer else set()
        if profile.get("mode") == "relay":
            return set()
        return {
            str(user_id).strip()
            for user_id in (profile.get("approval_owner_ids") or [])
            if str(user_id).strip()
        }

    def set_slack_approval_owner(
        self, user_id: str, *, add: bool, display_name: str = ""
    ) -> dict[str, Any]:
        """Edit Manual Socket Mode approval owners.

        Owner status implies inbound permission. Relay ownership is derived from
        the OAuth installer and is intentionally not editable here.
        """
        user_id = str(user_id).strip()
        if not user_id:
            return {"ok": False, "error": "user_id required"}
        profile = self.secrets.get("slack:default")
        if not profile:
            return {"ok": False, "error": "Slack is not connected in Manual mode."}
        if profile.get("mode") == "relay" or profile.get("managed"):
            return {
                "ok": False,
                "error": "Relay approval ownership is set by the Slack installer.",
            }

        owners = self.slack_approval_owner_ids()
        if add:
            owners.add(user_id)
        else:
            owners.discard(user_id)
            if not owners and self._has_manual_slack_inbox_binding():
                return {
                    "ok": False,
                    "error": (
                        "Choose another approval owner before removing the last one "
                        "while Slack Inbox routing is active."
                    ),
                }
        profile["approval_owner_ids"] = sorted(owners)
        if add:
            allowed = set(profile.get("allowed_users") or [])
            allowed.add(user_id)
            profile["allowed_users"] = sorted(allowed)
        self.secrets.put("slack:default", profile)
        if display_name:
            self._note_person("slack", user_id, display_name)
        if self.gateway is not None and "slack" in self.gateway.settings:
            self.gateway.settings["slack"].allowed_users = set(
                profile.get("allowed_users") or []
            )
        return {
            "ok": True,
            "approval_owner_ids": sorted(owners),
            "allowed_users": list(profile.get("allowed_users") or []),
        }

    def _has_manual_slack_inbox_binding(self) -> bool:
        for raw in self.inbox_routing.bindings():
            if raw.get("channel") != "slack":
                continue
            team_id, _ = slack_split(str(raw.get("target") or ""))
            if team_id is None:
                return True
        return False

    def _slack_actor_owns_item(
        self,
        item,
        *,
        actor_id: str,
        chat_id: str,
        team_id: Optional[str],
    ) -> bool:
        """Authorize a Slack resolution against both its owner and delivery binding."""
        event_team, event_channel = slack_split(chat_id)
        event_team = team_id or event_team
        binding = self.inbox_routing.binding_for(item.inbox)
        owner_team = event_team
        if binding.channel == "slack":
            owner_team, bound_channel = slack_split(binding.target)
            if owner_team != event_team or bound_channel != event_channel:
                return False
        return bool(actor_id) and actor_id in self.slack_approval_owner_ids(owner_team)

    def _set_allowed(
        self, name: str, user_id: str, *, team_id: Optional[str] = None, add: bool
    ) -> dict[str, Any]:
        """Add/remove a sender on the allow-list. With `team_id` the edit targets that
        scope's profile — a workspace's `slack:team:<id>`, or a GitHub App
        installation's `github:install:<id>` (the same per-tenant pattern);
        without, the flat `<name>:default` list (manual single-workspace mode)."""
        user_id = str(user_id).strip()
        if not user_id:
            return {"ok": False, "error": "user_id required"}
        scope = "install" if name == "github" else "team"
        profile_key = f"{name}:{scope}:{team_id}" if team_id else f"{name}:default"
        profile = self.secrets.get(profile_key)
        if not profile:
            return {
                "ok": False,
                "error": (
                    "workspace not connected" if team_id else "connector not connected"
                ),
            }
        allowed = set(profile.get("allowed_users") or [])
        allowed.add(user_id) if add else allowed.discard(user_id)
        profile["allowed_users"] = sorted(allowed)
        self.secrets.put(profile_key, profile)
        # reflect into the live gateway so it takes effect without a restart
        if self.gateway is not None and name in self.gateway.settings:
            if team_id:
                from ..connectors import TeamAuth

                teams = self.gateway.settings[name].teams
                team = teams.setdefault(team_id, TeamAuth())
                team.allowed_users = set(allowed)
            else:
                self.gateway.settings[name].allowed_users = set(allowed)
        return {"ok": True, "allowed_users": sorted(allowed), "team_id": team_id}

    async def disconnect_slack_workspace(self, team_id: str) -> dict[str, Any]:
        """Stop relaying ONE workspace: delete the cloud routing row (best-effort),
        drop the local per-team token, and hot-reload the gateway. Removing the last
        workspace also clears relay mode on slack:default so the connector reads
        disconnected (the manual Socket Mode fields, if any, are left untouched)."""
        team_id = str(team_id).strip()
        profile_key = f"slack:team:{team_id}"
        if not team_id or not self.secrets.get(profile_key):
            return {"ok": False, "error": "workspace not connected"}
        from .. import cloud
        from ..config import load_config

        await asyncio.to_thread(
            lambda: cloud.slack_disconnect_workspace(
                self.secrets, load_config(), team_id
            )
        )
        self.secrets.delete(profile_key)
        remaining = [
            m["profile"]
            for m in self.secrets.status()
            if m.get("profile", "").startswith("slack:team:")
        ]
        if not remaining:
            default = self.secrets.get("slack:default") or {}
            if default.get("mode") == "relay":
                default.pop("mode", None)
                default.pop("managed", None)
                if default.get("bot_token"):
                    # Manual Socket Mode creds predating the relay switch: keep them
                    # stored but DISABLED — removing the last workspace must never
                    # silently start listening with old tokens.
                    default["type"] = "token"
                    default["enabled"] = False
                    self.secrets.put("slack:default", default)
                else:
                    default.pop("type", None)
                    default.pop("enabled", None)
                    if default:  # e.g. a flat allow-list worth keeping
                        self.secrets.put("slack:default", default)
                    else:
                        self.secrets.delete("slack:default")
        await self.refresh_gateway()
        return {"ok": True, "remaining_workspaces": len(remaining)}

    def slack_status(self) -> dict[str, Any]:
        """Slack connection health in three honest layers (UX-DECISIONS §21):
        the desktop↔relay socket, the cloud sign-in that authorizes it, and each
        workspace's bot token. The desktop can't see the Slack↔cloud leg, so no
        layer here ever claims it — event silence ≠ outage."""
        from .. import cloud

        default = self.secrets.get("slack:default") or {}
        mode = default.get("mode") or ""
        signin = cloud.status(self.secrets)

        relay: dict[str, Any] = {
            "state": "offline",
            "reconnects": 0,
            "last_event_at": None,
            "last_error": "",
        }
        teams: dict[str, Any] = {}
        adapter = (
            self.gateway._adapters.get("slack") if self.gateway is not None else None
        )
        snapshot = getattr(
            adapter, "status", None
        )  # relay adapter only; Socket Mode has none
        if callable(snapshot):
            relay = snapshot()
            teams = relay.pop("teams", {})
        return {
            "ok": True,
            "mode": mode,
            "relay": relay,
            "signed_in": bool(signin.get("signed_in")),
            "teams": teams,
        }

    async def disconnect_github_installation(
        self, installation_id: str
    ) -> dict[str, Any]:
        """Stop relaying ONE GitHub installation: delete the cloud routing rows
        (best-effort), drop the local profile, hot-reload the gateway. The Slack
        per-workspace disconnect, GitHub flavour — a manual PAT stays untouched."""
        installation_id = str(installation_id).strip()
        from .. import cloud
        from ..config import load_config
        from ..connectors import github_installs

        if not installation_id or not self.secrets.get(
            github_installs.PREFIX + installation_id
        ):
            return {"ok": False, "error": "installation not connected"}
        await asyncio.to_thread(
            lambda: cloud.github_disconnect_installation(
                self.secrets, load_config(), installation_id
            )
        )
        result = github_installs.disconnect_install(self.secrets, installation_id)
        await self.refresh_gateway()
        return result

    def github_status(self) -> dict[str, Any]:
        """GitHub relay health, same three honest layers as Slack: the shared
        relay socket, the cloud sign-in, and per-installation token health."""
        from .. import cloud

        default = self.secrets.get("github:default") or {}
        signin = cloud.status(self.secrets)
        relay: dict[str, Any] = {
            "state": "offline",
            "reconnects": 0,
            "last_event_at": None,
            "last_error": "",
        }
        installs: dict[str, Any] = {}
        missed: dict[str, Any] = {}
        adapter = (
            self.gateway._adapters.get("github") if self.gateway is not None else None
        )
        snapshot = getattr(adapter, "status", None)
        if callable(snapshot):
            relay = snapshot()
            installs = relay.pop("installs", {})
            missed = relay.pop("missed", {})
        return {
            "ok": True,
            "mode": default.get("mode") or "",
            "relay": relay,
            "signed_in": bool(signin.get("signed_in")),
            "installs": installs,
            "missed": missed,
        }

    async def start_gateway(self) -> list[str]:
        """Build the messaging gateway and start enabled listeners. Inbound messages route to
        durable sessions: a channel message to its subscribers, a DM to the designated DM session
        (else parked). Returns the platforms whose listeners came up."""
        # Team steering/kicks are dispatched from tool threads; they need the app loop.
        self._loop = asyncio.get_running_loop()
        self.scheduler.start()  # tick scheduler for automations (independent of connectors)
        return await self._build_and_start_gateway()

    async def refresh_gateway(self) -> list[str]:
        """Hot-reload the messaging listeners with fresh secrets — called after a connector
        connect/disconnect so pasting new tokens takes effect immediately. A platform socket
        (Slack Socket Mode) authenticates at connect time, so new creds mean reopening that
        socket; this replaces the adapters in-process — the sidecar never restarts."""
        await self.stop_gateway()
        started = await self._build_and_start_gateway()
        print(f"[coworker] messaging gateway reloaded: {', '.join(started) or 'none'}")
        return started

    async def _build_and_start_gateway(self) -> list[str]:
        settings = load_settings(self.secrets)
        self.gateway = Gateway(
            secrets=self.secrets,
            settings=settings,
            handler=self._dispatch_inbound,
            reply_resolver=self._resolve_inbox_reply,
            interaction_handler=self._on_interaction,
            on_unauthorized=self._park_unauthorized,
        )
        # Managed Slack relay wiring (only used when a connector picks relay mode):
        # the cloud sign-in JWT authorizes the relay WebSocket, and the relay
        # endpoint comes from config. Both are lazy — Socket Mode needs neither.
        from ..cloud import fresh_access_token
        from ..config import load_config

        cloud_config = load_config()

        def _relay_token() -> str:
            return fresh_access_token(self.secrets, cloud_config) or ""

        # Every relay-mode platform shares ONE cloud socket; the hub fans frames
        # out by provider tag. Built lazily on the first relay adapter.
        relay_ws_url = getattr(cloud_config, "cloud_relay_ws_url", "") or None
        relay_hub = None
        if relay_ws_url:
            from ..connectors.relay_client import RelayHub

            relay_hub = RelayHub(relay_ws_url, _relay_token)

        async def _github_token(installation_id: str) -> str:
            from ..cloud import github_installation_token

            return await asyncio.to_thread(
                github_installation_token, self.secrets, cloud_config, installation_id
            )

        for platform, st in settings.items():
            if not st.enabled:
                continue
            profile = self.secrets.get(f"{platform}:default") or {}
            adapter = make_adapter(
                platform,
                profile,
                secrets=self.secrets,
                token_provider=_relay_token,
                relay_url=relay_ws_url,
                relay_hub=relay_hub,
                github_token_client=_github_token,
            )
            if adapter is not None:
                self.gateway.register(adapter)
        return await self.gateway.start()

    async def stop_gateway(self) -> None:
        if self.gateway is not None:
            await self.gateway.stop()
            self.gateway = None

    # -- unauthorized inbound (parked, §19) --------------------------------------
    def _note_person(
        self, platform: str, user_id: Optional[str], name: Optional[str]
    ) -> None:
        """Remember a sender's display name (persisted) so ID-keyed surfaces — the allow-list
        chips above all — can show who a U07JK… actually is. Best-effort, newest name wins.
        """
        if not user_id or not name:
            return
        key = f"{platform}:{user_id}"
        if self._people.get(key) != name:
            self._people[key] = name
            try:
                self._people_path.write_text(json.dumps(self._people))
            except OSError:
                pass

    async def _park_unauthorized(self, event) -> None:
        """Gateway callback: keep what an unallowed sender said (names already resolved by the
        adapter, best-effort) so the owner can allow-and-deliver without a re-send."""
        s = event.source
        self._note_person(s.platform, s.user_id, s.user_name)
        self.parked.park(
            platform=s.platform,
            chat_id=s.chat_id,
            chat_name=s.chat_name,
            user_id=s.user_id or "?",
            user_name=s.user_name,
            chat_type=s.chat_type,
            thread_id=s.thread_id,
            team_id=s.team_id,
            text=event.text or "",
        )

    async def resolve_unauthorized(
        self, name: str, item_id: str, action: str
    ) -> dict[str, Any]:
        """Resolve one parked message: "dismiss" throws it away; "allow" adds the sender to the
        allow-list (future messages flow); "allow_deliver" also re-injects the parked message
        through the NORMAL inbound path — buffer + subscriptions — as if it just arrived.
        """
        item = self.parked.pop(item_id)
        if item is None or item.platform != name:
            return {"ok": False, "error": "unknown item"}
        if action == "dismiss":
            return {"ok": True}
        if action not in ("allow", "allow_deliver"):
            return {"ok": False, "error": f"unknown action: {action}"}
        allowed = self._set_allowed(name, item.user_id, team_id=item.team_id, add=True)
        if not allowed.get("ok"):
            return allowed
        if action == "allow_deliver":
            from ..connectors import MessageEvent, SessionSource

            event = MessageEvent(
                text=item.text,
                source=SessionSource(
                    platform=item.platform,
                    chat_id=item.chat_id,
                    user_id=item.user_id,
                    user_name=item.user_name,
                    chat_name=item.chat_name,
                    chat_type=item.chat_type,
                    thread_id=item.thread_id,
                    team_id=item.team_id,
                ),
            )
            await self._dispatch_inbound(event)
        return {"ok": True}

    async def deliver_to_session(
        self, session_id: str, message: str, *, source: Optional[dict[str, Any]] = None
    ) -> None:
        """Deliver an out-of-band message to a (durable) session — the agent stays resumable
        forever, so this works with no live socket. Busy (mid tool-loop): steer it into the live
        turn at its next step (don't start a colliding run). Idle: run a fresh background turn
        (results persist; if the session is Unattended, any approvals route to the Inbox). Shared
        by self-wake and channel-subscription delivery. `source` is the display-only MessageSource
        sidecar for connector messages (framed `message` stays the model-facing text).
        """
        engine = self.get_engine(session_id)
        if engine is None:
            return
        if not self.try_mark_running(session_id):
            engine.queue_steering(message, source)
            return
        try:
            async for event in engine.run(message, source=source):
                # Stream every event to any socket viewing this session, so a background turn
                # (channel delivery, self-wake, durable resume) is seen live — not just on reselect.
                await self.broadcast_session(
                    session_id, {"type": event.type.value, "data": event.data}
                )
                # A background turn has no user watching to read an inline error: a dead model or
                # tool failure would otherwise vanish. Log it and park it in the dead-letter store.
                if event.type.value == "error":
                    reason = (event.data or {}).get("error", "unknown error")
                    logger.warning(
                        "background turn failed for %s: %s", session_id, reason
                    )
                    self.unrouted.record(session_id, "-", message, reason=reason)
            self.save(session_id, engine)
        except (
            Exception
        ) as exc:  # an unexpected raise out of the turn must not be swallowed
            logger.warning("background turn crashed for %s: %s", session_id, exc)
            self.unrouted.record(session_id, "-", message, reason=str(exc))
            await self.broadcast_session(
                session_id, {"type": "error", "data": {"error": str(exc)}}
            )
        finally:
            self.mark_idle(session_id)
            await self.broadcast_session(session_id, {"type": "turn_done", "data": {}})

    # -- channel subscriptions (inbound messaging) ------------------------------
    async def _dispatch_inbound(self, event) -> None:
        """Route a non-token inbound message. Channel messages are buffered (for catch-up) and
        fanned out to every subscribed session; a DM (or any non-channel) goes to the user-designated
        DM session (delivered like any background turn) or, if none is set, is parked as unrouted.
        """
        src = event.source
        text = getattr(event, "text", "") or ""
        who = src.user_name or src.user_id or "?"
        channel = f"{src.platform}:{src.chat_id}"  # thread-agnostic channel address
        self._note_person(src.platform, src.user_id, src.user_name)
        # Structured sidecar (display-only) built from the resolved identities on the event — the
        # framed text below stays the model-facing `content`; `ms.text` carries the RAW message.
        ms = MessageSource(
            connector=src.platform,
            kind="channel" if src.chat_type in ("channel", "group") else "dm",
            channel_id=src.chat_id,
            channel_name=src.chat_name or src.chat_id,
            sender_id=src.user_id or "",
            sender_name=src.user_name or src.user_id or "?",
            ts=_inbound_epoch(getattr(event, "message_id", None)),
            text=text,
        )
        if src.chat_type in ("channel", "group"):
            self.channel_buffer.record(
                channel, who, text, name=src.chat_name
            )  # buffer all, even unsubscribed
            subs = self.subscriptions.for_channel(channel)
            # §31 mention router: a direct @-mention of the bot outranks the passive fan-out —
            # subscribed sessions must answer it; an unsubscribed channel spawns (or steers)
            # the per-thread coworker session.
            if getattr(event, "mentions_me", False):
                await self._route_mention(event, ms, subs)
                return
            if subs:
                # Chattiness tiers (§31): untagged channel traffic is judgement-only —
                # silence is the default; the must-respond framing is the mention path's.
                msg = (
                    f"💬 New message on {src.chat_name or channel} from {who}: {text}\n"
                    f"(You're subscribed to this channel but were NOT mentioned. Use your "
                    f"judgement: stay silent unless the message clearly concerns your job and "
                    f"a reply adds real value — most channel chatter needs no response from "
                    f'you. If you do reply, use the send_message tool with target "{channel}".)'
                )
                for sub in subs:
                    # Per-session connection hierarchy (§4.3): a session that has muted this
                    # connector skips delivery — the message is still buffered (above) for catch-up.
                    if not self._inbound_connector_allowed(
                        sub.session_id, src.platform
                    ):
                        continue
                    try:
                        await self.deliver_to_session(
                            sub.session_id, msg, source=ms.to_dict()
                        )
                    except Exception:
                        pass
                return
            return  # channel with no subscribers — nobody is listening
        # DM (or any non-channel): route to the designated session, else park it for visibility.
        dm = self.dm_session()
        if dm and self._inbound_connector_allowed(dm, src.platform):
            await self.deliver_to_session(dm, event.tagged_text(), source=ms.to_dict())
        elif dm:
            # Designated, but this session has muted the connector → park rather than deliver.
            self.unrouted.record(
                src.target, who, text, reason="connector muted for DM session"
            )
        else:
            self.unrouted.record(
                src.target, who, text, reason="no DM session designated"
            )

    # -- mention router (§31) ----------------------------------------------------
    async def _route_mention(self, event, ms: MessageSource, subs) -> None:
        """@OpenWorker tagged in a channel. A subscribed (user-connected) coworker owns the channel
        and must answer; otherwise the per-thread coworker session handles it — spawned on the
        first tag, steered by follow-ups (deduped on the thread target)."""
        from ..connectors.base import format_target

        src = event.source
        # Slack semantics: replying to a top-level message threads on THAT message's ts, so a
        # top-level tag (no thread_ts) keys — and is answered — on its own ts.
        thread_key = src.thread_id or getattr(event, "message_id", None)
        thread_target = format_target(src.platform, src.chat_id, thread_key)
        who = src.user_name or src.user_id or "?"
        chan = f"#{src.chat_name}" if src.chat_name else src.chat_id
        if subs:
            # The user connected a coworker to this channel — it answers tags; no spawn.
            msg = (
                f"🔔 You were tagged by {who} in {chan}: {event.text}\n"
                f"(You are subscribed to this channel and were mentioned directly — you must "
                f"respond. Reply in the thread with the send_message tool, target "
                f'"{thread_target}".)'
            )
            for sub in subs:
                if not self._inbound_connector_allowed(sub.session_id, src.platform):
                    continue
                try:
                    await self.deliver_to_session(
                        sub.session_id, msg, source=ms.to_dict()
                    )
                except Exception:
                    pass
            return
        sid = self.mention_sessions.get(thread_target)
        if sid and self.session_store.load(sid) is not None:
            # Follow-up tag in a thread we already own → steer the same session.
            msg = (
                f"💬 Follow-up in your Slack thread ({chan}) from {who}: {event.text}\n"
                f'(Reply in the thread with the send_message tool, target "{thread_target}" '
                f"— replies there are pre-approved.)"
            )
            await self.deliver_to_session(sid, msg, source=ms.to_dict())
            return
        await self._spawn_mention_session(event, ms, thread_target)

    async def _spawn_mention_session(
        self, event, ms: MessageSource, thread_target: str
    ) -> None:
        """First tag in a thread: a NEW visible coworker session that owns the thread. Its
        in-thread replies carry a standing grant (§25 shape, exact-target match) so the
        conversation never stalls on an approval nobody in Slack can see; everything else
        asks as usual (approvals park to the Inbox)."""
        import uuid

        src = event.source
        who = src.user_name or src.user_id or "?"
        chan = f"#{src.chat_name}" if src.chat_name else src.chat_id
        sid = uuid.uuid4().hex
        engine = self.get_engine(sid, agent=self.personas.default_id())
        if engine is None:
            self.unrouted.record(
                src.target, who, event.text, reason="could not spawn mention session"
            )
            return
        # Durable mapping FIRST (a fast follow-up tag mid-turn dedupes into steering),
        # then the live grant; get_engine re-derives it from the store on any rebuild.
        self.mention_sessions.set(
            thread_target, sid, channel=f"{src.platform}:{src.chat_id}"
        )
        engine.permissions.task_rules.setdefault("send_message", set()).add(
            thread_target
        )
        self.save(sid, engine)  # the sessions row must exist before rename/set_origin
        # Title = the ASK first, channel last (owner call 2026-07-14): the text is what
        # varies between sessions, so it gets the truncation budget; the mention token is
        # noise (origin is already told by the From Slack group + icon + origin_label).
        ask = re.sub(r"<@[^>]+>", "", event.text or "")
        ask = " ".join(ask.split())[:48]
        self.session_store.rename(sid, f"{ask} — {chan}" if ask else chan)
        label = chan + (f" · {src.team_id}" if src.team_id else "")
        self.session_store.set_origin(sid, src.platform, label)
        # Up to 6 lines of channel context, minus the tag itself (it's the opening line).
        recent = self.channel_buffer.recent(f"{src.platform}:{src.chat_id}", 7)[:-1]
        context = "\n".join(f"- {m['from']}: {m['text']}" for m in recent)
        opening = (
            f"🔔 You were mentioned on Slack in {chan} by {who}: {event.text}\n\n"
            f"You own this Slack thread. Reply in the thread using the send_message tool "
            f'with target "{thread_target}" — replies to this thread are pre-approved and '
            f"never prompt the user. Anything else (other channels, files, external "
            f"actions) asks for approval as usual. Keep replies concise and "
            f"Slack-appropriate."
            + (f"\n\nRecent channel context:\n{context}" if context else "")
        )
        try:
            await self.deliver_to_session(sid, opening, source=ms.to_dict())
        except Exception:
            logger.exception("mention session %s opening turn failed", sid)

    @staticmethod
    def _wake_message(wake) -> str:
        note = f" (note: {wake.note})" if getattr(wake, "note", "") else ""
        if wake.kind == "completion":
            return (
                f"⏰ Wake — the job `{wake.job_id}` you were waiting on has completed{note}. "
                "Continue where you left off."
            )
        if wake.kind == "event":
            return (
                f"⏰ Wake — the event `{wake.event_key}` you were waiting on has fired{note}. "
                "Continue where you left off."
            )
        return (
            f"⏰ Wake — the timer you set has fired{note}. Continue where you left off."
        )
