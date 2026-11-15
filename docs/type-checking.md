# Type checking (Python)

The Python tree is checked with `mypy`, but the gate is a **ratchet**, not a
zero-error threshold. This file explains why, and how to work with it.

## Run it

```bash
python -m pip install -e ".[dev]"     # brings in mypy
python tools/typecheck.py             # the gate: fails only on regressions
python tools/typecheck.py --report    # print status, never fail
python tools/typecheck.py --update    # lock in an improvement
```

`tools/typecheck.py` shells out to `mypy` and compares the result against
`tools/typecheck_baseline.json`.

## Why a ratchet

The state of the tree was measured before the gate existed (2026-11-14):

| | |
|---|---|
| modules analysed | 241 |
| code annotated | ~90.7% |
| errors | 398 over 71 files |
| error-free files | 170 (70.5%) |

Of those 398 errors, the largest single group is one **systemic** pattern rather than
398 independent bugs: `[index]` errors, 96 of them
`Value of type "dict[str, Any] | None" is not indexable` -- a repeated
Optional-narrowing idiom. Fixing them properly is a refactor, not a lint pass.

The counts are **platform-dependent**, and that is deliberate in how the gate treats
them: this code branches on `sys.platform` (`os.getpgid`/`killpg` are POSIX-only;
`CREATE_NEW_PROCESS_GROUP`, `CTRL_BREAK_EVENT` and `WinDLL` are Windows-only), so mypy
legitimately reports a few different errors per platform. Measured: **398 on Windows,
399 on Linux**, from 3 win-only and 4 linux-only errors. A gate on the raw count would
therefore be red on one platform no matter what it was set to -- see below.

A gate demanding zero errors today would be red from its first run. A permanently red
gate is worse than no gate, because people learn to ignore it. So the gate enforces
what is true and enforceable *now*:

1. **Total error count may only go down.** Any new error fails the build.
2. **Every file that is error-free today must stay error-free.** This is the rule that
   earns its keep: a careless edit in a well-typed module is caught immediately.
3. **No file may exceed its baseline count**, though it may improve.

Adding a new module is free as long as it type-checks, and it is protected from then
on: the enforcement sets are recomputed from the per-platform records on every
`--update`.

### How platform differences are handled

Rule 2 is the load-bearing one, and it is platform-independent in the sense that it
holds on every platform -- but each run is judged against **its own** platform's record.
The code branches on `sys.platform` (`os.getpgid`/`killpg` are POSIX-only;
`CREATE_NEW_PROCESS_GROUP`, `CTRL_BREAK_EVENT` and `WinDLL` are Windows-only), so mypy
legitimately reports a few different errors per platform. Measured: **398 on Windows,
399 on Linux**. A gate on the raw count would be red on one platform no matter what it
was set to -- see below.

The baseline therefore stores each platform's **own** measurement:

```json
{
  "platforms": {
    "win32": {"total_errors": 398, "clean_files": [...], "dirty_files": {...}},
    "linux": {"total_errors": 399, "clean_files": [...], "dirty_files": {...}}
  }
}
```

A run compares against the record for the platform it measured, so:

| | compared against |
|---|---|
| clean set, per-file ceilings, total | this platform's own record |
| ...when this platform has no record yet | the merged view (and the run says so) |

Two mistakes are worth recording, because both looked correct:

- **Enforcing the merged view on every platform leaves a hole.** A file clean on win32
  and dirty on linux is absent from the merged clean set, so a *win32-only* error in it
  passed unnoticed -- the gate was blind precisely where the platforms disagree. That is
  why enforcement is per platform, not merged.
- **Reporting against the merged view contradicts `--update`.** The gate said "newly
  clean, run `--update` to lock that in" for files that were merely clean on the platform
  being run. Following that advice adds the file to *this* platform's clean set and then
  fails the next check on the platform that has the error. Reporting now uses the same
  record as enforcement, and names the cross-platform gap explicitly.

An earlier single-level layout stored the merged values directly, which could not even
express the distinction. It silently broke three further things, each now pinned by a
test in `tests/test_typecheck_gate.py`:

- `--update` kept `max(old, new)` per file, so **an improvement could never be locked
  in** -- the gate printed "run --update to lock that in" and then ignored it;
- `clean_files` was merged by intersection against the stored list, so **a newly added
  clean module was never added to the enforced set**, contradicting the promise above;
- **deleting** a module read as a regression.

## The `ManagerBase` contract

`coworker/server/manager_base.py` declares the state and cross-mixin methods that one
mixin uses but another defines. Without it, each mixin looks like it is calling names
that do not exist, and mypy reports **1020** errors instead of 398 (measured
apples-to-apples, same mypy, same interpreter). Four manager modules become fully
error-free because of it.

Every manager mixin inherits `ManagerBase`. `tests/test_manager_contract.py` keeps the
contract honest in three directions:

- everything the contract declares is actually implemented by some mixin;
- everything a mixin uses via `self.*` is defined by a manager class or declared in the
  contract;
- **if a mixin calls `self.X` and does not define `X` itself, `ManagerBase` declares
  `X`.** The two weaker rules above are both satisfied by a *neighbour*: `InboxMixin`
  calling `self.promote_workspace(...)` found the name in `WorkspacesMixin`, so it
  passed, while mypy correctly reported `"InboxMixin" has no attribute
  "promote_workspace"`. The third rule closes that seam, and matches exactly the
  condition under which a mixin type-checks in isolation.

Each of the three rules exists because a negative control caught it missing: deleting a
declaration from the contract left the weaker tests green while the type checker
silently regressed.

## What the gate already found

`PersonasMixin.install_persona_from_catalog` called `self.install_from_git(...)`, a
method no `SessionManager` mixin has ever defined -- it lives on `PersonaRegistry`. At
runtime that path raised `AttributeError`:

```
SessionManager.__getattr__: None
ATTRIBUTE ERROR: 'SessionManager' object has no attribute 'install_from_git'
```

"Install a persona from the community catalog" had been broken since the initial
commit. The gate's first job was to find it.

## Environment note (Windows)

mypy must be able to see the project's installed packages, or every third-party import
becomes a phantom `import-not-found` error (63 of them, measured). Running mypy with
the project interpreter handles this:

```bash
python -m mypy --python-executable <project-venv>/Scripts/python.exe coworker
```

`tools/typecheck.py` invokes mypy as `sys.executable -m mypy`, so it inherits whatever
interpreter you ran it with.

## Lowering the baseline

`--update` records **this platform's** measurement and rewrites the baseline. Use it
when:

- you fixed a batch of errors and want to prevent them coming back;
- you deliberately accepted a new error (rare -- explain it in the commit message).

It only replaces the record for the platform you ran it on; the other platform's record
is left alone, so running it on Windows cannot erase what Linux last measured. To lower
both, run it on both (or as `--platform linux` from one machine).

Never use it to turn a red build green. That is how a ratchet becomes decoration.

## Pinned by tests

`tests/test_typecheck_gate.py` exercises the gate's own logic with `mypy` stubbed out:
the three enforcement rules, the newly-added-module case, improvement locking, module
deletion, the per-platform versus merged comparison, and that forbidding a false
"lock that in" message. The gate decides whether every branch is green, so a silent
logic bug there is worse than no gate at all.
