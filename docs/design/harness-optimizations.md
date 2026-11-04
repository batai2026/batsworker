# Harness optimizations (v1)

> Source: `coworker/engine.py` — `_is_transient_provider_error`,
> `_truncate_tool_result`, `_EGRESS_RATE_LIMITS`, and their wiring in `_loop`
> and `_handle_tool_calls`.

Three engine-level optimizations that improve reliability, context efficiency,
and safety without changing the tool interface.

## 1. Provider transient error auto-retry

**Problem**: A 50-step agent task fails at step 48 due to a 429 rate limit —
the user must manually retry the entire flow.

**Fix**: When a provider exception occurs and NOTHING has been streamed (the
error happened before the first token), the engine checks the exception against
transient patterns (429, 502, 503, rate limit, connection reset, overloaded)
and retries with exponential backoff (2s → 4s → 8s, max 3 retries).

**Guardrails**:
- Only retries when `not streamed and not streamed_reasoning` — a retry after
  partial output would duplicate deltas.
- Auth errors (401/403), context overflow, and model errors are NOT transient.
- The retry counter resets on successful stream — a fresh error gets a fresh
  budget of retries.
- Each retry appends a transcript notice (`"Transient error — retrying (1/3)…"`)
  so the user knows what happened.

**Code**: `_is_transient_provider_error()` + `_TRANSIENT_PATTERNS` +
retry logic in `_loop`'s except block.

## 2. Tool result semantic truncation

**Problem**: `_tool_result_message` does `json.dumps(result, default=str)` with
no truncation — a 2 MB `read_file` result enters the conversation history
verbatim, consuming context window and inflating every subsequent turn's
prompt cost.

**Fix**: `_truncate_tool_result(tool_name, content)` applies head+tail
truncation when content exceeds `_MAX_TOOL_RESULT_CHARS` (16,000 chars ≈ 4k
tokens): first 60% + last 20% are preserved, with a
`[.. N chars truncated — re-read with a narrower query if needed ..]` marker
in between.

**Why head+tail**: File beginnings carry imports and headers; file endings
carry conclusions and summaries. The model can re-read specific portions via
windowed tools (`read_file(start_line=…)`) if the omitted middle matters.

**Tool-agnostic**: The same truncation applies to all tools — `read_file`,
`browser_read_page`, `grep`, anything that returns a large string. The
threshold is char-based (not token-based) for speed; the 16k char ≈ 4k token
ratio is close enough for a coarse filter.

## 3. EGRESS tool per-turn rate limiting

**Problem**: `max_iterations` (150) is the only call-count limit. An agent in
Auto-Approve mode could send 50 messages or make 50 web fetches in a single
turn without any frequency check.

**Fix**: Per-tool per-turn rate limits for EGRESS-classified tools:

| Tool | Limit per turn |
|---|---|
| `send_message` | 10 |
| `web_fetch` | 20 |
| `web_search` | 15 |
| `browser_open_url` | 20 |

When the limit is reached, the tool is refused BEFORE authorization (no
approval card is shown for a call that will be throttled). The tool result
tells the agent: `"rate limited: send_message has been called 10 times this
turn (limit 10). Try a different approach or summarize what you have so far."`

**Counting**: All authorized calls count (including ones that fail at
execution — the intent to call is what consumes the rate budget). Counters
reset per user turn (`_loop` entry).

**Read tools are NOT rate-limited** — reads are cheap and the agent may need
many to complete a task. The limit targets EGRESS operations where each call
has real-world consequences.

## Tests

`tests/test_harness_optimizations.py` — 11 tests:
- Truncation: short unchanged, long head+tail, structure preservation,
  cross-tool consistency
- Transient detection: positive patterns (429/503/rate limit/connection) and
  negative patterns (401/context window/invalid model)
- Engine retry: 429 → retry succeeds (provider called twice, retry notice in
  transcript); auth error → no retry
- Rate limiting: default config sanity, threshold blocking
