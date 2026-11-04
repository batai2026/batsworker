# Agent observability (span timeline + OTLP export)

> Source: `coworker/observability.py` (TraceStore, SessionTracer, to_otlp_json),
> wired in `coworker/engine.py` (`_execute_sync` tool spans, `run()` turn spans,
> `_record_result` duration on TOOL_FINISHED), `manager.py` (attach + export),
> `surfaces/gui` (per-step duration display).

Every tool execution becomes a **span** — name, session, start/end, status,
duration — persisted locally in a dedicated SQLite store. Two consumers:

1. **Transcript tool cards**: the TOOL_FINISHED event now carries
   `duration_ms`; the StepRow renders it as a quiet label (`120 ms`, `3.4 s`)
   — the user sees *how long* each step took without opening anything.
2. **`GET /v1/sessions/{id}/timeline`**: the raw span list for tooling or the
   future timeline UI.

**OTLP export is double opt-in**: `config.otlp_endpoint` must be set AND
`POST /v1/observability/export {session_id}` must be called. Nothing leaves
the machine unless the user both configures an endpoint and explicitly
exports. The export shape is OTLP/HTTP-JSON (standard `resourceSpans`
structure) — compatible with any OTLP collector.

## Design decisions

- **Separate store from the audit log**: the audit answers "what did the agent
  DO" (sanitized arguments, OPE-101 retention); the trace answers "how long
  did it take and what did it cost". Different questions, different retention,
  different export.
- **No file contents or tool results in spans** — names and metadata only, so
  even the local store can't leak content if read.
- **Turn span** wraps each `run()` (started in the begin block, finished in the
  finally alongside checkpoints); **tool spans** wrap each `_execute_sync`
  call. The LLM round-trip span is deferred to v2: it needs a clean seam inside
  `_astream` that doesn't exist yet.
- **Never breaks the turn**: all tracer calls are `try/except`-wrapped, same
  posture as checkpoints.
