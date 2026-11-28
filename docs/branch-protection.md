# Branch protection on `main`

What gates a merge, why each check is (or is not) in the required set, and how to
change it.

**This file is the source of truth for the gate, and it is checked rather than
trusted.** `scripts/check_branch_protection.py --offline` runs on every pull request
and fails when the table below disagrees with what the workflows can actually
report; `--live` reads the remote configuration back and fails on any difference.
`scripts/apply_branch_protection.py` builds the API payload *from this file*, so
the written gate and the described gate cannot say different things.

Last verified against the remote: **2026-11-28**.

## Current configuration

Classic branch protection (there are no rulesets -- `GET /repos/.../rulesets` returns
an empty list). Every value here was read back from the API on 2026-11-28, not from
the UI.

| setting | value |
|---|---|
| required checks | **13** (listed below) |
| strict (up to date before merge) | **false** |
| enforce admins | true |
| required linear history | false |
| force pushes / deletions | false / false |
| required PR reviews | none |

Repository-level settings that are not part of the protection object, but decide
what a merge does:

```
allow_auto_merge: true
delete_branch_on_merge: false
```

`delete_branch_on_merge: false` is why a merged branch lingers and has to be
removed by hand. `scripts/check_stale_branches.py` exists to make that removal
safe; the setting is recorded here so that turning it on is a visible edit rather
than a quiet improvement.

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
therefore required. Its guard excludes only the scheduled cron, where the full backlog
scan already evaluates the same file.

`docker` and `Release` workflows trigger on `push` / tags only — nothing to
require on a PR.

This is not a rule to apply by hand: `--offline` derives it from the workflow YAML
in both directions. A job that can report on a PR but is not in the table fails the
check (a real gate left unenforced); a name in the table that no job produces fails
it too (a gate that never arrives, i.e. every PR pending forever).

### `required_linear_history` stays false

The repository setting `allow_squash_merge` is true and every merge since
2026-11-27 has been a squash (`(#74)` through `(#81)`), but linear history is a
different switch: it forbids *merge commits*, and `git log --merges` still carries
them from before that date (e.g. `d07710d`, `be1f7bb`). Leaving it false keeps the
history readable as it is; turning it on is a separate decision about how merges
should look, not a fix.

### `strict: false` — the tradeoff, stated honestly

**This was `true` until 2026-11-18 or later, and is `false` now.** The archived
snapshot of the live configuration taken on 2026-11-18 recorded `strict: true`
alongside these same 13 checks. Nothing recorded when it changed or why, and
nothing failed when it did -- which is the whole reason this file is now checked
against the remote instead of being believed.

What the setting means: with `strict: false`, the checks that pass are `this PR in
isolation`, not `main + this PR`. Two PRs that are each green can therefore merge
into a broken `main`, and neither PR's checks would have noticed. That is a real
gap, not a theoretical one.

With `strict: true`, merging one PR marks every other open PR out of date, and each
needs *Update branch* plus a ~13 min re-run.

It is `false` today. If you want the gap closed, run:

```
python scripts/apply_branch_protection.py          # shows strict: false -> true
python scripts/apply_branch_protection.py --apply  # writes, then reads back
```

Do not "fix" this by editing the row above to say `true`: `--live` will fail, and
it is right to. The row describes the remote, not the intention.

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

Edit the table and this file's `allow_auto_merge` / `delete_branch_on_merge` block,
then:

```
python scripts/check_branch_protection.py --offline   # doc vs workflows, no token
python scripts/check_branch_protection.py --live      # doc vs remote, needs a token
python scripts/apply_branch_protection.py             # dry run: what would change
python scripts/apply_branch_protection.py --apply --repo-flags
```

`apply_branch_protection.py` sends a **full-object `PUT`**: any field omitted is
reset to its default, so it restates every setting the document declares and
refuses to run if one is missing. It reads the remote back afterwards and exits
non-zero unless the result matches the document, because a `200` from the `PUT`
says only that the request was accepted.

`--repo-flags` is separate on purpose: `allow_auto_merge` and
`delete_branch_on_merge` are repository settings, not part of the protection
object, and live behind a different endpoint (`PATCH /repos/{owner}/{repo}`).

Check names must match the **job** name exactly, including any matrix expansion:

- `pytest (3.12)` — the matrix value is part of the context string
- `new-vulnerabilities / osv-scan` — the `/ osv-scan` comes from the reusable
  workflow called by the `new-vulnerabilities` job, and cannot be derived from
  this repo's YAML, which is why `--offline` accepts any documented name under the
  caller's prefix and `--live` is what pins the exact string
- `gate-stub` — the job id, since `eval.yml` sets no `name:` on it

A typo does not error; it silently makes a check that never reports, which blocks
every merge. `--offline` catches the "no job produces this name" half without a
token, and `--live` catches the rest.
