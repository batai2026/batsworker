"""Sandboxed file tools (sandbox v2) — file IO routed through DockerExecutor.

When the Docker sandbox is enabled, the catalog's ``files``/``code_files``
capabilities swap the host-side file tools for these, which read/write/list
inside the mounted ``/workspace`` of the long-lived container. The workspace is
the only writable path and the root FS is read-only, so a file op can never touch
the host outside the mount. Tool names and schemas intentionally mirror the host
tools (``read_file`` / ``write_file`` / ``list_files``) so the engine and approval
metadata keep working unchanged.

If Docker becomes unavailable mid-session (the executor raises ``RuntimeError``),
the tools fall back to the HOST workspace and mark the result
``"sandboxed": false`` + ``fallback_reason``, mirroring the shell executor's
per-command probe — the user can tell the file op was NOT sandboxed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

import aisuite as ai

from .sandbox import DockerExecutor

_DEFAULT_MAX_LINES = 2000
_MAX_LINE_CHARS = 500

_READ_SCHEMA = {
    "type": "function",
    "function": {
        "name": "read_file",
        "description": (
            "Read a text file from the sandboxed workspace, returning numbered lines "
            "('   12\\ttext') so code can be referenced as path:line. Large files are "
            "windowed: pass start_line to continue. Read-only."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path, relative to the workspace."},
                "start_line": {"type": "integer", "description": "First line to read, 1-based (default 1)."},
                "max_lines": {"type": "integer", "description": f"How many lines (default {_DEFAULT_MAX_LINES})."},
            },
            "required": ["path"],
        },
    },
}

_WRITE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "write_file",
        "description": (
            "Write a text file into the sandboxed workspace (the only writable path in "
            "the container). Creates parent directories as needed."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path, relative to the workspace."},
                "content": {"type": "string", "description": "Full file content."},
            },
            "required": ["path", "content"],
        },
    },
}

_LIST_SCHEMA = {
    "type": "function",
    "function": {
        "name": "list_files",
        "description": "List files and directories directly under a path in the sandboxed workspace.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Directory path, relative to the workspace (default root)."},
            },
            "required": [],
        },
    },
}

_REPLACE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "replace_in_file",
        "description": (
            "Replace a unique substring in a file inside the sandboxed workspace. "
            "Fails if the old string is not found (or found more than once)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path, relative to the workspace."},
                "old_string": {"type": "string", "description": "Exact text to replace."},
                "new_string": {"type": "string", "description": "Replacement text."},
            },
            "required": ["path", "old_string", "new_string"],
        },
    },
}

_DELETE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "delete_file",
        "description": "Delete a file inside the sandboxed workspace.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path, relative to the workspace."},
            },
            "required": ["path"],
        },
    },
}

_APPLY_PATCH_SCHEMA = {
    "type": "function",
    "function": {
        "name": "apply_patch",
        "description": (
            "Apply a unified diff patch to a file inside the sandboxed workspace. "
            "The file is read from the container, patched here, and written back. "
            "Fails loudly on hunk/context mismatches."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path, relative to the workspace."},
                "patch": {"type": "string", "description": "Unified diff text (---/+++ headers and @@ hunks)."},
            },
            "required": ["path", "patch"],
        },
    },
}


def _format_lines(content: str, start: int, n: int, path: str) -> dict[str, Any]:
    """Shared numbered-line formatting for both sandboxed and host reads."""
    lines = (content or "").splitlines()
    total = len(lines)
    selected: list[str] = []
    for i in range(start - 1, min(total, start - 1 + n)):
        text = lines[i].rstrip("\n")
        if len(text) > _MAX_LINE_CHARS:
            text = text[:_MAX_LINE_CHARS] + "… (line truncated)"
        selected.append(f"{i + 1:>6}\t{text}")
    end = start + len(selected) - 1 if selected else start - 1
    result: dict[str, Any] = {
        "path": path,
        "start_line": start,
        "end_line": end,
        "total_lines": total,
        "content": "\n".join(selected),
    }
    if end < total:
        result["note"] = (
            f"showing lines {start}-{end} of {total}; "
            f"call again with start_line={end + 1} to continue"
        )
    return result


def sandboxed_file_tools(
    executor: DockerExecutor,
    workspace: Optional[str | Path] = None,
    roots: Optional[list] = None,
) -> list:
    """Return sandbox-backed file tools bound to the given Docker executor.

    ``workspace`` enables a HOST fallback: if the container is unreachable (Docker
    died mid-session), the tools operate directly on the workspace and stamp the
    result ``sandboxed: false``.

    ``roots`` (RootDir list) registers each session root as an extra container
    mount (``/roots/N``), so multi-root sessions can read/write their scratch and
    granted dirs inside the sandbox too.
    """
    host_root = Path(workspace).resolve() if workspace else None
    # host root path → container prefix (/roots/N), registered before container start.
    root_map: dict[str, str] = {}
    for r in roots or []:
        host = str(getattr(r, "path", r))
        try:
            root_map[str(Path(host).expanduser().resolve())] = executor.add_mount(host)
        except Exception:  # pragma: no cover - a bad root must not break the toolset
            continue

    def _resolve(path: str) -> tuple[str, str]:
        """Map a tool path to a container operation: ('workspace', rel) for paths
        under the mounted workspace, ('root', container-absolute-path) for mounted
        session roots, or ('error', message)."""
        raw = str(path or "").replace("\\", "/")
        if not raw:
            # Empty path = the workspace root (list_files()).
            return "workspace", ""
        if raw.startswith("/") or (len(raw) > 1 and raw[1] == ":"):
            # Absolute host path → must be inside a mounted root.
            for host, prefix in root_map.items():
                hp = host.replace("\\", "/")
                if raw == hp:
                    return "root", f"{prefix}/"
                if raw.startswith(hp.rstrip("/") + "/"):
                    rel = raw[len(hp.rstrip("/")) + 1 :]
                    return "root", f"{prefix}/{rel}"
            return "error", f"path is outside the sandboxed roots: {path}"
        safe = DockerExecutor._safe_rel(raw)
        if safe is None:
            return "error", f"unsafe path: {path}"
        return "workspace", safe

    def _host_abs(path: str) -> tuple[Optional[Path], Optional[str]]:
        """Resolve a tool path to a host-absolute path for the fallback tools."""
        raw = str(path or "").replace("\\", "/")
        if not raw:
            return (host_root, None) if host_root is not None else (None, "no host fallback workspace configured")
        if raw.startswith("/") or (len(raw) > 1 and raw[1] == ":"):
            for host in root_map:
                hp = host.replace("\\", "/")
                if raw == hp:
                    return Path(host), None
                if raw.startswith(hp.rstrip("/") + "/"):
                    return Path(host) / raw[len(hp.rstrip("/")) + 1 :], None
            return None, f"path is outside the sandboxed roots: {path}"
        if host_root is None:
            return None, "no host fallback workspace configured"
        safe = DockerExecutor._safe_rel(raw)
        if safe is None:
            return None, f"unsafe path: {path}"
        return host_root / safe, None

    def _host_read(path: str, start: int, n: int) -> dict[str, Any]:
        target, err = _host_abs(path)
        if target is None:
            return {"error": err or "unsafe path"}
        try:
            content = target.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return {"error": f"read failed: {exc}"}
        return _format_lines(content, start, n, path)

    def _host_write(path: str, content: str) -> dict[str, Any]:
        target, err = _host_abs(path)
        if target is None:
            return {"error": err or "unsafe path"}
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content or "", encoding="utf-8")
        except OSError as exc:
            return {"error": f"write failed: {exc}"}
        return {"ok": True, "path": path}

    def _host_list(path: str) -> dict[str, Any]:
        target, err = _host_abs(path)
        if target is None:
            return {"error": err or "unsafe path"}
        try:
            entries = sorted(str(p) for p in target.iterdir())
        except OSError as exc:
            return {"error": f"list failed: {exc}"}
        return {"ok": True, "path": path or "/workspace", "entries": entries}

    def _host_replace(path: str, old_string: str, new_string: str) -> dict[str, Any]:
        target, err = _host_abs(path)
        if target is None:
            return {"error": err or "unsafe path"}
        try:
            content = target.read_text(encoding="utf-8")
        except OSError as exc:
            return {"error": f"read failed: {exc}"}
        count = content.count(old_string)
        if count != 1:
            return {
                "error": (
                    f"expected exactly one match for old_string, found {count}"
                )
            }
        try:
            target.write_text(content.replace(old_string, new_string), encoding="utf-8")
        except OSError as exc:
            return {"error": f"write failed: {exc}"}
        return {"ok": True, "path": path}

    def _host_delete(path: str) -> dict[str, Any]:
        target, err = _host_abs(path)
        if target is None:
            return {"error": err or "unsafe path"}
        try:
            target.unlink()
        except OSError as exc:
            return {"error": f"delete failed: {exc}"}
        return {"ok": True, "path": path}

    def _host_apply_patch(path: str, patch: str) -> dict[str, Any]:
        from .unified_diff import apply_unified_diff

        target, err = _host_abs(path)
        if target is None:
            return {"error": err or "unsafe path"}
        try:
            content = target.read_text(encoding="utf-8")
        except OSError as exc:
            return {"error": f"read failed: {exc}"}
        new_content, err = apply_unified_diff(content, patch)
        if err is not None:
            return {"error": err}
        try:
            target.write_text(new_content or "", encoding="utf-8")
        except OSError as exc:
            return {"error": f"write failed: {exc}"}
        return {"ok": True, "path": path}

    def read_file(path: str, start_line: int = 1, max_lines: int = _DEFAULT_MAX_LINES) -> dict[str, Any]:
        start = start_line if isinstance(start_line, int) and start_line > 0 else 1
        n = max_lines if isinstance(max_lines, int) and max_lines > 0 else _DEFAULT_MAX_LINES
        n = min(n, _DEFAULT_MAX_LINES)
        kind, value = _resolve(path)
        try:
            if kind == "root":
                content, err = executor.read_container_file(value)
            elif kind == "workspace":
                content, err = executor.read_workspace_file(value)
            else:
                return {"error": value}
            if err:
                return {"error": err}
            return _format_lines(content or "", start, n, path)
        except RuntimeError as exc:
            res = _host_read(path, start, n)
            return {**res, "sandboxed": False, "fallback_reason": str(exc)}

    def write_file(path: str, content: str) -> dict[str, Any]:
        kind, value = _resolve(path)
        try:
            if kind == "root":
                err = executor.write_container_file(value, content or "")
            elif kind == "workspace":
                err = executor.write_workspace_file(value, content or "")
            else:
                return {"error": value}
            if err:
                return {"error": err}
            return {"ok": True, "path": path}
        except RuntimeError as exc:
            res = _host_write(path, content or "")
            return {**res, "sandboxed": False, "fallback_reason": str(exc)}

    def list_files(path: str = "") -> dict[str, Any]:
        kind, value = _resolve(path)
        try:
            if kind == "root":
                listing, err = executor.list_container_dir(value)
            elif kind == "workspace":
                listing, err = executor.list_workspace_dir(value)
            else:
                return {"error": value}
            if err:
                return {"error": err}
            entries = [ln for ln in (listing or "").splitlines() if ln.strip()]
            return {"ok": True, "path": path or "/workspace", "entries": entries}
        except RuntimeError as exc:
            res = _host_list(path)
            return {**res, "sandboxed": False, "fallback_reason": str(exc)}

    def replace_in_file(path: str, old_string: str, new_string: str) -> dict[str, Any]:
        kind, value = _resolve(path)
        try:
            if kind == "root":
                content, err = executor.read_container_file(value)
            elif kind == "workspace":
                content, err = executor.read_workspace_file(value)
            else:
                return {"error": value}
            if err:
                return {"error": err}
            count = (content or "").count(old_string)
            if count != 1:
                return {"error": f"expected exactly one match for old_string, found {count}"}
            new_content = (content or "").replace(old_string, new_string)
            if kind == "root":
                err = executor.write_container_file(value, new_content)
            else:
                err = executor.write_workspace_file(value, new_content)
            if err:
                return {"error": err}
            return {"ok": True, "path": path}
        except RuntimeError as exc:
            res = _host_replace(path, old_string, new_string)
            return {**res, "sandboxed": False, "fallback_reason": str(exc)}

    def delete_file(path: str) -> dict[str, Any]:
        kind, value = _resolve(path)
        try:
            if kind == "root":
                err = executor.delete_container_file(value)
            elif kind == "workspace":
                err = executor.delete_workspace_file(value)
            else:
                return {"error": value}
            if err:
                return {"error": err}
            return {"ok": True, "path": path}
        except RuntimeError as exc:
            res = _host_delete(path)
            return {**res, "sandboxed": False, "fallback_reason": str(exc)}

    def apply_patch(path: str, patch: str) -> dict[str, Any]:
        from .unified_diff import apply_unified_diff

        kind, value = _resolve(path)
        try:
            if kind == "root":
                content, err = executor.read_container_file(value)
            elif kind == "workspace":
                content, err = executor.read_workspace_file(value)
            else:
                return {"error": value}
            if err:
                return {"error": err}
            new_content, derr = apply_unified_diff(content or "", patch)
            if derr is not None:
                return {"error": derr}
            if kind == "root":
                err = executor.write_container_file(value, new_content or "")
            else:
                err = executor.write_workspace_file(value, new_content or "")
            if err:
                return {"error": err}
            return {"ok": True, "path": path}
        except RuntimeError as exc:
            res = _host_apply_patch(path, patch)
            return {**res, "sandboxed": False, "fallback_reason": str(exc)}

    for fn, schema, category, risk in [
        (read_file, _READ_SCHEMA, "filesystem", "low"),
        (write_file, _WRITE_SCHEMA, "filesystem", "medium"),
        (list_files, _LIST_SCHEMA, "filesystem", "low"),
        (replace_in_file, _REPLACE_SCHEMA, "filesystem", "medium"),
        (delete_file, _DELETE_SCHEMA, "filesystem", "medium"),
        (apply_patch, _APPLY_PATCH_SCHEMA, "filesystem", "medium"),
    ]:
        name = schema["function"]["name"]
        fn.__name__ = name
        fn.__doc__ = schema["function"]["description"]
        fn.__coworker_schema__ = schema
        fn.__aisuite_tool_metadata__ = ai.ToolMetadata(
            name=name,
            category=category,
            risk_level=risk,
            capabilities=["files"],
            requires_approval=risk == "medium",
        )

    return [
        ai.tool(read_file, metadata=read_file.__aisuite_tool_metadata__),
        ai.tool(write_file, metadata=write_file.__aisuite_tool_metadata__),
        ai.tool(list_files, metadata=list_files.__aisuite_tool_metadata__),
        ai.tool(replace_in_file, metadata=replace_in_file.__aisuite_tool_metadata__),
        ai.tool(delete_file, metadata=delete_file.__aisuite_tool_metadata__),
        ai.tool(apply_patch, metadata=apply_patch.__aisuite_tool_metadata__),
    ]