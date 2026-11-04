# Turn checkpoints ("time travel")

> Source: `coworker/checkpoints.py` (`SessionCheckpoints`), wired in
> `coworker/engine.py` (turn boundaries + capture) and
> `coworker/server/manager.py` (ownership + REST). UI: the rail's Checkpoints
> section (`RightRail.tsx`).

Every turn that modifies tracked files leaves one **checkpoint**: a record of
each modified file's pre-turn content, stored content-addressed under the state
dir. The user can restore the session's files to the state at the end of any
checkpointed turn — one click in the rail, confirmed inline.

## Capture

- **Seam**: `engine._execute_sync` — the single funnel every tool call passes
  through (runs in the worker thread, so capture is plain sync I/O). Only
  `risk.WRITE_TOOLS` (`write_file`, `replace_in_file`, `apply_patch`,
  `apply_unified_diff`) capture; the target paths come from
  `permissions.write_paths` (patch/diff blobs are parsed for file headers),
  resolved with `provenance.resolve`, and accepted only inside a **writable
  root** (late-bound via the engine's live `permissions.roots`).
- **First capture wins**: within a turn, a path's earliest pre-content is the
  rollback truth; later writes to the same path in the same turn add nothing.
- **Success-gated**: the entry attaches to the pending turn only when the tool
  call SUCCEEDED (`note_write`); a failed/interrupted call discards it — a write
  that raised left nothing on disk to roll back.
- **Honest gaps**: files over `MAX_FILE_BYTES` (20 MB) or unreadable ones are
  recorded as `skipped`; rollback reports them as unrestorable and leaves them
  untouched — never silently keeping or clobbering content it can't vouch for.
  Shell-caused changes (`sed`, git, builds) are invisible to capture by design;
  the UI labels checkpoints as covering file-tool writes.

## Turn boundaries

`run()` opens a pending turn beside `session_facts.begin_turn()` and finalizes
it in a `finally` around the loop — a crash or kill mid-turn leaves an
unfinalized pending turn that the next load discards (fail toward less history,
the same defensive posture as the other stores). Turns with no tracked writes
produce no record: checkpoint seqs have gaps, and the first write-bearing turn
of a session is the oldest rollback point (there is no pre-session baseline in
v1).

## Rollback

`rollback_to(K)` restores, for every file **first** modified after K, the
content captured at that first post-K modification (exactly the state at the
end of turn K), deletes files whose pre-K content was "absent", and truncates
the turn list to K. Refusals: while a turn is running (`manager.is_running`)
or mid-flight captures exist (`CheckpointBusy`); unknown seqs raise
`CheckpointNotFound` → 404-shaped error JSON. Conversation history is NOT
rewound in v1 — the surface posts a transcript notice so the agent knows a
rollback happened. Blob GC is deliberately absent: content-addressed blobs are
deduped, orphaned ones are bounded, and a sweep racing an in-flight capture
would need locking v1 doesn't have.

## Surface

- Storage: `<data-dir>/checkpoints/<session_id>/blobs/<sha[:2]>/<sha>` +
  `turns.json` (atomic tmp+replace; defensive parse). Never inside a writable
  root — the agent must not reach its own undo history. Session ids are
  sanitized before naming a path.
- REST: `GET /v1/sessions/{id}/checkpoints` (works without a live engine —
  reads the persisted list) and
  `POST /v1/sessions/{id}/checkpoints/{seq}/rollback`.
- Rail UI: a **Checkpoints** section (only when history exists) listing turns
  newest-first — `#seq · time · N files [· M skipped]` — with a two-step
  inline-confirmed rollback button (same vocabulary as the two-step delete);
  success closes the artifact viewer and refreshes artifacts, and a status line
  reports restored/removed counts. All strings ride the i18n pipeline
  (`MessageKey`-typed, zh entries in `i18n/zh/cards.ts`).
- Tests: `tests/test_checkpoints.py` (capture/rollback round trips, earliest-
  capture-wins, nested-file deletion, out-of-root refusal, failed-write
  discard, skipped-file honesty, cross-restart persistence, busy/unknown guards,
  path-traversal safety); hermetic e2e mocks in `e2e/fixtures.ts`.
