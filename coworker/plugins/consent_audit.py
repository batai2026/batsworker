"""Append-only consent audit ledger (P2-10).

Every plugin/MCP install, uninstall and permission change that went through the
unified pipeline leaves one JSONL row, so "who widened this server's authority,
and when" is answerable after the fact. Best-effort by contract: auditing must
never break the request it records.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Optional

from ..secrets import state_dir

AUDIT_FILENAME = "plugin_audit.jsonl"
_MAX_LIMIT = 500


def _path() -> Path:
    return Path(state_dir()) / AUDIT_FILENAME


def record(
    *,
    kind: str,
    id: str,
    action: str,
    decision: str,
    consent: Optional[dict[str, Any]] = None,
    source: str = "api",
) -> None:
    """Append one audit row. ``decision``: accepted | deferred | denied."""
    entry: dict[str, Any] = {
        "ts": time.time(),
        "kind": kind,
        "id": id,
        "action": action,
        "decision": decision,
        "source": source,
    }
    if consent:
        for key in ("capability_set", "risks", "permissions"):
            if key in consent:
                entry[key] = consent[key]
        widened = (consent.get("permission_diff") or {}).get("widened") or []
        if widened:
            entry["widened"] = widened
    try:
        path = _path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass  # best-effort: a broken state dir must not fail an install


def read(limit: int = 100, id: Optional[str] = None) -> list[dict[str, Any]]:
    """Newest-first rows, optionally filtered by plugin/server id."""
    path = _path()
    try:
        raw = path.read_text(encoding="utf-8")
    except Exception:
        return []
    rows: list[dict[str, Any]] = []
    for line in raw.splitlines():
        try:
            row = json.loads(line)
        except (json.JSONDecodeError, TypeError):
            continue  # a torn line must not void the rest of the ledger
        if not isinstance(row, dict):
            continue
        if id and row.get("id") != id:
            continue
        rows.append(row)
    limit = max(1, min(int(limit or 100), _MAX_LIMIT))
    return rows[-limit:][::-1]
