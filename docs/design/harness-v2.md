# Harness optimizations v2 (delta batching, execution waves, prompt cache)

> Source: `coworker/engine.py` — delta batching in `_loop`, execution waves in
> `_handle_tool_calls`/`_plan_execution_waves`, prompt cache documentation.

## 1. Delta batching (streaming efficiency)

**Problem**: Every streamed delta yielded an event. A 2000-token output produces
~500+ events, each triggering IPC serialization and GUI re-render.

**Fix**: Deltas are buffered in `text_buffer` / `reasoning_buffer` and flushed
on a combined time+size window (50ms OR 100 chars, whichever comes first).
The final flush happens when the stream ends. Error/interrupt paths also flush
so partial output is never lost.

**Implementation**: `_loop` streaming section — `_should_flush()` checks both
thresholds, `_flush_deltas()` yields combined events. `streamed`/`streamed_reasoning`
accumulators are unaffected (they always get every delta for history).

**Effect**: Event volume drops from ~500 to ~20 for a typical response. The
GUI receives the same final text (joined from the same `streamed` list), just
in fewer, larger deltas.

## 2. Execution waves (tool dependency DAG)

**Problem**: `_parallel_safe` used a binary split — low-risk tools ran
concurrently, everything else ran serially. Three independent writes to
different files would be serialized unnecessarily.

**Fix**: `_plan_execution_waves(cleared)` replaces the binary split with
**greedy wave assignment** based on file path conflict analysis:

1. Extract write paths and read paths per call (via `write_paths` + `resolve`)
2. A call **conflicts** with a wave if its write paths overlap the wave's
   read OR write paths, or its read paths overlap the wave's write paths
3. Each call is assigned to the first wave with no conflict (greedy, order-
   preserving — the model's call order is respected within a wave)
4. Calls with no extractable paths (shell commands, gate tools) fall back to
   `_parallel_safe()` metadata: low-risk tools share a wave, others get
   their own serial wave

**Result**: A write to `a.md` and a write to `b.md` now run in parallel
(same wave). A write to `out.md` followed by a read of `out.md` are
serialized (different waves). Read-only batches behave as before.

**Constraint**: Wave assignment is order-preserving (the model's call order
within a wave is maintained). Reads of the same file don't conflict (both
can read in parallel). Only write-read and write-write conflicts force
separation.

## 3. Prompt cache audit

**Finding**: The existing `_outbound_messages()` architecture is already
cache-friendly — the context provider (clock, directory listing, skill menu)
injects into the **last user message only**, so the message prefix (system +
all previous turns + tool results) is byte-stable across calls.

**Cross-turn caveat**: The context block moves to each new user message, so
the previous user message loses its context block between calls — causing a
cache miss at that position. This is a known trade-off of the live
clock/environment pattern (owner ruling 2026-08-20: stale timestamps caused
bad reasoning). Fixing it would require persisting the injected context per
message, which conflicts with the "never mutates self.messages" invariant.

**Validation**: `test_prompt_cache_within_turn_stable` (same messages → same
outbound) and `test_prompt_cache_cross_turn_prefix_stable` (system message
byte-stable across turns, context block on last user message only).

## Tests

`tests/test_harness_v2.py` — 6 tests:
- Wave planning: low-risk parallel (no paths), high-risk serial, write+read
  same file serialized, writes to different files parallel
- Prompt cache: within-turn stability, cross-turn system message stability
