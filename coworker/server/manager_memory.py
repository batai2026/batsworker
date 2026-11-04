"""Memory cluster split out of manager.py (manager-split refactor).

Mixin methods only; shared state lives on the SessionManager facade (self.*). Module-level
imports are limited to what the moved code references; everything else resolves via self.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Optional

from ..memory import Scope
from ..projects import project_key, project_label

_SCOPES = {s.value for s in Scope}


class MemoryMixin:
    def _memory_saved_notifier(self, session_id: str):
        """MEMORY-SPEC §5.1: push the memory_saved event that powers the GUI's save
        toast ("I'll remember that — … [Undo]"). Best-effort by design: `remember` may
        run with no socket attached (background runs) or off the loop thread — a lost
        toast never fails the save."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        def notify(item, previous=None) -> None:
            if loop is None or not loop.is_running():
                return
            payload = {
                "type": "memory_saved",
                "data": {
                    "id": item.id,
                    "scope": item.scope.value,
                    "summary": item.summary or "",
                    "content": item.content,
                    # Set when this was an EDIT of an existing memory: the surface says
                    # "I've updated what I remember" and Undo restores this text.
                    "previous": previous or "",
                },
            }
            try:
                asyncio.run_coroutine_threadsafe(
                    self.broadcast_session(session_id, payload), loop
                )
            except RuntimeError:
                pass

        return notify

    # -- project bindings (pass 20 / UX-044) -------------------------------------

    def project_menu(self, session_id: str, kind: str) -> dict[str, Any]:
        """The submenu payload: the session's derived project (pinned, labeled per
        the UX-044 rules) + named entries MRU-ordered. The GUI shows 5 and grows a
        filter at 6+; the full named list ships so the filter reaches everything."""
        record = self.session_store.load(session_id)
        ws = (record.workspace if record else None) or self.default_workspace
        derived_key = project_key(ws) if ws else None
        names = self.session_store.names()
        bound = ((record.bindings if record else {}) or {}).get(kind)
        named = names.list(kind)
        return {
            "kind": kind,
            "bound": bound,
            "derived": (
                {**project_label(derived_key), "key": derived_key}
                if derived_key
                else None
            ),
            "named": [{"name": n["name"], "key": n["key"]} for n in named],
        }

    def set_binding(
        self, session_id: str, kind: str, name: Optional[str]
    ) -> dict[str, Any]:
        """Bind (or unbind, name=None) a named project for this session. Takes
        effect at the next engine build — the running engine keeps the knowledge
        it started with (same doctrine as memory deletions)."""
        if kind not in ("memory", "board"):
            return {"ok": False, "error": f"unknown kind {kind!r}"}
        if self.is_running(session_id):
            return {"ok": False, "error": "wait for the current task to finish first"}
        if name and self.session_store.names().resolve(kind, name) is None:
            return {"ok": False, "error": f"no {kind} named {name!r}"}
        record = self.session_store.load(session_id)
        bindings = dict((record.bindings if record else {}) or {})
        if name:
            bindings[kind] = name
        else:
            bindings.pop(kind, None)
        if record is None:
            return {"ok": False, "error": "unknown session"}
        self.session_store.set_bindings(session_id, bindings)
        # Rebind applies from the next engine build; drop the cached engine so the
        # next turn rebuilds with the new key (messages persist via the record).
        self._engines.pop(session_id, None)
        return {"ok": True, "bindings": bindings}

    def name_current_project(
        self, session_id: str, kind: str, name: str
    ) -> dict[str, Any]:
        """Give the session's derived project a user name (UX-044 'Name current…')."""
        record = self.session_store.load(session_id)
        ws = (record.workspace if record else None) or self.default_workspace
        if not ws:
            return {"ok": False, "error": "session has no workspace"}
        try:
            entry = self.session_store.names().name_current(
                kind, name, project_key(ws)
            )
        except ValueError as e:
            return {"ok": False, "error": str(e)}
        return {"ok": True, **entry}

    def list_memory(self) -> list[dict[str, Any]]:
        return [
            {
                "id": m.id,
                "scope": m.scope.value,
                "content": m.content,
                "summary": m.summary or "",
                "created_at": m.created_at or "",
            }
            for m in self.memory_store.list()
        ]

    def add_memory(
        self, content: str, scope: str = "workspace", workspace: Optional[str] = None
    ) -> dict[str, Any]:
        content = (content or "").strip()
        if not content:
            return {"ok": False, "error": "content required"}
        chosen = Scope(scope) if scope in _SCOPES else Scope.WORKSPACE
        ws = self.resolve_workspace(workspace) if chosen is Scope.WORKSPACE else None
        item = self.memory_store.add(content, scope=chosen, workspace=ws)
        return {"id": item.id, "scope": item.scope.value, "content": item.content}

    def update_memory(self, item_id: int, content: str) -> dict[str, Any]:
        """Edit-in-place from the memory screen (§5.3). The user rewrote the fact, so
        the stale one-line summary is cleared rather than left contradicting it."""
        content = (content or "").strip()
        if not content:
            return {"ok": False, "error": "content required"}
        item = self.memory_store.update(item_id, content, summary="")
        if item is None:
            return {"ok": False, "error": f"no memory with id {item_id}"}
        return {"ok": True, "id": item.id, "content": item.content}

    def delete_memory(self, item_id: int) -> dict[str, Any]:
        """Row delete on the memory screen — and the toast's Undo (§5.1)."""
        if self.memory_store.delete(item_id):
            return {"ok": True, "id": item_id}
        return {"ok": False, "error": f"no memory with id {item_id}"}

    def delete_all_memory(self) -> dict[str, Any]:
        return {"ok": True, "deleted": self.memory_store.delete_all()}

    def get_memory_settings(self) -> dict[str, Any]:
        return self.memory_settings.snapshot()

    def set_memory_settings(
        self, enabled: Optional[bool] = None, user_rules: Optional[str] = None
    ) -> dict[str, Any]:
        return self.memory_settings.set(enabled=enabled, user_rules=user_rules)

    # -- plain-text memory (export / reveal / re-import) ---------------------------

    def _memory_export_dir(self, workspace: Optional[str]) -> Path:
        """Where MEMORY.md lives: the requested workspace when valid, else a
        user-visible folder under the state dir (no workspace → global memory)."""
        from ..secrets import state_dir as _state_dir

        if workspace:
            ws = Path(workspace).expanduser().resolve()
            if ws.is_dir():
                return ws
        return _state_dir() / "memory"

    def export_memory(self, workspace: Optional[str] = None) -> dict[str, Any]:
        """Write the memory store as a readable MEMORY.md and return its path."""
        from ..memory.markdown import MEMORY_FILENAME, memory_to_markdown

        folder = self._memory_export_dir(workspace)
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / MEMORY_FILENAME
        path.write_text(
            memory_to_markdown(self.memory_store.list()), encoding="utf-8"
        )
        return {"ok": True, "path": str(path)}

    def import_memory(self, workspace: Optional[str] = None) -> dict[str, Any]:
        """Re-import the edited MEMORY.md — replace each section's entries so the
        edited file becomes the source of truth for the scopes it names."""
        from ..memory.markdown import (
            MEMORY_FILENAME,
            parse_memory_markdown,
        )

        folder = self._memory_export_dir(workspace)
        path = folder / MEMORY_FILENAME
        if not path.is_file():
            return {
                "ok": False,
                "error": f"no memory file at {path} — export it first",
            }
        rows = parse_memory_markdown(path.read_text(encoding="utf-8"))
        replaced: dict[str, int] = {}
        for row in rows:
            scope = Scope(row["scope"])
            ws = row.get("workspace") or None
            if scope is Scope.WORKSPACE:
                ws = self.resolve_workspace(ws) if ws else None
            key = (scope.value, ws)
            if key not in replaced:
                # Replace this scope's existing entries first (delete_all is
                # scoped; workspace rows share the WORKSPACE scope so we delete
                # by scope+workspace via list+delete of matching rows).
                for existing in self.memory_store.list(
                    scope=scope, workspace=ws
                ):
                    self.memory_store.delete(existing.id)
                replaced[key] = 0
            self.memory_store.add(
                row["content"], scope=scope, workspace=ws
            )
            replaced[key] += 1
        return {
            "ok": True,
            "replaced": [
                {"scope": k[0], "workspace": k[1], "count": v}
                for k, v in replaced.items()
            ],
        }

    def reveal_memory(self, workspace: Optional[str] = None) -> dict[str, Any]:
        """Open the folder containing MEMORY.md in the OS file manager (best-effort:
        the folder is always created; a headless/CI machine may have no file manager)."""
        import subprocess
        import sys

        folder = self._memory_export_dir(workspace)
        folder.mkdir(parents=True, exist_ok=True)
        opened = True
        try:
            if sys.platform == "darwin":
                subprocess.Popen(
                    ["open", str(folder)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            elif sys.platform == "win32":
                import os

                os.startfile(str(folder))  # type: ignore[attr-defined]
            else:
                subprocess.Popen(
                    ["xdg-open", str(folder)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
        except Exception:
            opened = False
        return {"ok": True, "path": str(folder), "opened": opened}
