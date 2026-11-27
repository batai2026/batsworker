"""Diagnostics from the desktop webview, on disk.

Why this exists: the SPA's Tauri bridge maps every rejected `invoke` to `null`
(see `surfaces/gui/src/tauri.ts`), so a missing capability or an unregistered
command looks exactly like "this feature isn't in the desktop app". Release
builds compile no devtools (`src-tauri/Cargo.toml` enables only `tray-icon`), so
the `console.error` that now accompanies such a failure is invisible unless
someone attaches a debugger. A user can point at a file; they cannot point at a
console they can't open.

Contract, mirroring `plugins/consent_audit`: best-effort, append-only JSONL, and
never raise into the request it records. Two differences, both because this
stream is driven by a browser rather than by rare install events:

* The file is **bounded**: it is capped at `MAX_BYTES` and rotated once to
  ``.1``, so a misbehaving page cannot fill the disk.
* Entries are **whitelisted**: only known fields survive, strings are truncated,
  and anything oversized is dropped. The caller is a webview we do not fully
  trust -- it must not be able to smuggle arbitrary content into a file that a
  human will later paste into a bug report.

No request headers are ever read here, so the sidecar token cannot land in the
log by accident.
"""

from __future__ import annotations

import json
import platform as platform_module
import sys
import time
from pathlib import Path
from typing import Any, Optional

from .secrets import state_dir

LOG_FILENAME = "ui_events.jsonl"

# One rotated companion, so the on-disk ceiling is 2 x MAX_BYTES.
MAX_BYTES = 1 << 20  # 1 MiB
MAX_BATCH = 20  # entries accepted in one POST
MAX_ENTRY_BYTES = 4096  # serialized ceiling for a single entry
MAX_READ = 500
MAX_TEXT = 500  # per free-text field
MAX_STACK = 4000

# Fields the SPA may send. Everything else is dropped rather than stored: this
# file is meant to be safe to hand to a maintainer verbatim.
_ALLOWED = {"command", "message", "stack", "origin", "platform", "app_version", "level"}


def _path() -> Path:
    return Path(state_dir()) / LOG_FILENAME


def path() -> str:
    """Where the log lives, for telling a user what to attach to a bug report."""
    return str(_path())


def platform() -> str:
    """The sidecar's own OS name, i.e. where the log file physically is.

    Distinct from an entry's ``platform`` field, which is what the webview
    reported: the two disagreeing is itself a useful signal."""
    return f"{sys.platform} ({platform_module.machine()})"


def _clip(value: Any, limit: int) -> Optional[str]:
    if not isinstance(value, str):
        return None
    text = value.replace("\x00", "")
    return text[:limit]


def sanitize(entry: Any) -> Optional[dict[str, Any]]:
    """Reduce a client-supplied entry to known, bounded fields.

    Returns ``None`` when there is nothing worth keeping, so a junk payload can
    never create a row.
    """
    if not isinstance(entry, dict):
        return None
    clean: dict[str, Any] = {}
    for key in _ALLOWED:
        if key not in entry:
            continue
        limit = MAX_STACK if key == "stack" else MAX_TEXT
        clipped = _clip(entry.get(key), limit)
        if clipped:
            clean[key] = clipped
    if not clean:
        return None
    # Only a literal "error"/"warn" is meaningful; anything else would be novel
    # vocabulary invented by the client.
    if clean.get("level") not in ("error", "warn"):
        clean.pop("level", None)
    return clean


def _rotate_if_needed(path: Path, incoming: int) -> None:
    try:
        size = path.stat().st_size
    except OSError:
        return  # no file yet
    if size + incoming <= MAX_BYTES:
        return
    try:
        path.replace(path.with_suffix(path.suffix + ".1"))
    except OSError:
        # A concurrent reader holding the file open on Windows can block the
        # rename; truncating keeps the cap even when rotation is unavailable.
        try:
            path.write_text("", encoding="utf-8")
        except OSError:
            pass


def record(events: Any) -> int:
    """Append up to ``MAX_BATCH`` sanitized entries. Returns how many were kept.

    Best-effort by contract: a broken state dir must not fail the delivery it is
    only supposed to observe.
    """
    if not isinstance(events, list):
        return 0
    now = time.time()
    rows: list[str] = []
    for raw in events[:MAX_BATCH]:
        clean = sanitize(raw)
        if clean is None:
            continue
        clean["ts"] = now
        try:
            line = json.dumps(clean, ensure_ascii=False)
        except (TypeError, ValueError):
            continue
        if len(line.encode("utf-8")) > MAX_ENTRY_BYTES:
            continue
        rows.append(line)
    if not rows:
        return 0
    payload = "\n".join(rows) + "\n"
    try:
        path = _path()
        path.parent.mkdir(parents=True, exist_ok=True)
        _rotate_if_needed(path, len(payload.encode("utf-8")))
        with path.open("a", encoding="utf-8") as fh:
            fh.write(payload)
    except Exception:
        return 0  # never fail the request this only observes
    return len(rows)


def read(limit: int = 100) -> list[dict[str, Any]]:
    """Newest-first rows, tolerating a torn final line."""
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
            continue  # a torn line must not void the rest of the file
        if isinstance(row, dict):
            rows.append(row)
    limit = max(1, min(int(limit or 100), MAX_READ))
    return rows[-limit:][::-1]


def report(limit: int = 200) -> str:
    """A self-contained text file a user can attach to a bug report.

    Deliberately not a second JSON dump: whoever receives this will read it in a
    text editor, so it opens with the facts needed to interpret the rest (time,
    platform, app version, where the full log lives) and keeps one line per
    event. No token, no headers, no workspace paths -- only what `record()`
    already whitelisted.

    A missing log is not an error: "no events were recorded" is itself the
    diagnostic, and it is the common case for a healthy install.
    """
    from . import __version__

    rows = read(limit=limit)
    out = [
        "OpenWorker diagnostics",
        "======================",
        "",
        f"Generated:   {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"App version: {__version__}",
        f"Platform:    {platform() or 'unknown'}",
        f"Log file:    {path()}",
        f"Events:      {len(rows)}",
        "",
    ]
    if not rows:
        out.append("No webview failures were recorded. Nothing to attach beyond this line.")
        out.append("")
        return "\n".join(out)

    out.append("Newest first. Each entry is one webview command that failed.")
    out.append("")
    for row in rows:
        when = row.get("ts")
        stamp = (
            time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(when))
            if isinstance(when, (int, float))
            else "unknown time"
        )
        out.append(f"[{stamp}] {row.get('level', 'error')}: {row.get('command', '?')}")
        if row.get("message"):
            out.append(f"  message: {row['message']}")
        if row.get("origin"):
            out.append(f"  origin:  {row['origin']}")
        if row.get("platform"):
            out.append(f"  client:  {row['platform']}")
        if row.get("app_version"):
            out.append(f"  version: {row['app_version']}")
        if row.get("stack"):
            # Keep the trace readable but bounded; it is a file, so indentation
            # is cheaper than one long line.
            for line in str(row["stack"]).splitlines()[:20]:
                out.append(f"    {line}")
        out.append("")
    return "\n".join(out)
