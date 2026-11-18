# Branch protection on `main`

What gates a merge, why each check is (or is not) in the required set, and how to
change it. Last updated 2026-11-19.

## Current configuration

Classic branch protection (there are no rulesets). Verified by reading the API
back after the change, not from the UI.

| setting | value |
|---|---|
| required checks | **13** (listed below) |
| strict (up to date before merge) | **true** |
| enforce admins | true |
| required linear history | **false** |
| force pushes / deletions | false / false |
| required PR reviews | none |

### The 13 required checks

| check | job time | what it protects |
|---|---|---|
| `lint` | 0m09s | ruff over `coworker tests scripts` |
| `typecheck` | 0m58s | mypy ratchet (`tools/typecheck.py`) |
| `gate-stub` | 0m33s | eval baseline (deterministic stub case) |
| `new-vulnerabilities / osv-scan` | 0m29s | dependency delta — fails only on vulns the PR *adds* |
| `suppression-register` | 0m05s | dead/stale `osv-scanner.toml` exemptions + expiry register drift |
| `gui-unit` | 0m32s | vitest |
| `gui-unit-windows` | 1m25s | vitest on Windows |
| `docker-image` | 1m01s | image builds + isolation self-check |
| `rust` | 1m44s | `cargo` build/test with `--locked` |
| `gui-e2e` | 4m10s | Playwright |
| `pytest (3.15)` | 4m17s | test suite on the shipped interpreter |
| `pytest (3.12)` | 5m14s | test suite on the floor interpreter |
| `pytest-windows` | 12m50s | test suite on Windows |

Wall clock is set by `pytest-windows` at ~13 min; every other job finishes inside
~5.5 min in parallel.

## Why these and not others

### Checks that must NOT be required

`push-delta`, `code-scanning` and `full-backlog` are in
`.github/workflows/dependency-audit.yml` but guarded by
`if: github.event_name == 'push'` / `== 'schedule'`. **A required check that never
reports on a pull request leaves the PR permanently pending** and blocks every
merge forever. They stay advisory on PRs by design; `push-delta` still blocks the
push to `main`.

`suppression-register` sits in the same workflow but is guarded the other way —
`if: github.event_name != 'schedule'` — so it does report on pull requests, and is
therefore required. Its guard excludes only the weekly cron, where the full backlog
scan already evaluates the same file.

`docker` and `Release` workflows trigger on `push` / tags only — nothing to
require on a PR.

### `required_linear_history` stays false

This repo merges with **merge commits** (the six dependency migrations and every
fix since, e.g. `e49db52`). Linear history forbids exactly that and would reject
the project's own convention. Do not turn it on without also deciding to abandon
merge commits.

### `strict: true` — the tradeoff

Requiring an up-to-date branch means the checks that pass are `main + this PR`,
not `this PR in isolation`. The cost is real: merging one PR marks every other
open PR out of date, and it needs *Update branch* plus a ~13 min re-run.

It was chosen deliberately: without it, two PRs that are each green in isolation
can combine into a broken `main`, and neither PR's checks would have noticed.

When there are several PRs in flight, merge the slowest/riskiest one first,
because each merge invalidates the rest.

## How the gate was proven armed

Configuration read back through the API only proves what was *written*. To prove
it *bites*, a throwaway branch (`test/gate-canary`, PR #46) was pushed with one
deliberate `ruff` F401 violation:

```
mergeable_state: blocked
PUT /pulls/46/merge -> HTTP 405
{"message": "2 of 12 required status checks have not succeeded: 1 failing."}
```

The PR was then closed without merging and the branch deleted. The `2 of 12`
is `lint` failing plus `pytest-windows` still in progress at that moment — which
is also the reason a *pending* required check blocks: GitHub waits.

(The `12` in the quoted response is the required-check count *at the time of that
canary*; the set has since grown to 13 with `suppression-register`. The API message
is quoted verbatim rather than rewritten, so it is not a typo.)

## Changing this configuration

Classic protection is a full-object `PUT`: **any field you omit is reset to its
default**, so restate every setting you care about. Use
`wdocs/bp_apply.py` in the audit scratch dir (dry-run by default, `--apply` to
write) rather than hand-editing, and read the result back afterwards.

Check names must match the **job** name exactly, including any matrix expansion:

- `pytest (3.12)` — the matrix value is part of the context string
- `new-vulnerabilities / osv-scan` — the `/ osv-scan` comes from the reusable
  workflow called by the `new-vulnerabilities` job
- `gate-stub` — the job id, since `eval.yml` sets no `name:` on it

A typo does not error; it silently makes a check that never reports, which
blocks every merge. Always confirm with a canary after editing.
