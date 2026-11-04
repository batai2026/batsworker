"""Plain-text memory — export/import memory as an editable Markdown file.

The memory store is SQLite; this module renders its contents as a human-readable
Markdown document (grouped by scope) and parses an edited document back into
entries. Round-trip semantics are **replace per scope**: re-importing an edited
file makes the file the source of truth for each section it names (global, or a
specific workspace), so the user can edit memory like a doc — the Lindy model —
without a database UI.

Format::

    # OpenWorker Memory

    This file is a plain-text view of the local memory store. Edit the bullets
    and re-import to update what the worker remembers between conversations.

    ## Global
    - A preference that applies everywhere

    ## Workspace: C:\\path\\to\\project
    - A fact about this project

    ## Session: abc-123
    - A session-scoped note (rarely exported)
"""

from __future__ import annotations

import re
from typing import Any, Optional

from .base import MemoryItem, Scope

MEMORY_FILENAME = "MEMORY.md"

_HEADING_RE = re.compile(r"^##\s+(.+)$")
_BULLET_RE = re.compile(r"^\s*[-*]\s+(.+?)\s*$")


def _scope_label(scope: Scope, workspace: Optional[str]) -> str:
    if scope == Scope.GLOBAL:
        return "Global"
    if scope == Scope.WORKSPACE:
        return f"Workspace: {workspace or '(unknown)'}"
    return f"Session: {workspace or ''}".rstrip(":")


def memory_to_markdown(items: list[MemoryItem]) -> str:
    """Render memory entries as an editable Markdown document grouped by scope."""
    # Group: (scope, workspace) -> [content]; session entries keep their session id.
    groups: dict[tuple[str, Optional[str]], list[str]] = {}
    for item in items:
        key = (item.scope.value, item.workspace or None)
        groups.setdefault(key, []).append(item.content)

    lines = [
        "# OpenWorker Memory",
        "",
        "This file is a plain-text view of the local memory store. Edit the "
        "bullets and re-import to update what the worker remembers between "
        "conversations.",
        "",
    ]
    # Stable ordering: global first, then workspaces alphabetically, then sessions.
    def sort_key(kv):
        (scope, ws), _ = kv
        return (0 if scope == Scope.GLOBAL.value else 1 if scope == Scope.WORKSPACE.value else 2, ws or "")

    for (scope, ws), contents in sorted(groups.items(), key=sort_key):
        lines.append(f"## {_scope_label(Scope(scope), ws)}")
        lines.append("")
        if not contents:
            lines.append("- _nothing_")
            lines.append("")
            continue
        for content in contents:
            # Bullets must be single-line; fold hard line breaks so the round-trip
            # stays parseable.
            one_line = " ".join(content.strip().splitlines())
            lines.append(f"- {one_line}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def parse_memory_markdown(text: str) -> list[dict[str, Any]]:
    """Parse an edited memory document into ``{scope, workspace, content}`` rows.

    ``## Global`` → global entries; ``## Workspace: <path>`` → workspace entries
    bound to that path; ``## Session: <id>`` → session entries. Bullets under a
    heading become content; empty/`_nothing_` sections produce no rows.
    """
    rows: list[dict[str, Any]] = []
    current: Optional[dict[str, Any]] = None
    for raw in (text or "").splitlines():
        heading = _HEADING_RE.match(raw.strip())
        if heading:
            label = heading.group(1).strip()
            if label.lower() == "global":
                current = {"scope": Scope.GLOBAL.value, "workspace": None}
            elif label.lower().startswith("workspace:"):
                ws = label.split(":", 1)[1].strip()
                current = {"scope": Scope.WORKSPACE.value, "workspace": ws or None}
            elif label.lower().startswith("session:"):
                sid = label.split(":", 1)[1].strip()
                current = {"scope": Scope.SESSION.value, "workspace": sid or None}
            else:
                current = None
            continue
        bullet = _BULLET_RE.match(raw)
        if not bullet or current is None:
            continue
        content = bullet.group(1).strip()
        if not content or content == "_nothing_":
            continue
        rows.append({**current, "content": content})
    return rows