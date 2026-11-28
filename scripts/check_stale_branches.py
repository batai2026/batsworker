#!/usr/bin/env python
"""Decide whether a branch can be deleted without losing work.

Written after a session that deleted six remote branches by hand and had to
re-derive the judgement three times, each time from scratch, because a note is
not a check. The judgement is one question with two traps:

**The question.** Relative to the merge-base, does some commit on main
reproduce exactly the change this branch introduced? If yes, deleting the ref
drops nothing. If no, keep it.

**Trap 1: ancestry is the wrong question after a squash merge.** Squash landing
rewrites history, so the branch tip is never an ancestor of main and
``git branch --merged`` never lists it -- forever, not just for a while. The
branch looks unmerged on every future run. Hence the content scan.

**Trap 2: deletion lines in ``git diff <branch> main --numstat`` prove
nothing.** The branch's merge-base is old, so everything main added since shows
up as a deletion or a rewrite. That is the normal shape of a branch that was
merged and then lived alongside later work -- not evidence of loss. This script
never consults that diff; ``test_deletions_in_diff_branch_main_are_not_a_signal``
builds exactly that misleading output and asserts the verdict does not move.

Every refusal is a refusal to *guess*: a range too wide to scan gives
``inconclusive``, unrelated history gives ``unrelated``, and both mean keep.

Three limits worth knowing before trusting a KEEP line:

* **A branch whose work was re-implemented by hand reads as KEEP.** If the same
  change reached main spread over several edited-together commits, nothing on
  main reproduces the branch's diff byte-for-byte and ``git cherry`` marks it
  ``+``. This is not hypothetical: ``fix/osv-withdrawn-suppression`` was ported
  into main across PRs #78 and #79 and is reported KEEP today. A three-way
  ``git merge-tree`` was evaluated as a second opinion and rejected -- main has
  since edited the same lines, so it conflicts and answers nothing either way.
  KEEP is the safe direction, and the cost of a false KEEP is one manual look.
* **A squash that also carries unrelated work is KEEP** for the same reason.
  Pinned by ``test_squash_carrying_extra_work_is_kept``.
* **Nothing runs this on a schedule.** A stale branch nobody points this at
  stays stale. The tests pin the verdict arms against throwaway repositories;
  they assert nothing about the real remote.

Usage:
    python scripts/check_stale_branches.py                 # report, deletes nothing
    python scripts/check_stale_branches.py --fetch         # refresh refs first
    python scripts/check_stale_branches.py --delete        # act on deletable ones
    python scripts/check_stale_branches.py --local         # local branches instead

Exit 0 in report mode. With ``--delete``, 1 if a deletion failed. With
``--strict``, 1 if any branch could not be decided (``inconclusive`` or
``unrelated``). 2 on unusable input (not a repo, ``--main`` missing).
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

DEFAULT_REMOTE = "origin"
DEFAULT_MAIN = "main"
# A branch that diverged long ago puts every commit on main inside the scan
# range. Past this many candidates we stop and say so instead of spending
# minutes diffing or quietly narrowing the search.
DEFAULT_MAX_CANDIDATES = 400

UNDECIDED_KINDS = frozenset({"unrelated", "inconclusive"})

# Determinism matters: two runs of the same comparison must produce the same
# bytes, or "the change is identical" becomes a coin flip. External diff drivers,
# textconv and rename heuristics are per-clone config, and a rename-guessed
# line is not the same change.
_DIFF_OPTS = (
    "--no-color",
    "--no-ext-diff",
    "--no-textconv",
    "--no-renames",
)


class GitError(RuntimeError):
    """A git subcommand failed when its success was required."""


@dataclass(frozen=True)
class Verdict:
    branch: str
    kind: str
    deletable: bool
    evidence: str

    def line(self) -> str:
        marker = "DELETE" if self.deletable else "KEEP  "
        return f"{marker}  {self.kind:<12} {self.branch:<38} {self.evidence}"


class Repo:
    """Thin, read-mostly wrapper. All judgement lives in ``classify``."""

    def __init__(self, path: str | Path) -> None:
        self.cwd = str(path)

    def _run(self, *args: str, check: bool = True) -> subprocess.CompletedProcess[bytes]:
        proc = subprocess.run(
            ["git", *args], cwd=self.cwd, capture_output=True, env=_git_env()
        )
        if check and proc.returncode != 0:
            raise GitError(
                f"git {' '.join(args)} failed ({proc.returncode}): "
                f"{proc.stderr.decode('utf-8', 'replace').strip()}"
            )
        return proc

    def text(self, *args: str) -> str:
        return self._run(*args).stdout.decode("utf-8", "replace").strip()

    def rev_parse(self, rev: str) -> str | None:
        proc = self._run(
            "rev-parse", "--verify", "--quiet", "--end-of-options", f"{rev}^{{commit}}",
            check=False,
        )
        if proc.returncode != 0:
            return None
        return proc.stdout.decode("ascii", "replace").strip() or None

    def is_ancestor(self, maybe_ancestor: str, descendant: str) -> bool:
        proc = self._run(
            "merge-base", "--is-ancestor", maybe_ancestor, descendant, check=False
        )
        if proc.returncode not in (0, 1):
            raise GitError(f"merge-base --is-ancestor failed ({proc.returncode})")
        return proc.returncode == 0

    def tree_of(self, rev: str) -> str:
        return self.text("rev-parse", f"{rev}^{{tree}}")

    def merge_base(self, a: str, b: str) -> str | None:
        proc = self._run("merge-base", a, b, check=False)
        if proc.returncode != 0:
            return None
        return proc.stdout.decode("ascii", "replace").strip() or None

    def raw_diff(self, a: str, b: str) -> bytes:
        """Paths, modes and full blob ids of everything that differs.

        Comparing these bytes is exact, not a heuristic: identical raw output
        means identical paths, identical modes and identical content. There is
        deliberately no second pass over the patch text -- it could not differ
        once the blob ids match, and an arm that can never fire is not a check.
        """
        return self._run("diff", "--raw", "--no-abbrev", *_DIFF_OPTS, a, b).stdout

    def commits_in(self, base: str, tip: str) -> list[str]:
        out = self.text("rev-list", f"{base}..{tip}")
        return [line for line in out.splitlines() if line]

    def cherry(self, upstream: str, head: str) -> list[str]:
        """``git cherry``: '-' when an equivalent patch is already upstream."""
        out = self.text("cherry", upstream, head)
        return [line for line in out.splitlines() if line.strip()]

    def branches(self, ref_root: str) -> list[str]:
        out = self.text("for-each-ref", "--format=%(refname)", ref_root)
        names = []
        for ref in out.splitlines():
            ref = ref.strip()
            if not ref or ref.endswith("/HEAD"):
                continue
            names.append(ref[len(ref_root):] if ref.startswith(ref_root) else ref)
        return sorted(names)

    def fetch(self, remote: str) -> None:
        self._run("fetch", "--prune", remote)

    def delete(self, name: str, ref_root: str, remote: str) -> None:
        if ref_root.startswith("refs/remotes/"):
            self._run("push", "--delete", remote, name)
        else:
            self._run("branch", "--delete", "--force", name)


def _git_env() -> dict[str, str]:
    env = dict(os.environ)
    # A deletion pushes. It must fail rather than sit at a password prompt with
    # nobody there to answer it.
    env["GIT_TERMINAL_PROMPT"] = "0"
    env.setdefault("LC_ALL", "C")
    return env


def classify(
    repo: Repo,
    branch: str,
    main: str,
    *,
    max_candidates: int = DEFAULT_MAX_CANDIDATES,
) -> Verdict:
    """Return the deletion verdict for one branch.

    Short answers first -- ancestry, then tree equality -- because both are one
    git call and conclusive. The content scan is bounded and last.
    """
    tip = repo.rev_parse(branch)
    if tip is None:
        return Verdict(branch, "gone", True, "ref does not exist (already pruned)")
    main_tip = repo.rev_parse(main)
    if main_tip is None:
        raise GitError(f"{main} does not resolve to a commit")

    if repo.is_ancestor(tip, main_tip):
        return Verdict(branch, "merged", True, f"{tip[:12]} is an ancestor of {main}")

    # Early exit, not a separate kind of evidence: if the trees are equal there
    # is nothing main lacks, and the scan below would reach the same conclusion
    # from main's tip commit. No test isolates this arm; deleting it changes
    # speed, not verdicts.
    if repo.tree_of(tip) == repo.tree_of(main_tip):
        return Verdict(branch, "same-tree", True, f"tree of {tip[:12]} equals {main}")

    base = repo.merge_base(tip, main_tip)
    if base is None:
        return Verdict(branch, "unrelated", False, f"no common ancestor with {main}")

    want_raw = repo.raw_diff(base, tip)
    candidates = repo.commits_in(base, main_tip)
    if len(candidates) > max_candidates:
        return Verdict(
            branch,
            "inconclusive",
            False,
            f"{len(candidates)} commits behind {main}, over the {max_candidates} cap",
        )

    landed = []
    for sha in candidates:
        if repo.raw_diff(base, sha) != want_raw:
            continue
        # A different commit reproducing the same change: exactly what a squash
        # merge leaves behind, and exactly what ancestry can never report.
        landed.append(sha)
    if landed:
        where = ", ".join(sha[:12] for sha in landed[:3])
        more = "" if len(landed) <= 3 else f" (+{len(landed) - 3} more)"
        return Verdict(branch, "squash", True, f"landed as {where}{more}")

    # Not contained: say how much work is here, so a reader can tell one stray
    # commit from an abandoned feature.
    ahead = repo.commits_in(base, tip)
    already = sum(1 for line in repo.cherry(main_tip, tip) if line.startswith("-"))
    detail = f"{len(ahead)} commit(s) ahead of {main}"
    if already:
        detail += f", {already} patch(es) already upstream"
    return Verdict(branch, "unmerged", False, detail)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Report which branches can be deleted without losing work.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--repo", default=".", help="path inside the repository")
    parser.add_argument("--remote", default=DEFAULT_REMOTE,
                        help="remote to read refs from and delete from "
                             f"(default {DEFAULT_REMOTE})")
    parser.add_argument("--main", default=None,
                        help="branch everything is compared against "
                             "(default: <remote>/main, or main with --local)")
    parser.add_argument("--local", action="store_true",
                        help="inspect local branches instead of the remote's")
    parser.add_argument("--fetch", action="store_true",
                        help="run `git fetch --prune <remote>` first")
    parser.add_argument("--delete", action="store_true",
                        help="actually delete the branches marked DELETE")
    parser.add_argument("--strict", action="store_true",
                        help="exit 1 if any branch could not be decided")
    parser.add_argument("--max-candidates", type=int, default=DEFAULT_MAX_CANDIDATES,
                        help="give up (keep the branch) past this many commits to scan")
    args = parser.parse_args(argv)

    top = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=args.repo, capture_output=True, text=True, encoding="utf-8",
        errors="replace", env=_git_env(),
    )
    if top.returncode != 0:
        print(f"{args.repo} is not inside a git repository", file=sys.stderr)
        return 2

    repo = Repo(top.stdout.strip())
    ref_root = "refs/heads/" if args.local else f"refs/remotes/{args.remote}/"
    prefix = "" if args.local else f"{args.remote}/"
    main_ref = args.main or (DEFAULT_MAIN if args.local else f"{args.remote}/{DEFAULT_MAIN}")

    if args.fetch:
        try:
            repo.fetch(args.remote)
        except GitError as exc:
            print(f"fetch failed, using existing refs: {exc}", file=sys.stderr)
    else:
        print("note: refs may be stale; pass --fetch to prune and update first")

    if repo.rev_parse(main_ref) is None:
        print(f"{main_ref} does not exist -- nothing to compare against", file=sys.stderr)
        return 2

    names = [b for b in repo.branches(ref_root) if f"{prefix}{b}" != main_ref]
    if not names:
        print(f"no branches under {ref_root}")
        return 0

    verdicts = [
        classify(repo, f"{prefix}{name}", main_ref, max_candidates=args.max_candidates)
        for name in names
    ]

    print(f"--- {len(verdicts)} branch(es) under {ref_root}, against {main_ref} ---")
    for verdict in verdicts:
        print(verdict.line())

    to_delete = [v for v in verdicts if v.deletable and v.kind != "gone"]
    undecided = [v for v in verdicts if v.kind in UNDECIDED_KINDS]
    print(
        f"--- {len(to_delete)} deletable, {len(verdicts) - len(to_delete)} keep, "
        f"{len(undecided)} undecided ---"
    )
    for verdict in undecided:
        print(f"  {verdict.branch}: {verdict.evidence} -- left alone, inspect by hand")

    failed: list[str] = []
    if args.delete:
        for verdict in to_delete:
            name = verdict.branch[len(prefix):]
            try:
                repo.delete(name, ref_root, args.remote)
            except GitError as exc:
                print(f"failed to delete {verdict.branch}: {exc}", file=sys.stderr)
                failed.append(verdict.branch)
                continue
            print(f"deleted {verdict.branch}")
    elif to_delete:
        print("(report only -- pass --delete to remove these)")

    if failed:
        return 1
    if args.strict and undecided:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
