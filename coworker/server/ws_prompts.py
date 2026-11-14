"""The interactive-prompt side of the session socket.

Every prompt the runtime raises mid-turn — a tool approval, an ask_user question, a
tool install, a folder grant, a plan, a team roster, a work-item split — is parked as
an Inbox item and awaited through `inbox.wait`. That indirection is the whole point:
the answer can arrive from this live socket, from the cross-session Inbox, from a bound
channel, or after a restart, and the agent simply stays blocked until it does.

`PromptGateway` is one connection's view of that: it knows the manager, the socket
(for the inline card), the session and the agent, and nothing else — no engine, no
turn loop. `visibility` decides only where an item SHOWS (Unattended → the cross-session
Inbox, attended → inline), never whether it is durable.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from fastapi import WebSocket

from .manager import SessionManager
from ..engine import ApprovalOutcome
from ..inbox import VIS_INBOX, VIS_INLINE, args_preview
from .. import toolchain


def parse_json(s: str) -> dict[str, Any]:
    """Parse a structured Inbox resolution (directory/plan carry their reply as a JSON string)."""
    try:
        v = json.loads(s) if s else {}
        return v if isinstance(v, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


class PromptGateway:
    """One socket's prompt plumbing: park the item, wait, keep the place in the inbox."""

    def __init__(
        self,
        manager: SessionManager,
        ws: WebSocket,
        session_id: str,
        agent: str,
    ) -> None:
        self.manager = manager
        self.ws = ws
        self.session_id = session_id
        self.agent = agent

    def visibility(self) -> str:
        return (
            VIS_INBOX
            if self.manager.unattended.is_unattended(self.session_id)
            else VIS_INLINE
        )

    async def mirror(self, item) -> None:
        # Unattended items mirror to a bound channel as buttons (see mirror_inbox_item).
        await self.manager.mirror_inbox_item(item)

    def route(self) -> str:
        return self.manager.inbox_routing.route_for(self.session_id, self.agent)

    async def approval(self, request) -> ApprovalOutcome:
        # The engine has already emitted PERMISSION_REQUIRED (the live inline card). Park the
        # item so the answer can also come from the Inbox / a reconnect / after a restart.
        item = self.manager.inbox.add_approval(
            self.session_id,
            f"Run `{request.tool_name}`?",
            body="\n".join(
                p
                for p in (
                    (getattr(request, "reason", "") or "").strip(),
                    args_preview(getattr(request, "arguments", None)),
                )
                if p
            ),
            inbox=self.route(),
            visibility=self.visibility(),
            # Automation-run context (manual "Run now" rides this socket): lets the
            # card offer the task-persistent "Allow every time" (§25). {} elsewhere.
            data=self.manager.approval_prompt_data(self.session_id, request),
            tool_call_id=getattr(request, "tool_call_id", None),
        )
        if (
            item.state == "pending"
        ):  # freshly raised (not a durable-resume re-raise)
            self.manager.persist_session(
                self.session_id
            )  # the pending tool call is now on disk
            if item.visibility == VIS_INBOX:
                await self.mirror(item)
        resolution = await self.manager.inbox.wait(item.id)
        # Accept every vocabulary: the live card sends once/always_tool/always_command/
        # always_task/deny; the Inbox / a channel send allow/always/deny.
        return self.manager.approval_outcome(resolution, request, self.session_id)

    async def question(self, args: dict, tool_call_id=None) -> dict:
        # ask_user (engine does NOT emit the event — we do, only when attended).
        from ..tools.ask import answer_result, question_item_fields

        fields = question_item_fields(args)
        if fields is None:  # engine guards too; belt-and-braces
            return {"answer": "", "error": "no question"}
        item = self.manager.inbox.add_question(
            self.session_id,
            inbox=self.route(),
            visibility=self.visibility(),
            tool_call_id=tool_call_id,
            **fields,
        )
        if item.state == "pending":
            self.manager.persist_session(self.session_id)
            if item.visibility == VIS_INBOX:
                await self.mirror(item)
            else:
                await self.ws.send_json(
                    {
                        "type": "questionrequested",
                        "data": {
                            "question": item.title,
                            "options": item.options,
                            "allow_text": item.allow_text,
                            "multi": item.multi,
                            "header": item.header,
                            "questions": item.questions,
                        },
                    }
                )
        return answer_result(item.questions, await self.manager.inbox.wait(item.id))

    async def tool_request(self, args: dict, tool_call_id=None) -> dict:
        """Park a TOOL_REQUESTED prompt, then install the PINNED build if approved.

        Declining is a first-class outcome: the agent is told to fall back and disclose
        the gap rather than drop the check (OPE-85). Installs only ever come from the
        pinned registry with its digest verified — an approval is consent to install
        THAT artifact, not licence to fetch whatever a prompt asked for.
        """
        name = str(args.get("name", "")).strip()
        info = toolchain.describe(name)
        if not info:
            # Not in the pinned catalog: never show an install card that can only
            # end in "no pinned build" AFTER approval (owner-hit 2026-08-20 — agents
            # routed ordinary brew/pip installs through the card). Steer to the
            # shell, which has its own approval flow.
            return {
                "installed": False,
                "error": (
                    f"'{name}' is not in the pinned tool catalog "
                    f"({', '.join(sorted(toolchain.MANAGED))}). Install it yourself "
                    "with the shell (brew/pip/…, subject to the normal command "
                    "approval), or proceed without it and note the gap."
                ),
            }
        item = self.manager.inbox.add_toolrequest(
            self.session_id,
            f"Install {name}?" if name else "Install a tool?",
            body=str(args.get("reason", "")),
            inbox=self.route(),
            visibility=self.visibility(),
            data={
                "tool": name,
                "installable": bool(info and info.get("installable")),
                "version": (info or {}).get("version", ""),
                "summary": (info or {}).get("summary", ""),
                "url": (info or {}).get("url", ""),
                "source": (info or {}).get("source", ""),
            },
            tool_call_id=tool_call_id,
        )
        if item.state == "pending":
            self.manager.persist_session(self.session_id)
            if item.visibility == VIS_INBOX:
                await self.mirror(item)
        resp = parse_json(await self.manager.inbox.wait(item.id))  # {approved}
        if not resp.get("approved"):
            return {
                "installed": False,
                "reason": "the user declined to install it",
            }
        if not info or not info.get("installable"):
            return {
                "installed": False,
                "error": f"no pinned build of {name} is available for this platform",
            }
        try:
            path = await asyncio.to_thread(toolchain.install, name)
        except Exception as exc:  # noqa: BLE001 - surfaced to the agent verbatim
            return {"installed": False, "error": str(exc)}
        return {"installed": True, "path": path, "version": info["version"]}

    async def directory(self, args: dict, tool_call_id=None) -> dict:
        # The engine has already emitted DIRECTORY_REQUESTED. Park, await, then apply the grant.
        item = self.manager.inbox.add_directory(
            self.session_id,
            "Grant access to a folder?",
            body=str(args.get("reason", "")),
            inbox=self.route(),
            visibility=self.visibility(),
            data={
                "path": str(args.get("path", "")),
                "writable": bool(args.get("writable", False)),
                "primary": bool(args.get("primary", False)),
            },
            tool_call_id=tool_call_id,
        )
        if item.state == "pending":
            self.manager.persist_session(self.session_id)
            if item.visibility == VIS_INBOX:
                await self.mirror(item)
        resp = parse_json(
            await self.manager.inbox.wait(item.id)
        )  # {granted, path, writable}
        if not resp.get("granted"):
            return {"granted": False, "reason": "the user declined the request"}
        path = (resp.get("path") or args.get("path") or "").strip()
        if not path:
            return {"granted": False, "error": "no directory was provided"}
        writable = bool(resp.get("writable", args.get("writable", False)))
        if bool(args.get("primary", False)):
            # Root promotion (workspace-scratch-design.md §5) — the shell cd inside
            # is blocking, keep it off the event loop.
            promo = await asyncio.to_thread(
                self.manager.promote_workspace, self.session_id, path
            )
            if promo.get("ok"):
                return {
                    "granted": True,
                    "path": promo["path"],
                    "writable": True,
                    "primary": True,
                    "note": (
                        "This folder is now the session's workspace. For the rest "
                        "of this turn, address it by absolute path."
                    ),
                }
            # Promotion refused (e.g. the session already has a workspace): still
            # honor the grant as a plain additional folder.
            res = self.manager.add_root(self.session_id, path, writable)
            if not res.get("ok"):
                return {
                    "granted": False,
                    "error": promo.get("error", "could not promote"),
                }
            return {
                "granted": True,
                "path": path,
                "writable": writable,
                "primary": False,
                "note": promo.get("error", "")
                + " — granted as an additional folder instead",
            }
        res = self.manager.add_root(self.session_id, path, writable)
        if not res.get("ok"):
            return {
                "granted": False,
                "error": res.get("error", "could not grant access"),
            }
        primary = next(
            (
                r
                for r in res.get("roots", [])
                if r.get("path")
                and Path(r["path"]).expanduser().resolve()
                == Path(path).expanduser().resolve()
            ),
            None,
        )
        return {
            "granted": True,
            "path": (primary or {}).get("path", path),
            "writable": writable,
        }

    async def plan(self, args: dict, tool_call_id=None) -> dict:
        # The engine has already emitted PLAN_PROPOSED. Park, await the verdict.
        item = self.manager.inbox.add_plan(
            self.session_id,
            "Approve the plan?",
            body=str(args.get("plan", "")),
            inbox=self.route(),
            visibility=self.visibility(),
            tool_call_id=tool_call_id,
        )
        if item.state == "pending":
            self.manager.persist_session(self.session_id)
            if item.visibility == VIS_INBOX:
                await self.mirror(item)
        resp = parse_json(
            await self.manager.inbox.wait(item.id)
        )  # {approved, mode, feedback}
        if not resp.get("approved"):
            return {
                "approved": False,
                "feedback": resp.get("feedback") or "the user rejected the plan",
            }
        return {"approved": True, "mode": resp.get("mode") or "interactive"}

    async def team(self, args: dict, tool_call_id=None) -> dict:
        # The staffing gate. The engine already emitted TEAM_PROPOSED; park an
        # Inbox item as the durable resolution vehicle, wait for the verdict, and
        # on approval PRE-SPAWN the team (create_team fails closed on non-worker
        # personas, so a bad roster reads as a rejection with the reason).
        members = args.get("members") or []
        roster = "\n".join(
            f"- {m.get('persona', '?')}"
            + (f" · {m['model']}" if m.get("model") else "")
            + (f" — {m['reason']}" if m.get("reason") else "")
            for m in members
            if isinstance(m, dict)
        )
        item = self.manager.inbox.add_plan(
            self.session_id,
            "Create this team?",
            body=roster,
            inbox=self.route(),
            visibility=self.visibility(),
            tool_call_id=tool_call_id,
        )
        if item.state == "pending":
            self.manager.persist_session(self.session_id)
            if item.visibility == VIS_INBOX:
                await self.mirror(item)
        resp = parse_json(await self.manager.inbox.wait(item.id))
        if not resp.get("approved"):
            return {
                "approved": False,
                "feedback": resp.get("feedback") or "the user declined this roster",
            }
        # The gate checkbox is the USER's call: an explicit enable_chat in the
        # response overrides whatever the lead proposed.
        enable_chat = bool(
            resp["enable_chat"]
            if "enable_chat" in resp
            else args.get("enable_chat", False)
        )
        return self.manager.create_team(
            self.session_id,
            [m for m in members if isinstance(m, dict)],
            enable_chat=enable_chat,
        )

    async def items(self, args: dict, tool_call_id=None) -> dict:
        # The decomposition gate. TEAMS-flavored sibling of plan_approver:
        # park a durable Inbox item, wait, and on approval create the items.
        items = args.get("items") or []
        body = "\n".join(
            f"- {i.get('title', '?')} — Done when: {i.get('criteria', '?')}"
            for i in items
            if isinstance(i, dict)
        )
        item = self.manager.inbox.add_plan(
            self.session_id,
            "Approve the proposed work items?",
            body=body,
            inbox=self.route(),
            visibility=self.visibility(),
            tool_call_id=tool_call_id,
        )
        if item.state == "pending":
            self.manager.persist_session(self.session_id)
            if item.visibility == VIS_INBOX:
                await self.mirror(item)
        resp = parse_json(await self.manager.inbox.wait(item.id))
        if not resp.get("approved"):
            return {
                "approved": False,
                "feedback": resp.get("feedback") or "the user declined the split",
            }
        return self.manager.board_create_items(
            self.session_id, [i for i in items if isinstance(i, dict)]
        )

