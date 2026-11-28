#!/usr/bin/env python
"""Fails when the merge gate described in docs/branch-protection.md is not the gate
that is actually armed on the remote.

WHY THIS EXISTS. On 2026-11-18 the live configuration was read and recorded with
``strict: true`` (the archived snapshot still says so). At some point after that
``strict`` became ``false`` on the remote and nothing reported it: the doc still
claimed the branch had to be up to date, so "no PR has ever slipped through
stale" was a statement about luck, not about a check. A configuration nobody
reads back is a claim, and claims drift -- AGENTS.md section 3.2 is the same
lesson about required-check counts.

Two independent checks, deliberately separated:

  offline  (always runs)  The document's required-check list must contain every
                          job that actually reports on a *pull request*. This is
                          the check that would have caught a required check that
                          never reports (which blocks every merge forever) and,
                          crucially, it reads the workflows rather than trusting
                          the doc. No token, no network.

  live     (opt-in)       Compare the document against
                          ``GET /repos/{repo}/branches/main/protection`` plus the
                          repo-level flags. Needs a token with admin read on the
                          repository, which the gate job's read-only
                          ``GITHUB_TOKEN`` is not, so it runs from a laptop or a
                          dedicated job -- not from the PR gate. That gap is the
                          reason the offline arm exists and is the one wired into
                          CI (see the ``suppression-register`` job).

THE OFFLINE CHECK HAD TO BE BUILT AROUND A REAL TRAP. Three jobs in
``dependency-audit.yml`` are guarded ``if: github.event_name == 'push'`` or
``== 'schedule'``, so they never report on a PR and must NOT be required -- that
is what makes a PR permanently pending. So the offline rule is not "every job is
required"; it is:

  * every job that can report on a pull request must be in the doc, and
  * no job that cannot report on a pull request may be in the doc.

Both directions matter and both are pinned by tests, because each direction is a
different way for the gate to be wrong: the first leaves a real check
unenforced, the second blocks every merge.

WHAT THIS DOES NOT DO. It does not decide whether a given check is *worth*
requiring. That is a judgement, and it is written down per row in the doc. It
also does not apply anything -- see ``scripts/apply_branch_protection.py``, which
builds its payload from this document so the two cannot disagree.

Usage:
    python scripts/check_branch_protection.py --offline
    python scripts/check_branch_protection.py --live
    python scripts/check_branch_protection.py --offline --live --repo owner/name

Exit 0 when every enabled check agrees. 1 on a disagreement (with one line per
problem, each naming the file and line to edit). 2 on unusable input -- an
unreadable doc, a workflow tree that will not parse, or a missing token when
``--live`` was asked for, because judging from half the evidence is how the
original drift survived.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_REPO = "batai2026/batsworker"
DOC = Path("docs") / "branch-protection.md"
WORKFLOWS = Path(".github") / "workflows"

#: Settings whose *presence* in the document is what this tool checks, and the
#: doc-table label that must carry each of them. The label is matched literally
#: (after stripping markdown emphasis) so renaming a row is a visible edit.
SETTING_ROWS = {
    "strict": "strict (up to date before merge)",
    "enforce_admins": "enforce admins",
    "required_linear_history": "required linear history",
    "allow_force_pushes": "force pushes / deletions",
}

#: Repo-level settings the classic-protection document keeps *out* of its table,
#: but which decide what a merge actually does. `delete_branch_on_merge: false` is
#: why a merged branch lingers and has to be removed by hand (#80 and #81 exist to
#: make that hand-removal safe); `allow_auto_merge: true` lets a PR merge itself as
#: soon as its checks finish.
REPO_FLAGS = ("allow_auto_merge", "delete_branch_on_merge")


# -- document -------------------------------------------------------------------


@dataclass(frozen=True)
class Doc:
    """What the document claims. Parsed once, compared by the two checks."""

    checks: tuple[str, ...]
    settings: dict[str, bool]
    headline: int | None
    flags: dict[str, bool] = field(default_factory=dict)


def _cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _bool(text: str) -> bool | None:
    cleaned = text.replace("*", "").replace("`", "").strip().lower()
    if cleaned in ("true", "yes"):
        return True
    if cleaned in ("false", "no"):
        return False
    return None


def parse_doc(text: str) -> Doc:
    """Pull the required-check rows and the headline settings out of the document.

    The required-check list is recognised by having a backticked first cell and at
    least three columns -- that is the shape of the table under "The N required
    checks". Rows elsewhere (the usage examples, the settings table) do not match,
    which is why the parse is by shape and not by "the second table".
    """
    checks: list[str] = []
    settings: dict[str, bool] = {}
    headline: int | None = None

    for raw in text.splitlines():
        line = raw.strip()
        if not line.startswith("|"):
            continue
        cells = _cells(line)
        if len(cells) < 2:
            continue
        first = cells[0]
        m = re.fullmatch(r"`(.+?)`", first)
        if m and len(cells) >= 3:
            name = m.group(1).strip()
            if name and name not in checks:
                checks.append(name)
            continue
        label = first.replace("*", "").strip()
        if label == "required checks":
            digits = re.search(r"\d+", cells[1])
            headline = int(digits.group()) if digits else None
            continue
        if label == SETTING_ROWS["allow_force_pushes"]:
            # This row carries two settings in one cell: "false / false".
            parts = [p.strip() for p in cells[1].split("/")]
            for key, part in zip(("allow_force_pushes", "allow_deletions"), parts):
                parsed = _bool(part)
                if parsed is not None:
                    settings[key] = parsed
            continue
        for key, want in SETTING_ROWS.items():
            if label == want:
                parsed = _bool(cells[1])
                if parsed is not None:
                    settings[key] = parsed

    return Doc(checks=tuple(checks), settings=settings, headline=headline,
               flags=parse_repo_flags(text))


def doc_problems(doc: Doc) -> list[str]:
    """Structural problems: the document must state every setting it is trusted for."""
    problems: list[str] = []
    for key, want in SETTING_ROWS.items():
        if key not in doc.settings:
            problems.append(
                f"{DOC.as_posix()}: no '{want}' row -- the doc is the source of "
                "truth for this setting, so a missing row means it silently keeps "
                "whatever the remote has"
            )
    if doc.headline is not None and doc.headline != len(doc.checks):
        problems.append(
            f"{DOC.as_posix()}: headline says {doc.headline} required checks, "
            f"table lists {len(doc.checks)}"
        )
    return problems


def parse_repo_flags(text: str) -> dict[str, bool]:
    """Read the repo-level flags from the doc's "repository settings" block.

    Kept separate from ``parse_doc`` because these come from a ``key: value``
    block rather than a table, and because *not* mentioning a flag is a legitimate
    state that only the live check should object to -- an offline run must not
    invent a failure from a doc it cannot compare against anything.
    """
    flags: dict[str, bool] = {}
    for raw in text.splitlines():
        line = raw.strip().lstrip("-*").strip()
        for key in REPO_FLAGS:
            m = re.fullmatch(rf"`?{key}`?\s*[:=]\s*`?(true|false)`?", line, re.I)
            if m:
                flags[key] = m.group(1).lower() == "true"
    return flags


# -- workflows ------------------------------------------------------------------


@dataclass(frozen=True)
class Job:
    """One job, as YAML actually declares it."""

    workflow: str
    job_id: str
    context: str  # what GitHub reports as the required check's name
    pr_capable: bool
    reusable: bool = False


GUARD_RE = re.compile(r"github\.event_name\s*(==|!=)\s*'([a-z_]+)'")

#: A matrix key interpolated into the job name, e.g. `pytest (${{ matrix.python-version }})`.
MATRIX_EXPR_RE = re.compile(r"\$\{\{\s*matrix\.([A-Za-z0-9_-]+)\s*\}\}")


def _contexts(job: dict, job_id: str) -> list[str]:
    """Every check name this job can report, one per matrix combination.

    Two shapes matter here, both observed in this repo:

    * the name is literal and the matrix only expands it, giving
      ``pytest (3.12)`` from a name of ``pytest`` -- GitHub appends the
      combination in parentheses;
    * the name *contains* ``${{ matrix.x }}``, giving ``pytest (${{ matrix.python-version }})``
      as the name and ``pytest (3.12)`` once substituted.

    Getting this wrong is how a required check ends up documented under a name
    that never reports, so the substitution is done rather than pattern-matched.
    """
    name = str(job.get("name") or job_id)
    matrix = (job.get("strategy") or {}).get("matrix") or {}

    # Which matrix key does the name interpolate, if any?
    used = MATRIX_EXPR_RE.search(name)
    if used:
        key = used.group(1)
        values = matrix.get(key)
        if isinstance(values, list) and values:
            return [MATRIX_EXPR_RE.sub(str(v), name) for v in values]

    # Otherwise a single-key matrix is appended by GitHub in parentheses.
    axes = [(k, v) for k, v in matrix.items()
            if k not in ("include", "exclude") and isinstance(v, list)]
    if len(axes) == 1:
        return [f"{name} ({v})" for v in axes[0][1]]
    return [name]


def _pr_capable(workflow_doc: dict, job: dict) -> bool:
    """Can this job report a conclusion on a *pull request*?

    1. A guard on ``github.event_name`` is authoritative: ``== 'push'`` means never
       on a PR, ``!= 'schedule'`` means it does report on one.
    2. Otherwise the workflow must trigger on ``pull_request`` at all.

    A job explicitly removed from PRs some other way (``if: false``, a matrix that
    is empty on PRs) is out of reach of a static read; the live check is what
    settles those, which is why it exists.
    """
    guard = GUARD_RE.search(str(job.get("if") or ""))
    if guard:
        op, event = guard.group(1), guard.group(2)
        if op == "==":
            return event == "pull_request"
        return event != "pull_request"  # `!= 'x'` reports on everything but x

    triggers = workflow_doc.get("on", workflow_doc.get(True))
    if isinstance(triggers, str):
        return triggers == "pull_request"
    if isinstance(triggers, list):
        return "pull_request" in triggers
    if isinstance(triggers, dict):
        return "pull_request" in triggers
    return False


def read_jobs(root: Path) -> list[Job]:
    import yaml  # runtime dependency (pyproject: pyyaml>=6)

    jobs: list[Job] = []
    wf_dir = root / WORKFLOWS
    for path in sorted(wf_dir.glob("*.y*ml")):
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for job_id, job in (doc.get("jobs") or {}).items():
            job = job or {}
            calls = job.get("uses")
            if calls:
                # A reusable workflow always reports as "<caller job id> / <inner
                # job name>". The inner names are not declared here, so the
                # reported context cannot be derived from this repo's YAML at all
                # -- which is exactly why the live check exists. Record the id so
                # the comparison can tell "unknown, live will decide" apart from
                # "a plain job that reports under its own name".
                contexts = [job_id]
            else:
                contexts = _contexts(job, job_id)
            for context in contexts:
                jobs.append(Job(workflow=path.name, job_id=job_id,
                                context=context, pr_capable=_pr_capable(doc, job),
                                reusable=bool(calls)))
    return jobs


def offline_problems(doc: Doc, jobs: list[Job], root: Path) -> list[str]:
    problems: list[str] = []
    required = set(doc.checks)
    reporting = [j for j in jobs if j.pr_capable]

    for job in reporting:
        if job.reusable:
            # "<caller> / <inner job>" cannot be derived from this repo's YAML.
            # Accept any documented name under the caller's prefix; the live check
            # is what pins the exact string.
            if not any(c.startswith(f"{job.context} / ") for c in required):
                problems.append(
                    f"{DOC.as_posix()}: job `{job.job_id}` calls a reusable workflow "
                    f"({WORKFLOWS.as_posix()}/{job.workflow}) and reports on pull "
                    f"requests as `{job.context} / <inner job>`, but no row in the "
                    "table covers it -- its verdict does not gate a merge"
                )
            continue
        if job.context not in required:
            problems.append(
                f"{DOC.as_posix()}: `{job.context}` reports on pull requests "
                f"({WORKFLOWS.as_posix()}/{job.workflow}, job `{job.job_id}`) but is "
                "not a required check -- its verdict does not gate a merge"
            )

    for name in doc.checks:
        match = [j for j in jobs if j.context == name]
        if not match:
            inner = [j for j in jobs if j.reusable and name.startswith(f"{j.context} / ")]
            if inner:
                # The caller is known and reports on PRs; the inner job name came
                # from the reusable workflow, so this is as far as static reading goes.
                continue
            problems.append(
                f"{DOC.as_posix()}: `{name}` is listed as required but no job in "
                f"{WORKFLOWS.as_posix()}/ produces that check name -- a required "
                "check that never reports leaves every PR pending forever"
            )
        elif not any(j.pr_capable for j in match):
            wf = match[0].workflow
            problems.append(
                f"{DOC.as_posix()}: `{name}` is listed as required but "
                f"{WORKFLOWS.as_posix()}/{wf} guards it off pull requests -- every "
                "PR would wait on a check that never arrives"
            )

    if not reporting:
        problems.append(
            f"{WORKFLOWS.as_posix()}: no job was found that reports on pull "
            "requests; the workflow parse is wrong, not the gate"
        )
    return problems


# -- live -----------------------------------------------------------------------


def token() -> str | None:
    """Read a GitHub token from the environment, then from git's credential helper.

    No token is printed or logged. Returning None (rather than exiting) lets the
    caller say which check was skipped and why, instead of failing with a traceback
    that looks like a broken script.
    """
    import os
    for var in ("GITHUB_TOKEN", "GH_TOKEN"):
        value = os.environ.get(var)
        if value:
            return value
    try:
        out = subprocess.run(
            ["git", "credential", "fill"],
            input="protocol=https\nhost=github.com\n\n",
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        ).stdout
    except OSError:
        return None
    for line in out.splitlines():
        if line.startswith("password="):
            return line.split("=", 1)[1]
    return None


def api(repo: str, path: str, tok: str | None):
    req = urllib.request.Request(
        f"https://api.github.com/repos/{repo}{path}",
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "openworker-branch-protection-check",
            **({"Authorization": f"Bearer {tok}"} if tok else {}),
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read() or b"null"), None
    except urllib.error.HTTPError as exc:
        return None, f"HTTP {exc.code}: {exc.read().decode(errors='replace')[:300]}"
    except (urllib.error.URLError, TimeoutError) as exc:
        return None, f"{type(exc).__name__}: {exc}"


@dataclass(frozen=True)
class Live:
    checks: tuple[str, ...]
    settings: dict[str, bool]
    flags: dict[str, bool] = field(default_factory=dict)


def parse_live(payload: dict) -> Live:
    rsc = payload.get("required_status_checks") or {}
    checks = tuple(sorted(c.get("context", "") for c in (rsc.get("checks") or [])))
    return Live(
        checks=checks,
        settings={
            "strict": bool(rsc.get("strict")),
            "enforce_admins": bool((payload.get("enforce_admins") or {}).get("enabled")),
            "required_linear_history": bool(
                (payload.get("required_linear_history") or {}).get("enabled")),
            "allow_force_pushes": bool(
                (payload.get("allow_force_pushes") or {}).get("enabled")),
        },
    )


def live_problems(doc: Doc, live: Live) -> list[str]:
    problems: list[str] = []
    doc_set, live_set = set(doc.checks), set(live.checks)
    for name in sorted(live_set - doc_set):
        problems.append(
            f"{DOC.as_posix()}: `{name}` is required on the remote but missing from "
            "the document -- the gate is stricter than its description"
        )
    for name in sorted(doc_set - live_set):
        problems.append(
            f"{DOC.as_posix()}: `{name}` is documented as required but is NOT "
            "enforced on the remote -- the document is describing a gate that is "
            "not armed"
        )
    for key, claimed in sorted(doc.settings.items()):
        actual = live.settings.get(key)
        if actual is None:
            continue
        if claimed != actual:
            label = SETTING_ROWS[key]
            problems.append(
                f"{DOC.as_posix()}: `{label}` says {str(claimed).lower()} but the "
                f"remote is {str(actual).lower()}"
            )
    for key in REPO_FLAGS:
        if key not in doc.flags:
            problems.append(
                f"{DOC.as_posix()}: does not record `{key}` -- the live value is "
                f"{str(live.flags.get(key)).lower()}, so nothing would notice if it "
                "changed"
            )
        elif live.flags.get(key) is not None and doc.flags[key] != live.flags[key]:
            problems.append(
                f"{DOC.as_posix()}: `{key}` says {str(doc.flags[key]).lower()} but "
                f"the remote is {str(live.flags[key]).lower()}"
            )
    return problems


# -- cli ------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", default=".", help="repository root")
    parser.add_argument("--doc", default=str(DOC), help="the document to trust")
    parser.add_argument("--repo", default=DEFAULT_REPO, help="owner/name for --live")
    parser.add_argument("--offline", action="store_true",
                        help="check the document against the workflows (no token)")
    parser.add_argument("--live", action="store_true",
                        help="check the document against the remote configuration")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args(argv)

    if not args.offline and not args.live:
        args.offline = args.live = True

    root = Path(args.root).resolve()
    doc_path = root / args.doc
    try:
        doc = parse_doc(doc_path.read_text(encoding="utf-8"))
    except OSError as exc:
        sys.stderr.write(f"error: cannot read {doc_path}: {exc}\n")
        return 2

    problems = doc_problems(doc)
    offline_count: int | None = None
    live_view: Live | None = None
    report: dict[str, object] = {
        "doc": {"checks": list(doc.checks), "settings": doc.settings,
                "headline": doc.headline, "flags": doc.flags},
        "offline": None,
        "live": None,
        "problems": problems,
    }

    if args.offline:
        try:
            jobs = read_jobs(root)
        except Exception as exc:  # noqa: BLE001 - unreadable tree is one verdict
            sys.stderr.write(f"error: cannot read {WORKFLOWS.as_posix()}/: {exc}\n")
            return 2
        offline = offline_problems(doc, jobs, root)
        problems.extend(offline)
        offline_count = len(jobs)
        report["offline"] = {"jobs": len(jobs), "problems": offline}

    if args.live:
        tok = token()
        if not tok:
            sys.stderr.write(
                "error: --live needs a GitHub token (GITHUB_TOKEN / GH_TOKEN, or "
                "git's credential helper). Refusing to report a verdict from half "
                "the evidence.\n")
            return 2
        payload, err = api(args.repo, "/branches/main/protection", tok)
        if err:
            sys.stderr.write(f"error: cannot read protection from {args.repo}: {err}\n")
            return 2
        # The repo-level flags live on a different endpoint; a protection read that
        # succeeds says nothing about them.
        repo_payload, rerr = api(args.repo, "", tok)
        if rerr:
            sys.stderr.write(f"error: cannot read {args.repo} settings: {rerr}\n")
            return 2
        live = parse_live(payload)
        live = Live(checks=live.checks, settings=live.settings,
                    flags={k: bool(repo_payload.get(k)) for k in REPO_FLAGS
                           if repo_payload.get(k) is not None})
        live_view = live
        live_errs = live_problems(doc, live)
        problems.extend(live_errs)
        report["live"] = {"checks": list(live.checks), "settings": live.settings,
                          "flags": live.flags,
                          "problems": live_errs}

    if args.json:
        report["problems"] = problems
        print(json.dumps(report, indent=2))
    else:
        mode = " + ".join(m for m, on in (("offline", args.offline),
                                          ("live", args.live)) if on)
        print(f"branch protection on main vs {doc_path.name} [{mode}]")
        if offline_count is not None:
            print(f"  workflows parsed: {offline_count} check name(s)")
        if live_view is not None:
            print(f"  live: {len(live_view.checks)} required, "
                  + ", ".join(f"{k}={str(v).lower()}"
                              for k, v in live_view.settings.items()))
            if live_view.flags:
                print("  repo flags: " + ", ".join(
                    f"{k}={str(v).lower()}" for k, v in live_view.flags.items()))
        if problems:
            print(f"\n{len(problems)} problem(s):")
            for problem in problems:
                print(f"  - {problem}")
        else:
            print("  no drift")

    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
