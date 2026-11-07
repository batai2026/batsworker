# Eval & replay: turn ledger, fork/replay, harness registry, eval matrix, capture

> Source: `coworker/trajectory.py` (ledger), `coworker/server/manager_sessions.py`
> (fork / replay / trajectory diff), `coworker/personas/harness.py` (harness
> registry), `coworker/eval/matrix.py` (model × harness matrix),
> `coworker/eval/capture.py` (record → case export).

The observability stack answers "how long did each step take" (spans). This
document covers the other half: **what exactly was sent to the model, what the
model asked to run, and how to replay and compare it** — the foundation for
offline evaluation and regression testing of models, harnesses, and prompts.

Five pieces, in dependency order:

1. **Turn ledger** — append-only, redacted, per-session event log.
2. **Fork & replay** — cut a session at any turn boundary; re-run inputs.
3. **Harness registry** — one source of truth for "mode = composition".
4. **Eval matrix** — re-run a recorded session across models × harnesses.
5. **Eval capture** — one-command export of a real session into a matrix case.

## 1. Turn ledger (`coworker/trajectory.py`)

### Why

The span store (`observability.py`) records tool timing only. System prompts,
context injections, compaction boundaries, approval decisions, and the actual
outbound model requests left no trace — so there was no way to diff two runs of
the same session or even answer "what did we send the model in turn 3".

### Design principles

- **Metadata and digests only, never content.** Outbound requests, tool
  arguments, and tool results are recorded as `sha256` digests plus byte/char
  counts and argument *key names* — never message bodies, command text, or file
  contents. The ledger can verify a replay without duplicating the conversation
  or creating a new exfiltration surface.
- **Redaction at the write layer.** `redact()` recursively replaces values of
  secret-looking keys (`token`, `secret`, `password`, `cookie`, `api_key`,
  `private_key`, …) and known secret prefixes (`Bearer`, `sk-`, `ghp_`,
  `xox[abprs]-`, `AKIA`, `AIza`, PEM headers); strings are truncated to 1200
  chars.
- **The ledger must never break a turn.** `SessionTrajectory.record()`
  swallows all exceptions; the engine wraps every write in a `_traj()` helper
  with the same contract.
- **On by default, local-first.** Files live in `<data-dir>/trajectory/
  <session>.jsonl` next to `traces.db` — outside the workspace, so the agent
  cannot rewrite its own ledger. Both config keys (`trajectory_enabled`,
  `trajectory_max_records = 5000`) are **user-global only**: a repo config
  cannot disable the ledger.
- **Retention by count.** Past `trajectory_max_records`, the file is
  atomically rewritten (`<file>.tmp` + `os.replace`) keeping the newest N.
- **Turn boundaries cover `run()` only.** `retry()`/`resume()` reuse the
  current turn number rather than adding boundary events.

### Record model

One JSON object per line (`{"v":1,"seq":…,"ts":…,"session_id":…,"turn":…,"kind":…,"data":{…}}`):

| kind | data |
|---|---|
| `turn_start` | `mode`, `input_chars`, `input_digest`, `has_source`, `has_display`, **`message_count`** |
| `turn_end` | `status` |
| `model_request` | `model`, `messages` (count), `bytes`, `digest`, `roles`, `tools` (names), `settings` (key names) |
| `context_injection` | `chars`, `digest` |
| `tool_call` | `name`, `call_id`, `argument_keys`, `arguments_digest`, `arguments_chars` |
| `tool_result` | `name`, `call_id`, `status`, `duration_ms`, `result_digest`, `result_chars`, `reason` |
| `approval_requested` / `approval_resolved` | `name`, `call_id`, `reason` / `status`, `outcome` |
| `notice` | `kind`, `text`, scalar fields |
| `code_exec` | code-mode executions: code + `sandboxed` + bridge call count |

Write points in the engine: `run()` (turn boundaries), `_astream()`
(`model_request` right before `produce()`), `_outbound_messages()`
(`context_injection`), `_execute_sync()` (`tool_call` / `tool_result`,
including interrupted/mangled/denied/unknown outcomes), `_authorize()`
(approval request/resolution), `_append_notice()` (errors, retries, compaction,
budget notices).

### Read API

`GET /v1/sessions/{id}/trajectory?limit=500&since_seq=0` →
`{session_id, count, records}`. Read-only, side-effect free; historical files
remain readable even with `trajectory_enabled=false`.

`turn_start.message_count` is recorded *after* the user message is appended,
so `message_count - 1` is the index of that turn's user message — the
authoritative turn → message-index mapping that fork, matrix, and capture all
share.

## 2. Fork & replay (`coworker/server/manager_sessions.py`)

### Fork semantics: "cut before turn N"

`POST /v1/sessions/{id}/fork` with optional `{"turn": N}`:

- With `turn`: the new session contains all messages *before* turn N (the cut
  point is resolved **only from the ledger** — steering/injected messages are
  also `role=user`, so counting messages is unreliable). Missing ledger or
  unknown turn → 400.
- Without `turn`: a tip fork copies everything.

Fork copies **session state only, not workspace/file state**: both branches
share the workspace directory, and later file edits in the source are not
rolled back (workspace rollback is the checkpoint API's job — orthogonal).
Trajectory and checkpoints are not copied; the fork numbers its own ledger
from turn 1.

Inheritance rules:

| Field | Rule |
|---|---|
| workspace / model / mode / agent / extra_roots / grants / bindings | inherited (grants = same user's intent in the same lineage) |
| team | **not** inherited — a fork is a plain session |
| compaction | tip fork only (a mid-cut invalidates the anchor) |
| title | source title truncated + `" (fork)"` (≤80 chars) |
| lineage | `SessionRecord.fork = {"from_session", "from_turn", "cut_messages"}` persisted in the sessions table |

### Replay

`POST /v1/sessions/{id}/replay` with optional `{"input": "…"}` → 202:

- Without `input`: replays the recorded user message at the fork's cut point
  (`input_source: "lineage"`); no lineage → 400.
- With `input`: uses the given text (`input_source: "override"`).
- Execution reuses the background delivery channel (`deliver_to_session`), so
  approvals flow through the normal Inbox/Unattended pipeline — replay never
  bypasses permissions. A running target session → 409.

### Trajectory diff

`GET /v1/sessions/{id}/trajectory/diff?other={sid}&from_turn=1&other_from_turn=1`
aligns both sides' `model_request` records in order and compares digests,
returning per-pair `{turn_left, turn_right, digest_match, bytes_left,
bytes_right, model_left, model_right}` plus `matched`/`compared` totals. Fork
sides have a natural turn offset (the fork renumbers from 1), so the caller
supplies both start turns; the fork response includes a `diff_hint` URL with
suggested parameters.

### Fail-closed rules

Internal (`__`-prefixed) sessions → 403; forking a running source → 409;
fork is read-only against the source session; no new config switches — both
operations are explicit user actions.

## 3. Harness registry (`coworker/personas/harness.py`)

**Problem**: the runtime preset was the persona manifest, but the eval matrix
kept its own hardcoded `HarnessSpec`s with duplicated prompt texts that had
already drifted from the manifests, and user-installed personas could not be
evaluated as harnesses.

**Fix**: a harness (capability slots + system prompt + permission mode +
folder gate) is derived from **one source** — the persona manifest; only
`code` has no manifest and uses the in-code `CODE_CAPABILITIES /
CODE_INSTRUCTIONS`.

```python
@dataclass(frozen=True)
class HarnessSpec:
    name: str
    capabilities: tuple[str, ...]
    system_prompt: str
    mode: str = "interactive"
    requires_folder: bool = True
    source: str = "manifest"        # "manifest" | "builtin"
```

- `builtin_specs()` — minimal / creator / code-mode parsed from
  `personas/builtin/<id>/manifest.md`, plus `code` from code constants.
- `installed_specs()` — builtin ∪ all installed manifest personas; registry
  construction failures never block the builtins (falls back to
  `builtin_specs()`).
- `resolve(name)` — raises `ValueError("unknown harness: …")`.
- `eval/matrix.py` derives `HARNESSES = builtin_specs()`; `run_matrix()`
  defaults to `installed_specs()`, so any installed persona id is a valid
  `--harnesses` entry. Reports carry `harness_sources`.

Recorded consequence: `minimal`/`creator`/`code-mode` prompts now come from
manifest bodies and `code` uses `CODE_CAPABILITIES` (includes `egress`) — not
byte-comparable with pre-registry reports. That is deliberate: the eval
profile must track the runtime profile.

## 4. Eval matrix (`coworker/eval/matrix.py`)

Re-runs every recorded user turn of a session under a fixed (harness, model)
cell and collects the ledger's `model_request` summaries into a comparable
report.

- **Recording side**: `recorded_turns(session_id)` maps turns to message
  indices via `turn_start.message_count` (the same mapping fork uses) and
  reads raw user text from `conversations/<sid>.jsonl` — not redacted, so the
  input is complete.
- **Execution side**: each cell gets a fresh engine (`build_engine(agent=
  harness_agent(spec), model=…, mode=…, provider=…)`) plus a temporary
  `SessionTrajectory` — cells never pollute each other or the real ledger.
- **No approvals by default**: the engine denies tool calls unless
  `--auto-approve` is passed.
- **`--stub`**: a deterministic fake provider (no network, no keys) so pytest
  can cover the scaffolding; real runs go through `providers.router` with
  explicit `--models`.
- **Report**: one JSON object per cell with `status`, per-turn
  `digest`/`bytes`/`tools`, plus iterations/duration/error. Identical digests
  mean the model produced byte-identical outbound payloads under that harness.

## 5. Eval capture (`coworker/eval/capture.py`)

Closes the loop from a real session to an eval asset. Previously, building a
matrix input meant hand-assembling messages and turn boundaries from two
JSONL files.

`python -m coworker.eval.capture <session-id> --out <dir>` exports:

- `messages.jsonl` — one line per turn: `{"turn": n, "input": <raw text>}`
  (boundaries via `recorded_turns()`).
- `baseline.json` — `{session_id, captured_at, turns: [{turn, model, bytes,
  requests, tool_calls, tools}]}`, where `model`/`bytes`/`requests` come from
  `model_request` summaries and `tools` is the turn's `tool_call` name
  sequence.

**Privacy gate**: before writing anything, capture scans the ledger for the
redaction marker `[redacted]` — a hit means the session touched a secret, and
export is refused (`--force` overrides; CLI exits 2 with a stderr hint). The
message bodies themselves are *not* redacted (eval needs raw input), so the
gate guards the fact of secret contact, not the content.

`matrix.py --case <dir>` consumes the package (mutually exclusive with
`--session`), and each report cell gains a `vs_baseline` block:
per-turn `{bytes, base_bytes, bytes_delta, tools, base_tools, tools_match}`.

**Reference by default, gate on request.** The baseline never fails a run
unless the caller asks: `--check-baseline` turns the comparison into an
assertion, judging each turn against four failure kinds — `cell_error`
(execution failed), `missing_turn` (the baseline has no such turn),
`tool_drift` (the actual `tool_call` name sequence differs), and
`bytes_drift` (outbound bytes moved more than `--max-bytes-ratio`, default
10%; a zero-byte baseline treats any bytes as drift). The verdict lands in
`report["baseline_check"]` and the CLI exits **3** on drift (distinct from
**1** for a failed cell), so CI can gate on it without losing the run-failure
signal.

## Non-goals & follow-ups

- No GUI for any of the above (branch tree, diff viewer, audit panel).
- No workspace snapshot/restore in fork (use the checkpoint API).
- No cross-harness model selection or historical report archiving.
- Batch replay and code-mode-specific replay special-casing are not
  implemented.
