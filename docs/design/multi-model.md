# Multi-model routing (fast model + escalation infrastructure)

> Source: `coworker/config.py` (`fast_model`), `coworker/agent.py::build_engine`
> (reviewer fallback chain), `coworker/engine.py` (`_select_model`,
> `_compact_now` fallback chain), `manager.py` (config pass).

A session can specify a **fast model** — a lighter model for utility calls
that don't drive the main agent loop. The main loop always uses the strong
model; utility calls default to the fast model when set.

## Fallback chain (per call site)

| Call site | Chain |
|---|---|
| **Compaction** | `compaction_settings["model"]` > `fast_model` > session model |
| **Reviewer** | `reviewer_model` (config) > `fast_model` (config) > session model |
| **Main loop** | `self._select_model()` → `self.model` (v1: always session model) |

## Design rationale

**Utility calls first, main loop later**: Compaction is the most expensive
utility call (it processes the entire span). Routing it to a fast model saves
significant tokens without affecting agent quality — the summary is for the
agent's reference, not for execution. The reviewer benefits similarly (the
judgment is binary: allow/deny/unsure). The main loop is deliberately NOT
routed in v1 — mid-turn model switching risks inconsistent behavior.

**`_select_model()` infrastructure**: The engine calls `_select_model()`
before each `_astream()` round-trip. The default returns `self.model`. A v2
override can implement iteration-aware escalation (fast model for early
read-heavy iterations, strong model for synthesis) without changing the
engine's core loop.

## Config

| Field | Default | Meaning |
|---|---|---|
| `fast_model` | `None` | Lighter model for utility calls (user-global) |
| `reviewer_model` | `None` | Reviewer-specific override (beats `fast_model`) |

Fallback chain: explicit override > `fast_model` > session model. A repo
config can't set either (both are `_GLOBAL_ONLY_FIELDS`).

## Tests

`tests/test_multi_model.py` — 9 tests: config settable, `_select_model`
defaults to session model (even with fast_model set), main loop uses session
model (not fast_model), compaction fallback chain (3 cases), reviewer
fallback chain.
