"""Agent observability (v1) — span-level trace records, local by default.

Every tool execution becomes a **span**: kind, name, session, turn (trace),
start/end timestamps, status, and free-form meta (args digest, tokens). Spans
land in a dedicated SQLite store under the data dir and power two things:

  - `GET /v1/sessions/{id}/timeline` — the raw span list (the transcript shows
    per-tool durations from the TOOL_FINISHED event instead).
  - `POST /v1/observability/export` — push the session's spans to a configured
    OTLP/HTTP-JSON endpoint (``config.otlp_endpoint``). Explicit, manual, and
    off by default: **nothing leaves the machine unless the user both
    configures an endpoint and calls the export.**

Design notes:
  - A separate store (not the audit log): the audit answers "what did the agent
    DO" with sanitized arguments and its own retention policy (OPE-101); the
    trace answers "how long did it take and what did it cost". Different
    questions, different retention, different export surfaces.
  - Spans never contain file contents or tool results — names and metadata
    only, so exporting is safe even though the user must still opt in.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional


class TraceStore:
    """Thread-safe span store (the scheduler, WS handlers, and tool worker
    threads all touch it)."""

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript("""
                CREATE TABLE IF NOT EXISTS spans (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    trace_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    name TEXT NOT NULL,
                    started REAL NOT NULL,
                    ended REAL,
                    status TEXT,
                    duration_ms INTEGER,
                    meta TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_spans_session
                    ON spans(session_id, started DESC);
            """)
            self._conn.commit()

    def start_span(
        self,
        *,
        session_id: str,
        trace_id: str,
        kind: str,
        name: str,
        meta: Optional[Dict[str, Any]] = None,
    ) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO spans (session_id, trace_id, kind, name, started, meta) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    session_id,
                    trace_id,
                    kind,
                    name,
                    time.time(),
                    json.dumps(meta or {}, ensure_ascii=False),
                ),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def finish_span(self, span_id: int, *, status: str, meta: Optional[Dict[str, Any]] = None) -> None:
        with self._lock:
            row = self._conn.execute(
                "SELECT started, meta FROM spans WHERE id=?", (span_id,)
            ).fetchone()
            if row is None:
                return
            ended = time.time()
            duration = int((ended - float(row["started"])) * 1000)
            existing = json.loads(row["meta"] or "{}")
            if meta:
                existing.update(meta)
            self._conn.execute(
                "UPDATE spans SET ended=?, status=?, duration_ms=?, meta=? WHERE id=?",
                (
                    ended,
                    status,
                    duration,
                    json.dumps(existing, ensure_ascii=False),
                    span_id,
                ),
            )
            self._conn.commit()

    def session_spans(self, session_id: str, *, limit: int = 500) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM spans WHERE session_id=? ORDER BY started DESC LIMIT ?",
                (session_id, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def close(self) -> None:
        with self._lock:
            self._conn.close()


class SessionTracer:
    """The engine-facing object (attached post-construction, like checkpoints):
    a TraceStore bound to one session id, with a cheap no-op path when tracing
    is not attached."""

    def __init__(self, store: TraceStore, session_id: str) -> None:
        self.store = store
        self.session_id = session_id

    def start_span(self, kind: str, name: str, meta: Optional[Dict[str, Any]] = None) -> Optional[int]:
        try:
            return self.store.start_span(
                session_id=self.session_id, trace_id=self.session_id, kind=kind, name=name, meta=meta
            )
        except Exception:
            return None

    def finish(self, span_id: Optional[int], *, status: str = "ok", meta: Optional[Dict[str, Any]] = None) -> None:
        if span_id is None:
            return
        try:
            self.store.finish_span(span_id, status=status, meta=meta)
        except Exception:
            pass


def to_otlp_json(spans: List[Dict[str, Any]], *, resource_name: str = "openworker") -> Dict[str, Any]:
    """Minimal OTLP/HTTP-JSON trace export shape (spec: application/json body of
    the standard ExportTraceServiceRequest). One resource, spans grouped by
    session (trace_id = session id — v1 treats the session as the trace)."""
    by_session: Dict[str, List[Dict[str, Any]]] = {}
    for span in spans:
        by_session.setdefault(span["session_id"], []).append(span)
    scope_spans = []
    for session_id, rows in by_session.items():
        scope_spans.append(
            {
                "scope": {"name": "coworker.engine"},
                "spans": [
                    {
                        "traceId": _otlp_id(session_id),
                        "spanId": _otlp_id(str(span.get("id", ""))),
                        "name": f'{span["kind"]}/{span["name"]}'[:255],
                        "kind": "SPAN_KIND_INTERNAL",
                        "startTimeUnixNano": str(int(float(span["started"]) * 1e9)),
                        "endTimeUnixNano": str(
                            int(float(span["ended"] or span["started"]) * 1e9)
                        ),
                        "attributes": [
                            {"key": "openworker.status", "value": {"stringValue": span.get("status") or ""}},
                            {"key": "openworker.duration_ms", "value": {"intValue": span.get("duration_ms") or 0}},
                        ],
                    }
                    for span in rows
                ],
            }
        )
    return {
        "resourceSpans": [
            {
                "resource": {
                    "attributes": [
                        {"key": "service.name", "value": {"stringValue": resource_name}}
                    ]
                },
                "scopeSpans": scope_spans,
            }
        ]
    }


def _otlp_id(text: str) -> str:
    """OTLP ids are 8/16-byte hex. Derive a stable 16-byte id from arbitrary text."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]
