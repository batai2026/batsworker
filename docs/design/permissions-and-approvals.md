# Permissions & approvals

> Source: `coworker/permissions.py` (`PermissionEngine`),
> `coworker/risk.py` (`RiskClass`), `coworker/reviewer.py` (Auto-Approve
> reviewer), the authorize path in `coworker/engine.py`.

This is the subsystem that decides, for every single tool call, whether it
runs, asks, or is refused. The governing principle: **fail closed, and no
convenience feature can ever widen what a human hasn't approved.**

## Permission modes

`PermissionMode` (`permissions.py:214`):

| Mode | Value | Meaning |
|---|---|---|
| `DISCUSS` | `discuss` | Read-only conversation: no edits, no planning workflow |
| `PLAN` | `plan` | Same read-only gate as DISCUSS; differs only in intent — drives the agent toward a `propose_plan` approval |
| `INTERACTIVE` | `interactive` | Ask for approval (default) |
| `BYPASS_APPROVALS` | `bypass-approvals` | Full access minus the hard floors |
| `AUTO_APPROVE` | `auto-approve` | INTERACTIVE plus an LLM reviewer that may pre-approve cards |
| `CUSTOM` | `custom` | INTERACTIVE plus auto-allow of the config's `auto_allow` tools |

Naming history: `bypass-approvals` was renamed from `"auto"` (spec §1.5,
2026-08-12) so that "safety system off" can't be confused with `auto-approve`
in a picker. Without a reviewer plugged in, `AUTO_APPROVE` behaves exactly
like `INTERACTIVE`.

DISCUSS and PLAN share one enforcement gate (read-only); the split is about
steering the model, not about a second permission path — OPE-117's review
follow-up made sure even those modes still show a card where one is owed.

## Risk classes

Every tool call is classified first (`risk.py:18`):

| Class | Value | Rationale (from code comments) |
|---|---|---|
| `READ` | `read` | No side effects — always allowed |
| `EGRESS` | `egress` | Reaches the network — the request itself can carry data off-machine |
| `WRITE_LOCAL` | `write_local` | Mutates the workspace — path-scoped + mode-gated |
| `EXEC` | `exec` | Runs commands — mode-gated |
| `EXTERNAL` | `external` | Side effects off the machine — the unattended-Inbox hook |

`send_message` is gated as EGRESS because the message body is model-chosen
free text over the same outbound channel (spec §2.2); `browser_open_url` is
classified the same way ("the same channel by another name", OPE-111).

## Hard floors

`PermissionEngine.evaluate()` (`permissions.py:326`) returns a `Decision`
(allowed / reason / needs-user / human-only / rule-matched). Certain floors run
**before** any mode, allowlist, or reviewer gets a say:

1. **Self-protection** — `protected_paths()` (`permissions.py:93`): the agent
   may not edit OpenWorker's own settings, secrets, or permission config.
2. **Writable roots** — writes outside the granted workspace roots are denied
   outright.
3. **Deferred execution** — `.git/hooks`, CI configs, and similar
   "execute-later" files are human-only: approving a file write is not
   approving the code that runs unattended afterwards (OPE-117).
4. **Persistent authority** — `PERSISTENT_AUTHORITY_TOOLS`
   (`permissions.py:85`): `save_skill` and scheduled-task CRUD can install
   future behavior, so they are always human-only.
5. **Provenance floor (OPE-114 §1)** — running something the agent *downloaded
   this session* is the classic fetch-then-execute attack, so it forces a human
   card even in Auto-Approve mode, overriding both the reviewer and command
   allowlists. The engine tracks this session-scoped provenance set; the
   reviewer never even sees those cards to judge.

Mode-level config obeys the same direction: repository config can only
*narrow* permissions (`_GLOBAL_ONLY_FIELDS` in `config.py`) — a checked-in
file can never grant capabilities the user didn't already have.

## The authorize pipeline

`engine.py::_authorize` runs, per tool call:

```
PermissionEngine.evaluate()
   → provenance floor (OPE-114)          [human card, overrides everything]
   → Auto-Approve reviewer (if plugged)  [may turn ask→allow, never blocked→allow]
   → PERMISSION_REQUIRED event + approver callback
        → ApprovalOutcome: once | always_tool | always_command
                           | always_domain | readonly_session | deny
```

Approval cards are interactive (they stream as events and await the injected
async `approver`), and every unanswered one is also parked as a persisted
Inbox item first, so a dropped socket or restart loses nothing
([server-and-auth.md](server-and-auth.md)).

Standing rules: task-scoped entries ("tool → exact target") auto-allow
matching calls for automation runs — scoped to the exact target, not the whole
tool.

## The Auto-Approve reviewer

`coworker/reviewer.py::Reviewer` runs only in `Mode.AUTO_APPROVE`. It is an
LLM judge using **the same provider and model as the session**, pre-consulted
on all would-be cards at once (`_preconsult_reviewer`) so one judgment covers
the batch cheaply.

Invariants:

- It can only turn **ask into allow**. A call the permission engine blocked
  stays blocked; the reviewer has no authority to widen policy.
- On **deny**, the agent receives a terse refusal (full reasoning is shown to
  the *user*, plus a one-shot "Allow anyway").
- **Breaker**: 5 consecutive denials in a row trip `_REVIEWER_TRIP` and pause
  auto-approve for the rest of the turn. (Streak semantics per owner ruling
  2026-08-24: raised 2→5 and made cumulative-aware, because a naive 2-count
  silently punished legitimate back-and-forth.)
- **Shadow mode** records reviewer verdicts without touching decisions — used
  for the evaluation corpora in `tests/corpora/` and the reports in
  `reports/`.

## Where grants live

`always_*` outcomes mint **session grants** — they widen nothing beyond the
current session, and the self-protection/deferred-execution floors still apply
on top (a grant never overrides a floor). Automation tasks carry their own
task-scoped `always_allowed_tools` rules, see [automation.md](automation.md).
