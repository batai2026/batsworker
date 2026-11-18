# Contributing to OpenWorker

Thanks for taking the time to contribute. This guide is mostly what CI actually
enforces: the commands below are the ones the workflows run, so a red build is
something you can reproduce on your own machine. Where a rule is a review
convention rather than a check — screenshots, say — the guide says so.

Two naming notes first, because they show up in commands:

- The product is **OpenWorker**, the repository is `batai2026/batsworker`, and
  the Python distribution is `coworker`. All three move in lockstep on a release.
- "Tests" means three separate suites (Python, GUI unit, GUI end-to-end). A
  change that touches more than one surface needs more than one of them.

OpenWorker is in **beta** and is developed against an internal roadmap, so a PR
that adds a feature already under development, or that conflicts with the
project's direction, may be declined even if it is well built. Bug fixes,
test coverage, and docs are always welcome. Opening an issue first is the
cheapest way to find out which of the two you are about to write.

## Before you start

| | Requirement |
|---|---|
| Python | 3.10+ (CI runs 3.12; `pyproject.toml` declares `>=3.10`) |
| Node | 20+ |
| Rust | stable, via [rustup](https://rustup.rs/) — desktop shell and `stt/` only |
| `libclang` | only for `stt/` — `whisper-rs` runs bindgen. Windows: LLVM in `C:\Program Files\LLVM\bin`, exported as `LIBCLANG_PATH` |
| OS | Linux and Windows are both blocking in CI; macOS is built by `.github/workflows/release.yml` but not tested by `.github/workflows/ci.yml` |

Bootstrap a Python venv at `.venv` with the repo's own script:

```shell
bash packaging/setup_dev_env.sh      # Windows: run from Git Bash or WSL
```

It installs `-e .[messaging,dev]`. For the GUI, `npm ci` from `surfaces/gui/`
(the lockfile is authoritative; `npm install` is for when you are intentionally
changing dependencies).

## What CI runs

`.github/workflows/ci.yml` runs on every push and pull request. These are the
jobs your PR has to keep green — reproduce them locally before you push and you
will not be surprised.

| Job | Command | Notes |
|---|---|---|
| `lint` | `ruff check coworker tests scripts` | Rules pinned to `E4, E7, E9, F` in `pyproject.toml`; three modules carry a deliberate `E402` waiver |
| `pytest` (Linux) | `pytest tests -q --cov --cov-report=term` | Installs `requirements-dev.lock` + `pip install -e . --no-deps` |
| `pytest-windows` | same | Windows-only breakage happens (`time.tzset`, UTF-8 decoding, PowerShell quoting). Blocking on purpose |
| `docker-image` | `docker build -t openworker:ci .`, then `scripts/check_container_state_isolation.py` inside it | The image layout is a security boundary — state must not land in `/workspace` |
| `gui-unit` (+ Windows) | `npm ci`, `npx tsc --noEmit`, `npm test` | Plus `python surfaces/gui/scripts/check_i18n.py` |
| `gui-e2e` | `npm run e2e` | Hermetic Playwright: mocked `/v1` and WebSocket, no model or network |
| `rust` | `cargo clippy --all-targets -- -D warnings`, `cargo test --manifest-path stt/Cargo.toml` | Builds the SPA first; `-D warnings` means clippy lints fail the build |

Locally, from the repo root with `.venv` active:

```shell
ruff check coworker tests scripts
pytest tests -q                      # add --cov for the coverage gate

cd surfaces/gui
npx tsc --noEmit
npm test
npm run e2e                          # first run: npx playwright install chromium
```

Coverage is a gate at **82%** (`[tool.coverage.report] fail_under`). It sits a
point under the last measured full-suite number on purpose, to absorb platform
variance. If your change lowers coverage below the floor, add tests — lowering
the floor is a maintainer decision, not a way to unblock a branch.

### Windows specifics

CI runs a Windows job for a reason; keep these in mind when you touch
subprocess handling or file I/O:

- Read and write text as UTF-8 explicitly. The default console code page is GBK
  on a Chinese-locale Windows, and a decode error there has already caused a
  silent hang (fixed in `3398710`).
- `time.tzset()` does not exist on Windows. Anything timezone-related needs a
  guard, not an assumption.
- PowerShell quoting differs from `bash`; a command that works in Git Bash may
  split arguments differently under `pwsh`.

## Commit and branch conventions

Commits are Conventional-Commits style — `type(scope): subject` — which is what
the history uses:

```
feat(eval): gate real-model cells on the prompt payload, not model behaviour
fix: Windows GBK 解码崩溃导致提问无响应
refactor(errors): narrow the concrete close, annotate the loop (item 4, batch 3c)
docs(server-and-auth): record the frame dispatch table
chore(eval): refresh the stub golden baseline
```

Common types: `feat`, `fix`, `refactor`, `test`, `docs`, `ci`, `chore`,
`security`, `release`. Subject in the imperative mood, lower case, no trailing
period. Say *why* in the body when the diff cannot.

Branches are `type/short-slug`, matching the type of the work —
`security/dep-audit-rustix`, `fix/ci-cloud-hardening`,
`docs/security-and-changelog`. Branch from `main`.

## Pull requests

1. Keep the PR to one concern. A dependency bump, a refactor, and a feature in
   one branch cannot be reviewed or reverted independently.
2. **Attach screenshots for anything visible.** Before/after of what was broken
   and what it looks like now. This is the one review requirement that cannot
   be reconstructed from the diff.
3. Say what you ran and what it printed — the `pytest` summary line, which CI
   jobs you did not run locally. "Tests pass" without the command is not
   verifiable.
4. If it is a dependency upgrade, read the next section; the rules there are
   stricter than "update the version".
5. Expect review to ask for the smallest change that fixes the problem. That is
   house style, not an objection to your approach.

The PR template will prompt you for all of this.

## Dependency changes

This repo treats dependency risk as a tracked backlog, not a one-off audit.
`osv-scanner.toml` is the record; `.github/workflows/dependency-audit.yml` is
the enforcement. If your PR touches a lockfile, read these rules first.

**Regenerate locks with the repo's toolchain. Never hand-edit a lockfile.**

Python is locked with `uv` (`uv.lock`, plus the exported `requirements-dev.lock`
and `requirements-build.lock`). The exact export command is recorded at the top
of each generated file; use those flags so the diff contains only what you
changed:

```shell
uv lock                                   # resolve after editing pyproject.toml
uv export --frozen --no-hashes --no-emit-project --format requirements.txt \
  --extra dev --extra messaging --extra bedrock -o requirements-dev.lock
```

Node and Rust use their own tools: `npm install` for
`surfaces/gui/package-lock.json`, and `cargo update --precise <ver> -p <crate>`
for the two `Cargo.lock` files (`surfaces/gui/src-tauri/`, `stt/`).

Size the change to the advisory:

- Prefer the smallest bump that closes it — a patch or minor over a major.
- A major bump carries a migration note in the PR body: what changed, and what
  you re-verified. If there is no smaller path, say so explicitly.
- Read the code path before ranking. A medium-severity advisory in a function
  this codebase actually calls outranks a critical one in an unused dev-only
  transitive dependency.
- Verify the upgrade: install, build, run the affected suite. A red suite means
  revert or investigate — a broken upgrade is not done.

Do **not** add a fixable advisory to `osv-scanner.toml`. That file is only for
genuinely unfixable items, and every entry must carry:

- an `effectiveUntil` date — when it passes, the finding returns to CI. Accepted
  risk is debt with a due date, not silence; and
- a justification naming the code path that makes it unreachable or
  unexploitable *here*. The CVSS score is not a justification.

The key name matters, and it is version-coupled: `effectiveUntil` on
`[[IgnoredVulns]]` needs osv-scanner **2.7.1 or newer**. On 2.7.0 it is an unknown
key, the loader rejects the whole file and the scan exits non-zero, so every
acceptance silently stops applying. `ignoreUntil` is the deprecated spelling that
2.7.1 still honours (with a warning) and 2.7.0 understands. `.github/workflows/
dependency-audit.yml` pins osv-scanner-action v2.7.1 to match; bump one, bump the
other.

**An acceptance also has to stay true.** Two more things are enforced, because
both failed silently on the 2026-11-18 audit:

```shell
python scripts/check_suppressions.py
```

- The entry's package must still be in a lockfile, and still be pinned *below*
  the advisory's fixed version. If the crate is gone, or has moved past the fix,
  the entry suppresses nothing — worse, it hides any future finding for that
  crate. `osv-scanner` does not catch this: the id still exists, it just no
  longer describes this tree. For `zip` that would have covered a regression in a
  code path the updater really does reach on Windows.
- The id must map to a package at all. The map lives in the script's `ADVISORIES`
  table because osv-scanner **rejects** a `package` key here (unknown key → whole
  config refused → every acceptance off). An unmapped id fails CI; add it to that
  table or delete the entry.

Then regenerate the due-date table, which is what makes the calendar visible
instead of something to remember:

```shell
python scripts/check_suppressions.py --markdown docs/dependency-risk-register.md
```

`docs/dependency-risk-register.md` is generated. A stale copy fails CI, so do not
hand-edit it — an acceptance that lapses is meant to be seen coming, not
discovered on a red Monday.

Run the scan yourself before pushing a lockfile change:

```shell
osv-scanner --config=osv-scanner.toml --recursive ./
```

`--config` is explicit because the accept-list does not propagate into
subdirectories, and every lockfile lives below the repo root.

## Tests and test-only servers

- Backend tests live in `tests/` and use `pytest` with `asyncio_mode = "auto"`.
- Test doubles for external services are small in-process servers under
  `coworker/testing/` (`fake_github/`, `fake_slack/`). Use them rather than
  hitting a real API: it keeps the suite hermetic and keeps credentials out of CI.
- If a test needs PowerShell that may be absent on the runner, skip — do not
  pass vacuously. `tests/test_merge_pr_script.py` is the pattern.
- Anything that needs a real model endpoint belongs in the separately gated
  real-model matrix — the `Eval` workflow's manual `workflow_dispatch` run
  (`.github/workflows/eval.yml`) against a case under `evals/` — not in the
  default suite. The deterministic stub case in that workflow does run on every
  push and PR, and it pins an empty, non-git workspace on purpose: the outbound
  payload embeds paths, git state and the clock, so anything else makes the
  bytes drift.

## Security work

Do not open a public issue or PR for an unfixed vulnerability. Follow
[SECURITY.md](SECURITY.md) — it lists the private reporting channel, the
supported versions, and what is in scope. A hardening PR whose issue is already
public is fine.

## Documentation

- Design specs live in `docs/design/` and describe what the shipped code does.
  When a doc and the code disagree, the code wins — fix the doc in the same PR.
- `docs/README.md` is the index; add new design docs there.
- Release notes go in `CHANGELOG.md` under `## [Unreleased]`, using the Keep a
  Changelog sections the file already uses (`Added` / `Changed` / `Fixed` /
  `Security`). Entries are grouped by the release that first contains the
  change, not by commit date — the 1.0.x line was developed on parallel branches.

## Releases

Maintainers bump the version in lockstep across every file that carries it:

| File | Field |
|---|---|
| `pyproject.toml` | `version` |
| `coworker/__init__.py` | `__version__` |
| `coworker/server/app.py` | FastAPI `version=` |
| `surfaces/gui/package.json` | `version` |
| `surfaces/gui/package-lock.json` | the two root `version` fields — from `npm install`, not an editor |
| `surfaces/gui/src-tauri/Cargo.toml` | `version` |
| `surfaces/gui/src-tauri/tauri.conf.json` | `version` |

Then move `## [Unreleased]` in `CHANGELOG.md` into a dated section, tag
`v<version>`, and let `.github/workflows/release.yml` build installers into a
**draft** release for review. `release.yml` publishes nothing automatically — a
human publishes the draft.

Installers are signed only when the signing secrets exist. Do not describe a
build as signed unless it came from a workflow run that had them; see
"Installer signing" in [SECURITY.md](SECURITY.md).

## Merging

Branches land through a pull request on `main`, after CI is green, with a merge
commit — `Merge pull request #N from <branch>` is what the history shows.

Maintainers can close out a reviewed PR with the guarded helper:

```shell
./scripts/merge_pr.ps1 -Pr <number> -ExpectedSha <head-sha-that-was-reviewed>
```

Pass `-ExpectedSha`: without it a force-push between review and merge goes
unnoticed. Add `-DryRun` to run every guard and stop before the merge. The
script refuses rather than guesses when GitHub reports `mergeable_state=unknown`
(mergeability is computed lazily — wait a few seconds and retry). Its tests are
`tests/test_merge_pr_script.py`.

## Questions

Open an issue. For anything security-related, [SECURITY.md](SECURITY.md) first.
