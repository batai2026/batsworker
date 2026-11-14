"""Turn ledger (trajectory) — append-only, redacted, local per-session event log.

One JSONL file per session under ``<data-dir>/trajectory/<session>.jsonl``. Records
describe WHAT crossed the provider boundary and what the engine did — model id, an
outbound-payload digest/size, tool names + argument keys/digests, approval outcomes —
**never message bodies, argument values, or tool results**. That keeps the ledger
useful for byte-stability checks and later replay tooling without duplicating the
transcript or adding a second place secrets could leak from.

Design notes:
  - Separate from ``conversations`` (the canonical transcript) and ``traces.db``
    (tool-span timing). The ledger is the engine-side "what actually went out /
    what decision was made" record.
  - Writes are best-effort by contract: any failure is swallowed at the
    ``SessionTrajectory`` boundary so a ledger bug can never break a turn.
  - Retention is per-session record count; exceeding the cap atomically rewrites
    the file keeping the newest records.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from hashlib import sha256
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
DEFAULT_MAX_RECORDS = 5000
MAX_STRING_CHARS = 1200
_MAX_DEPTH = 6
_REDACTED = "[redacted]"

# Key names whose VALUE is replaced wholesale. Normalized (lowercase, `-`→`_`).
_SECRET_KEYS = frozenset(
    {
        "authorization",
        "proxy_authorization",
        "api_key",
        "apikey",
        "access_token",
        "refresh_token",
        "id_token",
        "token",
        "secret",
        "client_secret",
        "password",
        "passwd",
        "cookie",
        "set_cookie",
        "x_api_key",
        "private_key",
        "credential",
        "credentials",
    }
)

# Known credential shapes caught by value even when the key name looks harmless.
_SECRET_VALUE_PATTERNS = (
    re.compile(r"(?i)bearer\s+[a-z0-9._~+/=-]{8,}"),
    re.compile(r"\b(?:sk|rk)-[A-Za-z0-9_-]{16,}"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
)

_SAFE_ID = re.compile(r"[^A-Za-z0-9_.-]")


def _safe_id(session_id: str) -> str:
    return _SAFE_ID.sub("_", str(session_id)) or "session"


def _normalize_key(key: Any) -> str:
    return str(key).strip().lower().replace("-", "_")


def _redact_text(text: str) -> str:
    for pattern in _SECRET_VALUE_PATTERNS:
        text = pattern.sub(_REDACTED, text)
    if len(text) > MAX_STRING_CHARS:
        omitted = len(text) - MAX_STRING_CHARS
        text = text[:MAX_STRING_CHARS] + f"…[{omitted} chars clipped]"
    return text


def redact(value: Any, *, _depth: int = 0) -> Any:
    """Recursively strip secret-shaped keys/values and clip oversized strings."""
    if _depth > _MAX_DEPTH:
        return "[truncated]"
    if isinstance(value, dict):
        return {
            str(key): (
                _REDACTED
                if _normalize_key(key) in _SECRET_KEYS
                else redact(item, _depth=_depth + 1)
            )
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact(item, _depth=_depth + 1) for item in value]
    if isinstance(value, str):
        return _redact_text(value)
    if isinstance(value, (bytes, bytearray)):
        return f"[bytes {len(value)}]"
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return _redact_text(str(value))


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")


def text_digest(text: str) -> str:
    return sha256(str(text).encode("utf-8")).hexdigest()


def value_digest(value: Any) -> str:
    try:
        return sha256(_canonical_bytes(value)).hexdigest()
    except (TypeError, ValueError):
        return sha256(repr(value).encode("utf-8")).hexdigest()


def json_size(value: Any) -> int:
    try:
        return len(_canonical_bytes(value))
    except (TypeError, ValueError):
        return len(repr(value).encode("utf-8"))


def _input_chars(value: Any) -> int:
    if isinstance(value, str):
        return len(value)
    if isinstance(value, list):
        return sum(
            len(part.get("text") or "")
            for part in value
            if isinstance(part, dict) and part.get("type") == "text"
        )
    return 0


def outbound_snapshot(
    messages: list[dict[str, Any]],
    *,
    model: str,
    tool_names: list[str],
    settings_keys: list[str],
) -> dict[str, Any]:
    """Content-free fingerprint of the exact payload handed to the provider."""
    roles: dict[str, int] = {}
    for message in messages or []:
        role = str((message or {}).get("role") or "unknown")
        roles[role] = roles.get(role, 0) + 1
    # One canonical serialization feeds both size and digest (this runs per model call).
    payload = messages or []
    try:
        encoded = _canonical_bytes(payload)
    except (TypeError, ValueError):
        encoded = repr(payload).encode("utf-8", errors="replace")
    return {
        "model": str(model or ""),
        "messages": len(payload),
        "bytes": len(encoded),
        "digest": sha256(encoded).hexdigest(),
        "roles": roles,
        "tools": [str(name) for name in (tool_names or [])],
        "settings": [str(key) for key in (settings_keys or [])],
    }


def _seq_of(record: dict[str, Any]) -> int:
    try:
        return int(record.get("seq") or 0)
    except (TypeError, ValueError):
        return 0


def _turn_of(record: dict[str, Any]) -> int:
    try:
        return int(record.get("turn") or 0)
    except (TypeError, ValueError):
        return 0


class TrajectoryStore:
    """Thread-safe per-session JSONL store (engine loop + tool worker threads share it)."""

    def __init__(self, base_dir: str | Path, *, max_records: int = DEFAULT_MAX_RECORDS) -> None:
        self.base = Path(base_dir).expanduser()
        self.max_records = int(max_records) if int(max_records) > 0 else DEFAULT_MAX_RECORDS
        self._lock = threading.RLock()
        self._states: dict[str, dict[str, int]] = {}
        self._counts: dict[str, int] = {}

    def _file(self, session_id: str) -> Path:
        return self.base / f"{_safe_id(session_id)}.jsonl"

    def _read_rows(self, session_id: str) -> list[dict[str, Any]]:
        try:
            lines = self._file(session_id).read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        rows: list[dict[str, Any]] = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue  # a torn line must not break reads
            if isinstance(row, dict):
                rows.append(row)
        return rows

    def state(self, session_id: str) -> dict[str, int]:
        """Cached ``{"seq": n, "turn": n}``; first call scans the existing file."""
        with self._lock:
            state = self._states.get(session_id)
            if state is None:
                rows = self._read_rows(session_id)
                self._counts[session_id] = len(rows)
                last = rows[-1] if rows else {}
                state = {"seq": _seq_of(last), "turn": _turn_of(last)}
                self._states[session_id] = state
            return state

    def append(
        self,
        session_id: str,
        kind: str,
        data: dict[str, Any],
        *,
        state: dict[str, int],
    ) -> dict[str, Any]:
        with self._lock:
            seq = int(state.get("seq") or 0) + 1
            record = {
                "v": SCHEMA_VERSION,
                "seq": seq,
                "ts": round(time.time(), 3),
                "session_id": str(session_id),
                "turn": int(state.get("turn") or 0),
                "kind": str(kind),
                "data": redact(dict(data or {})),
            }
            self.base.mkdir(parents=True, exist_ok=True)
            with open(self._file(session_id), "a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
                handle.flush()
            state["seq"] = seq
            self._counts[session_id] = self._counts.get(session_id, 0) + 1
            if self._counts[session_id] > self.max_records:
                try:
                    self._trim(session_id)
                except (OSError, ValueError, TypeError):
                    pass  # trimming is housekeeping; never lose the just-written record
            return record

    def _trim(self, session_id: str) -> None:
        path = self._file(session_id)
        rows = self._read_rows(session_id)[-self.max_records :]
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(
            "".join(json.dumps(row, ensure_ascii=False, default=str) + "\n" for row in rows),
            encoding="utf-8",
        )
        os.replace(tmp, path)
        self._counts[session_id] = len(rows)

    def read(
        self,
        session_id: str,
        *,
        limit: int = 500,
        since_seq: int = 0,
    ) -> list[dict[str, Any]]:
        try:
            limit = max(0, int(limit))
            since_seq = int(since_seq)
        except (TypeError, ValueError):
            limit, since_seq = 500, 0
        with self._lock:
            rows = self._read_rows(session_id)
        rows = [row for row in rows if _seq_of(row) > since_seq]
        if limit and len(rows) > limit:
            rows = rows[-limit:]
        return rows

    def count(self, session_id: str) -> int:
        with self._lock:
            if session_id not in self._counts:
                self.state(session_id)
            return self._counts.get(session_id, 0)


class SessionTrajectory:
    """Engine-facing recorder bound to one session; a no-op when nothing is attached."""

    def __init__(self, store: TrajectoryStore, session_id: str) -> None:
        self.store = store
        self.session_id = str(session_id)
        self._state = store.state(self.session_id)

    @property
    def turn(self) -> int:
        return int(self._state.get("turn") or 0)

    def begin_turn(self, *, mode: str = "user", input: Any = None, **fields: Any) -> None:
        try:
            self._state["turn"] = self.turn + 1
            self.record(
                "turn_start",
                mode=mode,
                input_chars=_input_chars(input),
                input_digest=value_digest(input),
                **fields,
            )
        # Local store + serialisation only: a full disk or a value we cannot encode is the
        # real surface. Anything else is a bug we want to see, not swallow.
        except (OSError, ValueError, TypeError):
            pass

    def end_turn(self, *, status: str = "ok") -> None:
        self.record("turn_end", status=status)

    def record(self, event_kind: str, **data: Any) -> None:
        try:
            self.store.append(self.session_id, event_kind, data, state=self._state)
        except (OSError, ValueError, TypeError):
            pass  # the ledger must never break a turn

    def record_code_exec(
        self,
        code: str,
        *,
        sandboxed: bool,
        bridge_calls: int,
        exit_code: Any = None,
    ) -> None:
        """Code mode 审计：exec 的代码全文 + 沙箱标记 + 桥接调用计数。"""
        self.record(
            "code_exec",
            code=code,
            code_chars=len(code),
            sandboxed=sandboxed,
            bridge_calls=bridge_calls,
            exit_code=exit_code,
        )
