"""Per-session file checkpoints with turn-level rollback ("time travel").

What it covers: files modified by the tracked write tools (``risk.WRITE_TOOLS``:
write_file, replace_in_file, apply_patch, apply_unified_diff). Before any of
those executes, the file's current content — or its absence — is captured into a
content-addressed blob store; each turn accumulates the files it touched and is
appended as ONE checkpoint record when the turn ends. Rolling back to checkpoint
K restores, for every file first modified after K, the content captured at that
first post-K modification (which is exactly the file's state at the end of turn
K), and deletes files that did not exist then. The turn list is truncated to K:
later checkpoints are removed (their blobs stay on disk, deduped and harmless).

What v1 deliberately does NOT cover:
  - Changes made through ``run_shell`` (sed, git, builds…) are invisible to
    capture — the command text cannot be trusted to name its effects. The UI
    says a checkpoint covers file-tool writes.
  - Conversation history is not rewound; the surface posts a transcript notice
    so the agent (and the user) know a rollback happened.
  - Files over ``MAX_FILE_BYTES`` are marked skipped: rollback reports them as
    unrestorable rather than silently keeping the newer content.

Layout, under the session's state dir (never inside a writable root — the agent
must not be able to delete its own undo history):

    checkpoints/<session_id>/blobs/<sha[:2]>/<sha>   content-addressed
    checkpoints/<session_id>/turns.json              append-only turn records

A turn record: {seq, ts, user, files: [{path (resolved), display, blob?, size?,
skipped?}]}. ``blob`` absent ⇒ the file did not exist before the turn's first
write to it (rollback deletes it). ``skipped`` present ⇒ pre-content could not
be captured (too large / unreadable); rollback leaves that file alone.

Blobs are written BEFORE the tool mutates the file, and a turn record is only
finalized on a clean turn end — a crash mid-turn leaves an unfinalized pending
turn that is simply discarded on the next load (fail-toward-less-history, the
same defensive posture the other stores take).
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .risk import WRITE_TOOLS

MAX_FILE_BYTES = 20 * 1024 * 1024


class CheckpointBusy(RuntimeError):
    """A rollback was attempted while a turn is mid-flight (pending captures)."""


class CheckpointNotFound(KeyError):
    """No checkpoint with the requested seq exists for this session."""


def _atomic_write_json(path: Path, data: Any) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def _load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default
    except (OSError, ValueError):
        # Defensive parse (same posture as sessions/grants): a corrupt file
        # degrades to "no history" instead of killing the server.
        return default


class SessionCheckpoints:
    """One session's turn-level file checkpoints. Owned by the manager; the
    engine holds a reference and calls it from the tool-execution thread."""

    def __init__(
        self,
        session_id: str,
        state_dir: Path,
        *,
        roots_getter: Callable[[], List[Dict[str, Any]]],
        workspace_root: Path,
    ) -> None:
        # Session ids are engine-generated (alnum/dash/underscore, sometimes a
        # __-prefixed kind); never let one name a path traversal.
        safe = "".join(c if (c.isalnum() or c in "_-") else "_" for c in session_id)
        self._dir = Path(state_dir) / "checkpoints" / safe
        self._blobs = self._dir / "blobs"
        self._turns_path = self._dir / "turns.json"
        self._roots_getter = roots_getter
        self.workspace_root = Path(workspace_root)
        # Files captured since begin_turn, keyed by resolved path (first capture
        # wins: the earliest pre-content in the turn is the rollback truth).
        self._pending: Optional[Dict[str, Any]] = None

    # -- capture (engine thread) ---------------------------------------------

    def _writable_roots(self) -> List[Path]:
        out: List[Path] = []
        try:
            for root in self._roots_getter() or []:
                if root.get("writable"):
                    out.append(Path(str(root.get("path", ""))).resolve())
        except Exception:
            pass
        return out

    def capture_before(self, tool_name: str, arguments: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Capture a file's pre-write content. Returns the entry to attach on
        tool success (``note_write``), or None when there is nothing to track.
        Runs in the tool worker thread: sync I/O only, and never raises."""
        if self._pending is None or tool_name not in WRITE_TOOLS:
            return None
        try:
            from .permissions import write_paths

            paths, located = write_paths(tool_name, arguments or {})
            if not located or not paths:
                return None
            from .provenance import resolve

            resolved = resolve(paths[0], self.workspace_root)
            if resolved in self._pending["files"]:
                return None  # first capture wins — the earliest pre-content
            if not self._in_writable_root(Path(resolved)):
                return None
            entry = self._capture_file(resolved, display=str(paths[0]))
            if entry is None:
                return None
            self._pending["files"][resolved] = entry
            return entry
        except Exception:
            return None  # checkpoints must never break a write

    def note_write(self, entry: Dict[str, Any]) -> None:
        """Attach a captured entry to the pending turn (call only after the
        write tool SUCCEEDED — a failed write left nothing to roll back)."""
        if self._pending is None:
            return
        for record in self._pending["files"].values():
            if record is entry:
                return
        # capture_before already stored it in pending; nothing extra to do here
        # beyond existing (kept for call-site symmetry and future filtering).

    def discard(self, entry: Optional[Dict[str, Any]]) -> None:
        """Drop a capture whose tool call failed or was interrupted."""
        if self._pending is None or entry is None:
            return
        self._pending["files"] = {
            k: v for k, v in self._pending["files"].items() if v is not entry
        }

    def _capture_file(self, resolved: str, *, display: str) -> Optional[Dict[str, Any]]:
        path = Path(resolved)
        if not path.exists():
            return {"path": resolved, "display": display, "blob": None, "size": 0}
        try:
            size = path.stat().st_size
            if size > MAX_FILE_BYTES:
                return {
                    "path": resolved,
                    "display": display,
                    "skipped": "too large",
                    "size": size,
                }
            data = path.read_bytes()
        except OSError:
            return {"path": resolved, "display": display, "skipped": "unreadable"}
        sha = hashlib.sha256(data).hexdigest()
        blob = self._blobs / sha[:2] / sha
        if not blob.exists():
            blob.parent.mkdir(parents=True, exist_ok=True)
            tmp = blob.with_suffix(".tmp")
            tmp.write_bytes(data)
            os.replace(tmp, blob)
        return {"path": resolved, "display": display, "blob": sha, "size": len(data)}

    def _in_writable_root(self, path: Path) -> bool:
        try:
            resolved = path.resolve()
        except (OSError, ValueError):
            return False
        for root in self._writable_roots():
            try:
                resolved.relative_to(root)
                return True
            except ValueError:
                continue
        return False

    # -- turn boundaries ------------------------------------------------------

    def begin_turn(self, user_text: str) -> None:
        """Open a pending turn. Any unfinalized previous pending turn (a crash
        mid-turn) is dropped: it never became a checkpoint."""
        self._pending = {
            "seq": self._next_seq(),
            "ts": time.time(),
            "user": (user_text or "")[:160],
            "files": {},
        }

    def end_turn(self) -> None:
        """Finalize the pending turn as a checkpoint record — only when it
        actually touched tracked files (otherwise there is nothing to roll
        back to, and a gap-free seq would just be noise)."""
        pending = self._pending
        self._pending = None
        if not pending or not pending["files"]:
            return
        record = {
            "seq": pending["seq"],
            "ts": pending["ts"],
            "user": pending["user"],
            "files": list(pending["files"].values()),
        }
        self._dir.mkdir(parents=True, exist_ok=True)
        turns = self.list_turns()
        turns.append(record)
        _atomic_write_json(self._turns_path, turns)

    def _next_seq(self) -> int:
        seqs = [t.get("seq", 0) for t in self.list_turns()]
        return (max(seqs) if seqs else 0) + 1

    # -- listing & rollback ---------------------------------------------------

    def list_turns(self) -> List[Dict[str, Any]]:
        turns = _load_json(self._turns_path, [])
        return turns if isinstance(turns, list) else []

    def rollback_to(self, seq: int) -> Dict[str, Any]:
        """Restore files to their state at the end of turn ``seq`` and truncate
        the turn list there. Files whose pre-content could not be captured are
        reported as skipped and left untouched."""
        if self._pending is not None and self._pending["files"]:
            raise CheckpointBusy("a turn is mid-flight — wait for it to finish")
        turns = self.list_turns()
        index = next((i for i, t in enumerate(turns) if t.get("seq") == seq), None)
        if index is None:
            raise CheckpointNotFound(str(seq))

        # First post-seq capture per resolved path == state at end of turn seq.
        first: Dict[str, Dict[str, Any]] = {}
        for record in turns[index + 1 :]:
            for entry in record.get("files", []):
                first.setdefault(entry.get("path", ""), entry)

        restored = deleted = skipped = 0
        for path, entry in first.items():
            if entry.get("skipped"):
                skipped += 1
                continue
            target = Path(path)
            sha = entry.get("blob")
            try:
                if sha is None:
                    if target.exists():
                        target.unlink()
                        deleted += 1
                    continue
                blob = self._blobs / str(sha)[:2] / str(sha)
                if not blob.exists():
                    skipped += 1  # blob lost (state dir wiped) — fail soft
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(blob.read_bytes())
                restored += 1
            except OSError:
                skipped += 1

        _atomic_write_json(self._turns_path, turns[: index + 1])
        return {"seq": seq, "restored": restored, "deleted": deleted, "skipped": skipped}

    # Blob GC is deliberately absent in v1: blobs are content-addressed and
    # deduped, orphaned ones after truncation are bounded by what the session
    # actually wrote, and a sweep racing an in-flight capture needs locking the
    # whole design doesn't have yet.
