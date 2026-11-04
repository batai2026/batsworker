"""Artifacts cluster split out of manager.py (manager-split refactor).

Mixin methods only; shared state lives on the SessionManager facade (self.*). Module-level
imports are limited to what the moved code references; everything else resolves via self.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional

from .manager_helpers import _artifact_kind


class ArtifactsMixin:
    def _artifact_scan_root(self, session_id: str) -> Optional[Path]:
        """The dir the Artifacts panel lists: the session's SCRATCH surface only
        (workspace-scratch-design.md §2.5). For orphan sessions that's the workspace
        itself; for folder-gated sessions it's the side scratch root — never the user's
        repo, which would list the whole codebase as 'artifacts'."""
        record = self.session_store.load(session_id)
        workspace = record.workspace if record else self.default_workspace
        if workspace and self.is_temp_workspace(workspace):
            return Path(workspace).expanduser().resolve()
        if self._SESSION_ID_RE.match(session_id or "") and session_id not in {".", ".."}:
            d = (self.scratch_base() / session_id).resolve()
            if d.is_dir():
                return d
        # Legacy fallback (pre-universal-scratch sessions on a custom scratch base):
        # a workspace that is itself disposable still scans.
        if workspace and not record:
            return Path(workspace).expanduser().resolve()
        return None

    def list_artifacts(self, session_id: str) -> list[dict[str, Any]]:
        root = self._artifact_scan_root(session_id)
        if root is None or not root.is_dir():
            return []
        out: list[dict[str, Any]] = []
        suffixes = {
            ".md",
            ".markdown",
            ".html",
            ".htm",
            ".txt",
            ".json",
            ".csv",
            ".tsv",
            ".py",
            ".js",
            ".ts",
            ".tsx",
            ".css",
            ".png",
            ".jpg",
            ".jpeg",
            ".webp",
            ".gif",
            ".pdf",
            ".xlsx",
            ".xls",
            ".pptx",
            ".ppt",
            ".pptm",
            ".docx",
            ".doc",
            ".docm",
        }
        # os.walk with in-place pruning, NOT rglob: rglob descends first and filters after,
        # so a home-directory workspace walked into ~/Library and tripped the macOS App Data
        # TCC prompt ("OpenWorker would like to access data from other apps") on every turn.
        # Pruning here means those directories are never entered at all.
        from ..tools.search import OS_DATA_DIRS

        skip = {"node_modules", "target", "dist", "__pycache__"} | OS_DATA_DIRS
        for dirpath, dirs, files in os.walk(root):
            dirs[:] = [d for d in dirs if not d.startswith(".") and d not in skip]
            for name in files:
                if name.startswith("."):
                    continue
                path = Path(dirpath) / name
                if path.suffix.lower() not in suffixes:
                    continue
                try:
                    st = path.stat()
                    if not path.is_file():
                        continue
                    out.append(
                        {
                            "path": str(path.relative_to(root)),
                            # Absolute path for "Copy path" — the relative one is useless
                            # outside the app (tester catch 2026-07-12: it copied just the
                            # filename).
                            "abs_path": str(path),
                            "name": path.name,
                            "kind": _artifact_kind(path),
                            "size": st.st_size,
                            "modified_at": st.st_mtime,
                        }
                    )
                except OSError:
                    continue
        out.sort(key=lambda a: a["modified_at"], reverse=True)
        return out[:80]

    MAX_BINARY_PREVIEW = 25 * 1024 * 1024  # base64-over-JSON gets heavy past this

    def _artifact_target(
        self, session_id: str, path: str, *, allow_dir: bool = False
    ) -> tuple[Optional[Path], Optional[str]]:
        """Resolve an artifact path under one of the session's roots — workspace first,
        then the scratch dir, then user-granted extra roots. Universal scratch means a
        gated session's artifacts live BESIDE its workspace, so single-root resolution
        would orphan every transcript chip pointing at scratch."""
        record = self.session_store.load(session_id)
        workspace = record.workspace if record else self.default_workspace
        candidates: list[Path] = []
        if workspace:
            candidates.append(Path(workspace).expanduser().resolve())
        if self._SESSION_ID_RE.match(session_id or "") and session_id not in {".", ".."}:
            scratch = (self.scratch_base() / session_id).resolve()
            if scratch.is_dir() and scratch not in candidates:
                candidates.append(scratch)
        for r in (record.extra_roots if record else []) or []:
            p = Path(str(r.get("path", ""))).expanduser()
            if p.is_dir():
                rp = p.resolve()
                if rp not in candidates:
                    candidates.append(rp)
        if not candidates:
            return None, "no workspace"
        found_missing = False
        for root in candidates:
            target = (root / path).expanduser().resolve()
            try:
                target.relative_to(root)
            except ValueError:
                continue
            if allow_dir and target.is_dir():
                return target, None
            if target.is_file():
                return target, None
            found_missing = True
        if found_missing:
            return None, (
                "This isn't in the conversation's folder anymore — it may have been "
                "moved or deleted."
            )
        return None, "path escapes workspace"

    def read_artifact(self, session_id: str, path: str) -> dict[str, Any]:
        # Folders are readable too (a model sometimes links a whole package, e.g. a skill
        # build dir): return a listing the viewer can render instead of a dead end.
        target, err = self._artifact_target(session_id, path, allow_dir=True)
        if target is None:
            return {"ok": False, "error": err}
        if target.is_dir():
            entries: list[dict[str, Any]] = []
            try:
                children = sorted(
                    target.iterdir(), key=lambda c: (c.is_file(), c.name.lower())
                )
            except OSError as exc:
                return {"ok": False, "error": str(exc)}
            for child in children[:500]:
                try:
                    size = 0 if child.is_dir() else child.stat().st_size
                except OSError:
                    continue
                entries.append({"name": child.name, "dir": child.is_dir(), "size": size})
            return {"ok": True, "path": path, "kind": "folder", "entries": entries}
        kind = _artifact_kind(target)
        if kind == "office":
            # PowerPoint/Word binaries can't be previewed inline; the UI offers
            # "Open in default app" instead of trying to render them.
            return {"ok": True, "path": path, "kind": "office"}
        if kind in ("image", "pdf", "sheet"):
            import base64

            if target.stat().st_size > self.MAX_BINARY_PREVIEW:
                return {
                    "ok": False,
                    "error": "file too large to preview — use Reveal to open it",
                }
            mime = {
                ".png": "image/png",
                ".jpg": "image/jpeg",
                ".jpeg": "image/jpeg",
                ".webp": "image/webp",
                ".gif": "image/gif",
                ".pdf": "application/pdf",
                ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                ".xls": "application/vnd.ms-excel",
            }.get(target.suffix.lower(), "application/octet-stream")
            data = base64.b64encode(target.read_bytes()).decode("ascii")
            return {
                "ok": True,
                "path": path,
                "kind": kind,
                "data_url": f"data:{mime};base64,{data}",
            }
        try:
            text = target.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return {"ok": False, "error": "binary file cannot be previewed"}
        return {
            "ok": True,
            "path": path,
            "kind": kind,
            "content": text[:500000],
            "truncated": len(text) > 500000,
        }

    def reveal_artifact(
        self, session_id: str, path: str, mode: str = "reveal"
    ) -> dict[str, Any]:
        """Show the file in the OS file manager (`reveal`) or open it with its default app
        (`open`). The server runs on the user's machine in both desktop and browser builds, so
        this is local. Cross-platform: macOS `open`, Windows Explorer/ShellExecute, Linux
        `xdg-open`."""
        import os
        import subprocess
        import sys

        target, err = self._artifact_target(session_id, path, allow_dir=True)
        if target is None:
            return {"ok": False, "error": err}
        # A folder "opens" as itself in the file manager, whatever the mode.
        is_dir = target.is_dir()
        try:
            if sys.platform == "darwin":
                args = (
                    ["open", "-R", str(target)]
                    if mode == "reveal" and not is_dir
                    else ["open", str(target)]
                )
                subprocess.Popen(
                    args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                )
            elif sys.platform == "win32":
                if mode == "reveal" and not is_dir:
                    # Explorer wants the path glued to the switch: /select,<path>
                    subprocess.Popen(["explorer", f"/select,{target}"])
                else:
                    os.startfile(str(target))  # type: ignore[attr-defined]  # open in default app
            else:  # Linux/BSD
                tgt = str(target.parent) if mode == "reveal" and not is_dir else str(target)
                subprocess.Popen(
                    ["xdg-open", tgt],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
        except OSError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True}
