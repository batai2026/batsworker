<!--
  Keep the headings. Delete the prompts you did not need, but do not delete a
  section without saying why — a reviewer reading "n/a" can tell the difference
  between "not applicable" and "not considered".
-->

## What this changes

<!-- One paragraph. What was wrong, and what the diff does about it. -->

## Why

<!--
  The motivation a reviewer cannot get from the diff. Link the issue if there is
  one (`Fixes #123`). For a dependency upgrade: the advisory id, the vulnerable
  package, and whether the vulnerable code path is actually reachable here.
-->

## How I verified it

<!--
  Paste the command and its summary line, per suite you ran. "Tests pass" is not
  verifiable; `pytest tests -q` plus its last line is.
-->

| Suite | Command | Result |
|---|---|---|
| Python | `pytest tests -q` | |
| Lint | `ruff check coworker tests scripts` | |
| GUI unit | `cd surfaces/gui && npm test` | |
| GUI types | `cd surfaces/gui && npx tsc --noEmit` | |
| GUI e2e | `cd surfaces/gui && npm run e2e` | |
| Rust (desktop) | `cargo clippy --all-targets -- -D warnings` | |
| Rust (stt) | `cargo test --manifest-path stt/Cargo.toml` | |

<!-- Say which rows you did NOT run, and why. A CI-only result is fine to report as such. -->

## Screenshots

<!--
  Required for anything visible. Before (the bug) and after (fixed). For a pure
  backend change, write "not applicable" rather than leaving this empty.
-->

## Checklist

- [ ] One concern in this PR — no unrelated refactor, formatting sweep, or rename
- [ ] Screenshots attached for visible changes (or n/a stated above)
- [ ] Tests added or updated for the behaviour change
- [ ] `CHANGELOG.md` updated under `## [Unreleased]` (user-visible changes only)
- [ ] Docs updated where they now disagree with the code

## Dependency changes only

<!-- Delete this whole section if the PR does not touch a lockfile. -->

- Advisory: <!-- e.g. RUSTSEC-2026-0486, GHSA-xxxx, PYSEC-xxxx -->
- Package and version change: <!-- old -> new -->
- Smallest fix? <!-- If this is a major bump, what ruled out the smaller path? -->
- Reachable in this codebase? <!-- Which code path, or why not -->
- Lockfile regenerated with: <!-- uv / npm / cargo --precise -- never by hand -->
- `osv-scanner --config=osv-scanner.toml --recursive ./` result: <!-- clean, or the diff in findings -->
