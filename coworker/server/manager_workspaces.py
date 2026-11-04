"""Workspaces cluster split out of manager.py (manager-split refactor).

Mixin methods only; shared state lives on the SessionManager facade (self.*). Module-level
imports are limited to what the moved code references; everything else resolves via self.
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Any, Optional

from ..config import load_config, workspace_allowed_commands
from ..engine import TurnEngine
from ..projects import project_key, project_label, project_presence
from ..roots import RootDir
from ..sessions import SessionRecord
from ..workspace_trust import WorkspaceTrustStore
from .manager_helpers import _git_branch


class WorkspacesMixin:
    # -- workspaces -------------------------------------------------------------
    def open_workspace(self, path: str, *, create: bool = False) -> dict[str, Any]:
        resolved = Path(path).expanduser()
        if resolved.exists() and not resolved.is_dir():
            return {"path": str(resolved), "ok": False, "error": "not a directory"}
        if not resolved.exists():
            if not create:
                return {
                    "path": str(resolved),
                    "ok": False,
                    "error": "folder does not exist",
                }
            try:
                resolved.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                return {"path": str(resolved), "ok": False, "error": str(exc)}
        resolved = resolved.resolve()
        self.session_store.touch_workspace(str(resolved))
        return {
            "path": str(resolved),
            "ok": True,
            "git_branch": _git_branch(resolved),
            "command_trust": self.workspace_command_trust(resolved),
        }

    def workspace_command_trust(self, path: str | Path) -> dict[str, Any]:
        if not str(path).strip():
            return {
                "workspace": "",
                "requested_commands": [],
                "trusted": False,
                "required": False,
            }
        canonical = WorkspaceTrustStore.canonical(path)
        commands = (
            workspace_allowed_commands(canonical)
            if Path(canonical).is_dir()
            else []
        )
        trusted = self.workspace_trust.is_trusted(canonical)
        return {
            "workspace": canonical,
            "requested_commands": commands,
            "trusted": trusted,
            "required": bool(commands and not trusted),
        }

    def _mcp_workspace_trusted(self, workspace: Optional[str | Path]) -> bool:
        """Whether workspace `.coworker/mcp.json` may be loaded (#213).

        Same consent boundary as repository ``allowed_commands``: an untrusted
        clone must not define stdio processes that spawn at session open.
        """
        return bool(workspace and self.workspace_trust.is_trusted(workspace))

    def set_workspace_trust(
        self, path: str | Path, *, trusted: bool
    ) -> dict[str, Any]:
        if not str(path).strip():
            return {"ok": False, "error": "workspace path is required"}
        candidate = Path(path).expanduser()
        if trusted and not candidate.is_dir():
            return {"ok": False, "error": "workspace is not a directory"}
        canonical = self.workspace_trust.set_trusted(candidate, trusted)
        effective = load_config(
            canonical, workspace_trusted=trusted
        ).allowed_commands
        # Apply trust/revocation immediately to live sessions rooted at this exact path.
        for engine in self._engines.values():
            engine_workspace = str(
                (getattr(engine, "audit_context", {}) or {}).get("workspace", "")
            )
            if engine_workspace and WorkspaceTrustStore.canonical(
                engine_workspace
            ) == canonical:
                engine.permissions.allowed_commands = list(effective)
        return {
            "ok": True,
            **self.workspace_command_trust(canonical),
        }

    def trusted_workspaces(self) -> list[dict[str, Any]]:
        return [
            {
                **self.workspace_command_trust(path),
                "exists": Path(path).is_dir(),
            }
            for path in self.workspace_trust.list()
        ]

    def recent_workspaces(self) -> list[dict[str, Any]]:
        """Recent real projects for the folder gate. Per-conversation scratch dirs are
        excluded — they're workspaces to the session store, but never something a user
        should re-open as a 'project'."""
        scratch = self.scratch_base().resolve()
        out = []
        for path in self.session_store.recent_workspaces():
            p = Path(path)
            try:
                if p.resolve().is_relative_to(scratch):
                    continue
            except OSError:
                pass
            out.append({"path": path, "name": p.name, "exists": p.is_dir()})
        return out

    DEFAULT_SCRATCH_BASE = "~/OpenWorker"

    def scratch_base(self) -> Path:
        """Common area for per-conversation scratch directories. Configurable via prefs;
        the env override keeps tests (and any sandboxed run) out of the real home dir —
        universal scratch means every session provisions here, not just orphan ones."""
        base = (
            self._prefs.get("scratch_base")
            or os.environ.get("COWORKER_SCRATCH_BASE")
            or self.DEFAULT_SCRATCH_BASE
        )
        return Path(base).expanduser()

    def _provision_scratch(self, session_id: str) -> str:
        """Create (idempotently) and return this conversation's scratch directory."""
        d = self.scratch_base() / session_id
        d.mkdir(parents=True, exist_ok=True)
        return str(d.resolve())

    _SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")

    def is_temp_workspace(self, path: Optional[str]) -> bool:
        """True when `path` is a per-conversation temporary directory (lives under the
        scratch base). The GUI uses this to label the folder "Temporary folder" instead
        of exposing its raw path."""
        if not path:
            return False
        try:
            return (
                Path(path).expanduser().resolve().is_relative_to(self.scratch_base().resolve())
            )
        except OSError:
            return False

    def provision_temp_workspace(self, session_id: str, *, git: bool = True) -> dict[str, Any]:
        """UX-029 "Start in a temporary folder": create the conversation's temporary
        directory at SEND time (not connect) and, for code-family work, make git ready.
        Idempotent — re-sending against an existing dir is a no-op."""
        if not self._SESSION_ID_RE.match(session_id or "") or session_id in {".", ".."}:
            return {"ok": False, "error": "invalid session id"}
        path = self._provision_scratch(session_id)
        if git and not (Path(path) / ".git").is_dir():
            try:
                subprocess.run(
                    ["git", "init", "-q"],
                    cwd=path,
                    capture_output=True,
                    timeout=10,
                    check=False,
                )
            except (OSError, subprocess.SubprocessError):
                pass  # no git on PATH → still a usable folder, just not a repo
        return {"ok": True, "path": path, "git": (Path(path) / ".git").is_dir()}

    def save_temp_as_project(self, session_id: str, dest: str) -> dict[str, Any]:
        """UX-029 "Save as project…": move a session's temporary folder to a real
        location and rebind the session there. The cached engine is dropped so the next
        connect rebuilds against the new path — callers must reconnect after this."""
        if not dest or not dest.strip():
            return {"ok": False, "error": "no destination folder"}
        record = self.session_store.load(session_id)
        src = record.workspace if record and record.workspace else None
        if not src:
            engine = self._engines.get(session_id)
            executor = getattr(engine, "executor", None) if engine else None
            src = str(executor.cwd) if executor else None
        if not src or not self.is_temp_workspace(src) or not Path(src).is_dir():
            return {"ok": False, "error": "this session is not in a temporary folder"}
        if self.is_running(session_id):
            return {"ok": False, "error": "wait for the current task to finish first"}
        d = Path(dest).expanduser()
        if d.exists():
            if not d.is_dir() or any(d.iterdir()):
                return {"ok": False, "error": "destination must be a new or empty folder"}
            d.rmdir()  # shutil.move into an existing dir would nest src inside it
        try:
            d.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(src, str(d))
        except OSError as e:
            return {"ok": False, "error": f"could not move the folder: {e}"}
        new_path = str(d.resolve())
        if record:
            record.workspace = new_path
            self.session_store.save(record)
        self._engines.pop(session_id, None)
        self.session_store.touch_workspace(new_path)
        return {"ok": True, "path": new_path}

    def resolve_workspace(self, requested: Optional[str]) -> Optional[str]:
        if requested:
            p = Path(requested).expanduser()
            if p.is_dir():
                return str(p.resolve())
            return None
        return self.default_workspace

    # -- engines ----------------------------------------------------------------
    def engine_workspace(
        self, session_id: str, *, workspace: Optional[str] = None, agent: str = "code"
    ) -> Optional[str]:
        """The workspace `get_engine` would bind — for prepping MCP tools beforehand."""
        record = self.session_store.load(session_id)
        if record:
            return record.workspace or None
        return self.resolve_workspace(workspace)

    def _extra_roots_of(
        self, engine: TurnEngine, session_id: str
    ) -> list[dict[str, Any]]:
        """User/agent-added folders = the engine's roots minus the primary (index 0) AND
        the session's provisioned scratch root. Persisting the scratch as an "extra"
        would re-add it as a plain folder on every rebuild (universal scratch made
        index-0-only slicing wrong for dual-root sessions)."""
        roots = getattr(engine, "roots", None) or []
        scratch = (self.scratch_base() / session_id).expanduser()
        try:
            scratch = scratch.resolve()
        except OSError:
            pass
        return [
            {"path": str(r.path), "writable": bool(r.writable), "label": r.label}
            for r in roots[1:]
            if r.path != scratch
        ]

    # -- session roots (orphan Cowork: scratch + added folders) ------------------
    def get_roots(self, session_id: str) -> list[dict[str, Any]]:
        """The directories this session can touch: primary scratch first, then added folders.
        Reads the live engine when one is running; otherwise reconstructs from persisted state.
        """
        engine = self._engines.get(session_id)
        if engine is not None and getattr(engine, "roots", None):
            return [
                {
                    "path": str(r.path),
                    "writable": bool(r.writable),
                    "label": r.label,
                    "primary": i == 0,
                    "exists": r.path.is_dir(),
                }
                for i, r in enumerate(engine.roots)
            ]
        record = self.session_store.load(session_id)
        primary = (
            record.workspace
            if record and record.workspace
            else self._provision_scratch(session_id)
        )
        extra = (record.extra_roots if record else []) or []
        primary_is_scratch = self.is_temp_workspace(primary)
        out = [
            {
                "path": primary,
                "writable": True,
                "label": "scratch" if primary_is_scratch else "workspace",
                "primary": True,
                "exists": Path(primary).is_dir(),
            }
        ]
        # Universal scratch: a real-folder session also carries its provisioned scratch
        # root (mirrors the engine-side shape so a cold read matches a live one).
        if not primary_is_scratch and self._SESSION_ID_RE.match(session_id or ""):
            scratch = self.scratch_base() / session_id
            out.append(
                {
                    "path": str(scratch.expanduser().resolve()),
                    "writable": True,
                    "label": "scratch",
                    "primary": False,
                    "exists": scratch.is_dir(),
                }
            )
        for r in extra:
            p = str(r.get("path", ""))
            out.append(
                {
                    "path": p,
                    "writable": bool(r.get("writable", False)),
                    "label": r.get("label") or Path(p).name,
                    "primary": False,
                    "exists": Path(p).is_dir(),
                }
            )
        return out

    def promote_workspace(self, session_id: str, path: str) -> dict[str, Any]:
        """Root promotion (workspace-scratch-design.md §5): adopt `path` as the session's
        primary workspace. One-way and once — only while the primary is still the
        provisioned scratch; a session that already has a real workspace is never
        re-pointed. Mutates the live session (roots + shell cwd), persists, and marks
        the engine for a post-turn rebuild."""
        p = Path(path).expanduser()
        if not p.is_dir():
            return {"ok": False, "error": f"not a directory: {path}"}
        resolved = p.resolve()
        engine = self._engines.get(session_id)
        if engine is None:
            return {"ok": False, "error": "no live session to promote"}
        executor = getattr(engine, "executor", None)
        current = str(executor.cwd) if executor is not None else None
        if not current or not self.is_temp_workspace(current):
            return {"ok": False, "error": "this session already has a workspace"}
        roots = getattr(engine, "roots", None)
        if roots is None:
            return {"ok": False, "error": "this session has no directory list"}
        # Shared list: permissions, file tools, and the context injector see the new
        # primary immediately. The old scratch primary stays as the scratch root.
        roots[:] = [
            RootDir(path=resolved, writable=True, label="workspace"),
            *[r for r in roots if r.path != resolved],
        ]
        try:
            # Move the live shell too — save() derives the persisted workspace from the
            # executor's cwd, so this is also what makes the promotion durable.
            res = executor.run(f"cd {shlex.quote(str(resolved))}", timeout=15)
            if res.get("exit_code") != 0:
                executor.cwd = str(resolved)
        except Exception:
            executor.cwd = str(resolved)  # a respawned shell starts there
        self.save(session_id, engine)
        self.session_store.touch_workspace(str(resolved))
        self._promotion_rebuild.add(session_id)
        return {"ok": True, "path": str(resolved), "roots": self.get_roots(session_id)}

    def add_root(
        self, session_id: str, path: str, writable: bool = False
    ) -> dict[str, Any]:
        """Grant the session access to another folder (read-only or read-write). Mutates the live
        engine in place when running (file tools + permissions + context see it immediately) and
        persists it so a later resume still has it."""
        p = Path(path).expanduser()
        if not p.is_dir():
            return {"ok": False, "error": f"not a directory: {path}"}
        resolved = p.resolve()
        engine = self._engines.get(session_id)
        if engine is not None and getattr(engine, "roots", None) is not None:
            if any(r.path == resolved for r in engine.roots):
                # already present: just update its access level
                for r in engine.roots:
                    if r.path == resolved:
                        r.writable = bool(writable)
            else:
                engine.roots.append(RootDir(path=resolved, writable=bool(writable)))
            self.session_store.set_extra_roots(
                session_id, self._extra_roots_of(engine, session_id)
            )
        else:
            # A brand-new conversation has no record yet (it's only saved after the first turn) —
            # create one now so set_extra_roots has a row to update and the folder survives.
            if self.session_store.load(session_id) is None:
                self.session_store.save(
                    SessionRecord(
                        session_id=session_id,
                        workspace=self._provision_scratch(session_id),
                        model=self.model,
                        mode=self.mode.value,
                        messages=[],
                        agent="cowork",  # folder access is a Cowork affordance
                    )
                )
            session_scratch = str((self.scratch_base() / session_id).expanduser().resolve())
            extra = [
                r
                for r in self.get_roots(session_id)
                if not r["primary"] and r["path"] != session_scratch
            ]
            extra = [r for r in extra if Path(r["path"]).resolve() != resolved]
            extra.append(
                {
                    "path": str(resolved),
                    "writable": bool(writable),
                    "label": resolved.name,
                }
            )
            self.session_store.set_extra_roots(
                session_id,
                [
                    {
                        "path": r["path"],
                        "writable": r["writable"],
                        "label": r.get("label", ""),
                    }
                    for r in extra
                ],
            )
        self.session_store.touch_workspace(str(resolved))
        # Grant-time notice (pass 20): if this directory's project already has
        # memory or a board, say so — one line, pointer only, to agent + user.
        notice = self._project_notice(str(resolved))
        engine = self._engines.get(session_id)
        if notice and engine is not None:
            engine._append_notice("project_presence", notice)
        return {"ok": True, "roots": self.get_roots(session_id), "notice": notice}

    def _project_notice(self, path: str) -> Optional[str]:
        """One-line presence pointer for a newly granted directory, or None."""
        try:
            key = project_key(path)
            pres = project_presence(
                key, memory_store=self.memory_store, team_store=self.team_store
            )
        except Exception:
            return None
        parts = []
        if pres["memories"]:
            parts.append(f"project memory ({pres['memories']} entries)")
        if pres["board_items"]:
            parts.append("a board")
        if not parts:
            return None
        label = project_label(key)["label"]
        return f"“{label}” already has {' and '.join(parts)} — bind it by name or start a session there to use it."

    def remove_root(self, session_id: str, path: str) -> dict[str, Any]:
        """Revoke a previously-added folder. The primary scratch cannot be removed."""
        resolved = Path(path).expanduser().resolve()
        engine = self._engines.get(session_id)
        if engine is not None and getattr(engine, "roots", None):
            if engine.roots and engine.roots[0].path == resolved:
                return {
                    "ok": False,
                    "error": "cannot remove the primary scratch directory",
                }
            engine.roots[:] = [r for r in engine.roots if r.path != resolved]
            self.session_store.set_extra_roots(
                session_id, self._extra_roots_of(engine, session_id)
            )
        else:
            current = self.get_roots(session_id)
            if (
                current
                and current[0]["primary"]
                and Path(current[0]["path"]).resolve() == resolved
            ):
                return {
                    "ok": False,
                    "error": "cannot remove the primary scratch directory",
                }
            session_scratch = (self.scratch_base() / session_id).expanduser().resolve()
            extra = [
                r
                for r in current
                if not r["primary"]
                and Path(r["path"]).resolve() not in (resolved, session_scratch)
            ]
            self.session_store.set_extra_roots(
                session_id,
                [
                    {
                        "path": r["path"],
                        "writable": r["writable"],
                        "label": r.get("label", ""),
                    }
                    for r in extra
                ],
            )
        return {"ok": True, "roots": self.get_roots(session_id)}

    def _scratch_workspace_error(self, workspace: Any) -> Optional[dict[str, Any]]:
        """Refuse skill WRITES into a per-conversation scratch dir — a skill saved there is
        stranded in a throwaway folder. Backend chokepoint: guards every entry path (UI,
        REST, future import), not just the flows the GUI happens to gate."""
        if not workspace:
            return None
        try:
            ws = Path(str(workspace)).expanduser().resolve()
            if ws.is_relative_to(self.scratch_base().resolve()):
                return {
                    "ok": False,
                    "error": (
                        "That folder is a temporary session space — skills saved there "
                        "would be lost. Save it globally or pick a real project."
                    ),
                }
        except OSError:
            pass
        return None
