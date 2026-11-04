# Compaction quality improvements

> Source: `coworker/compaction.py` (`_validate_sections`, `_EXPECTED_SECTIONS`,
> updated `CONTINUATION_CONTRACT`), `coworker/engine.py::_compact_now`
> (checkpoint cross-reference).

The compaction summary prompt already had 8 structured sections (primary
request, key concepts, artifacts, errors, user messages, pending tasks,
current work, next step). Three improvements close the loop on quality:

## 1. Section validation

`summarize_span` now calls `_validate_sections(summary)` after the model
returns. Missing section headings are **logged as warnings** — never raised,
because a partial summary is better than no summary. Over time, the warning
pattern surfaces systematic quality issues (e.g. a model that consistently
skips "Errors and fixes") instead of silently swallowing them.

## 2. Checkpoint cross-reference

`_compact_now`'s notice now includes the latest checkpoint seq when the
session has recorded file checkpoints: `"Context compacted — earlier turns
were summarized. File states are preserved in checkpoints (latest: #3)."`

**Why**: after compaction, the agent's only memory of file contents is the
summary text — which explicitly says "do NOT carry full file contents as
truth." Without the checkpoint reference, the agent had no way to recover a
prior file version if a recent edit went wrong. Now it knows the checkpoint
system has the snapshots.

## 3. Continuation contract updated

`CONTINUATION_CONTRACT` (the instructions the agent sees after compaction)
now mentions checkpoints: "File states from earlier turns are preserved in
the session's checkpoints — you can recover a prior version via the
checkpoint system if a recent edit went wrong."

## Tests

`tests/test_compaction_quality.py` — 7 tests: section validation (all
present, missing some with correct count, empty summary), summarize_span
integration (partial and full), continuation contract checkpoint mention,
engine `_compact_now` checkpoint cross-reference.
