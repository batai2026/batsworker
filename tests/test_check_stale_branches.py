"""Tests for scripts/check_stale_branches.py.

The script exists because a branch-deletion judgement was re-derived by hand
three times in one session, and because the two ways of getting it wrong are
both silent:

* **Ancestry after a squash merge.** ``git branch --merged`` never lists a
  squash-merged branch again, so "not listed" reads as "has unmerged work"
  forever. ``test_squash_merged_branch_is_deletable`` pins both halves: that
  ancestry really does miss it, and that the verdict still comes out correct.
* **Deletion lines in ``git diff <branch> main --numstat``.** Those lines are
  main's later work, not evidence of loss.
  ``test_deletions_in_diff_branch_main_are_not_a_signal`` builds that exact
  shape and asserts the verdict does not move.

Everything uses real throwaway repositories: the questions asked here (does
this diff reproduce that one) are git's own semantics, and a mock would only
prove the mock. The tests therefore need a ``git`` binary and skip without one.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import check_stale_branches as csb  # noqa: E402

GIT = shutil.which("git")
pytestmark = pytest.mark.skipif(GIT is None, reason="needs git on PATH")

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_stale_branches.py"


# -- helpers -------------------------------------------------------------------


def git(cwd: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True,
        text=True, encoding="utf-8", errors="replace",
    )
    assert proc.returncode == 0, f"git {' '.join(args)} failed: {proc.stderr}"
    return proc.stdout.strip()


def write(repo: Path, name: str, text: str) -> None:
    path = repo / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def commit(repo: Path, message: str) -> str:
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", message)
    return git(repo, "rev-parse", "HEAD")


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    work = tmp_path / "repo"
    work.mkdir()
    git(work, "init")
    git(work, "symbolic-ref", "HEAD", "refs/heads/main")
    git(work, "config", "user.email", "test@example.invalid")
    git(work, "config", "user.name", "Test")
    git(work, "config", "commit.gpgsign", "false")
    write(work, "README.md", "base\n")
    commit(work, "base")
    return work


def verdict(repo: Path, branch: str, main: str = "main", **kw) -> csb.Verdict:
    return csb.classify(csb.Repo(repo), branch, main, **kw)


def branch_with_commit(repo: Path, name: str, text: str = "work\n") -> str:
    git(repo, "checkout", "-q", "-b", name, "main")
    write(repo, "feature.txt", text)
    sha = commit(repo, f"{name} work")
    git(repo, "checkout", "-q", "main")
    return sha


# -- verdict arms --------------------------------------------------------------


def test_merged_branch_is_deletable(repo: Path) -> None:
    branch_with_commit(repo, "feat")
    git(repo, "merge", "-q", "--no-ff", "-m", "merge feat", "feat")

    got = verdict(repo, "feat")
    assert got.kind == "merged"
    assert got.deletable is True


def test_squash_merged_branch_is_deletable(repo: Path) -> None:
    """The case ancestry cannot answer: squash rewrites the commit away."""
    branch_with_commit(repo, "feat")
    git(repo, "merge", "-q", "--squash", "feat")
    commit(repo, "feat (#1)")
    # Main must move past the squash commit, or the cheap "identical tree"
    # check answers first and the squash scan is never exercised -- which is
    # also the realistic shape: this branch sat around after main moved on.
    write(repo, "README.md", "main kept moving\n")
    commit(repo, "later main work")

    # Document the trap the script exists for: ancestry says "not merged".
    assert "feat" not in git(repo, "branch", "--merged", "main").split()

    got = verdict(repo, "feat")
    assert got.kind == "squash", got
    assert got.deletable is True
    assert "landed as" in got.evidence


def test_squash_of_several_commits_is_found(repo: Path) -> None:
    """The scan must survive a branch tip that is not itself the landed change."""
    git(repo, "checkout", "-q", "-b", "feat", "main")
    write(repo, "a.txt", "one\n")
    commit(repo, "step 1")
    write(repo, "b.txt", "two\n")
    commit(repo, "step 2")
    git(repo, "checkout", "-q", "main")
    git(repo, "merge", "-q", "--squash", "feat")
    landed = commit(repo, "feat collapsed (#2)")
    write(repo, "README.md", "main kept moving\n")
    commit(repo, "later main work")

    got = verdict(repo, "feat")
    assert got.kind == "squash"
    assert landed[:12] in got.evidence


def test_unmerged_branch_is_kept(repo: Path) -> None:
    branch_with_commit(repo, "feat", "unique work\n")
    write(repo, "elsewhere.txt", "main moved on\n")
    commit(repo, "unrelated main work")

    got = verdict(repo, "feat")
    assert got.kind == "unmerged"
    assert got.deletable is False
    assert "ahead of" in got.evidence


def test_same_paths_different_content_is_kept(repo: Path) -> None:
    """A near-miss must not read as a squash: same file, different bytes."""
    branch_with_commit(repo, "feat", "branch version\n")
    write(repo, "feature.txt", "main version\n")
    commit(repo, "main edits the same file")

    got = verdict(repo, "feat")
    assert got.kind == "unmerged", got
    assert got.deletable is False


def test_squash_carrying_extra_work_is_kept(repo: Path) -> None:
    """The known false negative, pinned rather than papered over.

    If the squash commit also carries work the branch never had, no commit on
    main reproduces the branch's change exactly, so the answer is KEEP. A human
    looks at it. Asserting this keeps the behaviour from silently drifting into
    "close enough" -- which is how work gets dropped.
    """
    branch_with_commit(repo, "feat", "feature\n")
    write(repo, "extra.txt", "something main added in the same squash\n")
    git(repo, "add", "-A")
    git(repo, "merge", "-q", "--squash", "--no-commit", "feat")
    commit(repo, "feat plus a manual tweak (#6)")
    write(repo, "README.md", "main kept moving\n")
    commit(repo, "later main work")

    got = verdict(repo, "feat")
    assert got.kind == "unmerged", got
    assert got.deletable is False


def test_equivalent_patch_landed_by_cherry_pick(repo: Path) -> None:
    """A cherry-pick makes a *different commit* with the same change.

    The committer date is forced apart, because two commits created in the same
    second are byte-identical objects and git then reports the branch as an
    ordinary ancestor -- which would test nothing.
    """
    sha = branch_with_commit(repo, "feat", "cherry work\n")

    env_date = {"GIT_COMMITTER_DATE": "2030-01-01T00:00:00 +0000"}
    proc = subprocess.run(
        ["git", "cherry-pick", sha], cwd=str(repo), capture_output=True, text=True,
        encoding="utf-8", errors="replace", env={**os.environ, **env_date},
    )
    assert proc.returncode == 0, proc.stderr
    write(repo, "README.md", "main kept moving\n")
    commit(repo, "later main work")

    tip = git(repo, "rev-parse", "feat")
    assert git(repo, "rev-parse", "main~1") != tip, "cherry-pick reused the object"
    got = verdict(repo, "feat")
    assert got.deletable is True
    assert got.kind == "squash", got


def test_unrelated_history_is_kept(repo: Path) -> None:
    git(repo, "checkout", "-q", "--orphan", "orphan")
    write(repo, "only.txt", "no common ancestor\n")
    commit(repo, "orphan root")
    git(repo, "checkout", "-q", "main")

    got = verdict(repo, "orphan")
    assert got.kind == "unrelated"
    assert got.deletable is False


def test_work_reimplemented_by_hand_is_kept(repo: Path) -> None:
    """The false negative seen in the real repo, reproduced.

    ``fix/osv-withdrawn-suppression`` was ported into main across PRs #78 and
    #79 with the change split up and reworded. Nothing on main reproduces its
    diff and ``git cherry`` answers ``+``, so the honest verdict is KEEP.
    Pinning it means a future change that starts calling this shape deletable
    has to argue with a test naming the actual branch.

    The complementary case -- a faithful port -- is
    ``test_equivalent_patch_landed_by_cherry_pick``.
    """
    git(repo, "checkout", "-q", "-b", "feat", "main")
    write(repo, "a.txt", "one\n")
    write(repo, "b.txt", "two\n")
    commit(repo, "change two files at once")
    git(repo, "checkout", "-q", "main")

    # Re-implemented piecemeal, in two commits, on main.
    write(repo, "a.txt", "one, reworded\n")
    commit(repo, "part 1 of the feature")
    write(repo, "b.txt", "two, also reworded\n")
    commit(repo, "part 2 of the feature")

    assert git(repo, "cherry", "main", "feat").startswith("+")
    got = verdict(repo, "feat")
    assert got.kind == "unmerged", got
    assert got.deletable is False


def test_too_many_candidates_is_inconclusive_not_a_guess(repo: Path) -> None:
    branch_with_commit(repo, "feat")
    write(repo, "main1.txt", "1\n")
    commit(repo, "main 1")
    write(repo, "main2.txt", "2\n")
    commit(repo, "main 2")

    got = verdict(repo, "feat", max_candidates=1)
    assert got.kind == "inconclusive"
    assert got.deletable is False
    assert "cap" in got.evidence


def test_deletions_in_diff_branch_main_are_not_a_signal(repo: Path) -> None:
    """The trap: `git diff <branch> main --numstat` shows removals for a branch
    that lost nothing. Those lines are main's own later work."""
    branch_with_commit(repo, "feat", "feature\n")
    git(repo, "merge", "-q", "--squash", "feat")
    commit(repo, "feat (#3)")
    git(repo, "rm", "-q", "README.md")
    write(repo, "later.txt", "added later\n")
    commit(repo, "later main work")

    # The misleading shape: main deleting a file reads as "feat lost this file".
    numstat = git(repo, "diff", "feat", "main", "--numstat")
    assert "0\t1\tREADME.md" in numstat, numstat
    assert any(line.startswith("-") for line in
               git(repo, "diff", "feat", "main", "--", "README.md").splitlines())

    got = verdict(repo, "feat")
    assert got.deletable is True, got
    assert got.kind == "squash"


def test_the_same_bytes_with_a_different_mode_are_not_the_same_change(repo: Path) -> None:
    """Identical content, different permission bits -- and the mode decides.

    The branch adds ``run.sh`` executable. On main the same bytes appear without
    the bit (a hand-applied copy, an editor that dropped it). Blob ids are
    equal; the change is not. If the comparison looked at content alone this
    would read as landed and delete the branch -- losing the only record that
    the file was meant to be executable.
    """
    script = "#!/bin/sh\necho hi\n"
    git(repo, "checkout", "-q", "-b", "feat", "main")
    write(repo, "run.sh", script)
    git(repo, "add", "run.sh")
    git(repo, "update-index", "--chmod=+x", "run.sh")
    commit(repo, "add executable script")
    git(repo, "checkout", "-q", "main")

    # Same bytes, no exec bit, no squash commit.
    write(repo, "run.sh", script)
    git(repo, "add", "run.sh")
    commit(repo, "recreate run.sh without the exec bit")
    write(repo, "README.md", "main kept moving\n")
    commit(repo, "later main work")

    assert git(repo, "rev-parse", "main:run.sh") == git(repo, "rev-parse", "feat:run.sh")
    base = git(repo, "merge-base", "main", "feat")
    assert "mode" in git(repo, "diff", base, "feat"), "the scan needs a mode line"

    got = verdict(repo, "feat")
    assert got.kind == "unmerged", got
    assert got.deletable is False


# -- CLI -----------------------------------------------------------------------


def run_cli(*args: str, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=timeout,
    )


def test_cli_reports_and_deletes_nothing(repo: Path) -> None:
    branch_with_commit(repo, "used")
    git(repo, "merge", "-q", "--no-ff", "-m", "merge used", "used")
    branch_with_commit(repo, "wanted", "keep me\n")

    result = run_cli("--repo", str(repo), "--local")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "DELETE" in result.stdout and "used" in result.stdout
    assert "KEEP" in result.stdout and "wanted" in result.stdout
    assert "pass --delete" in result.stdout
    # Still there.
    assert "used" in git(repo, "branch", "--format=%(refname:short)").split()


def test_cli_delete_removes_only_the_deletable(repo: Path) -> None:
    branch_with_commit(repo, "used")
    git(repo, "merge", "-q", "--no-ff", "-m", "merge used", "used")
    branch_with_commit(repo, "wanted", "keep me\n")

    result = run_cli("--repo", str(repo), "--local", "--delete")
    assert result.returncode == 0, result.stdout + result.stderr
    remaining = git(repo, "branch", "--format=%(refname:short)").split()
    assert "used" not in remaining
    assert "wanted" in remaining


def test_cli_strict_fails_on_an_undecided_branch(repo: Path) -> None:
    git(repo, "checkout", "-q", "--orphan", "orphan")
    write(repo, "only.txt", "x\n")
    commit(repo, "orphan root")
    git(repo, "checkout", "-q", "main")

    assert run_cli("--repo", str(repo), "--local").returncode == 0
    strict = run_cli("--repo", str(repo), "--local", "--strict")
    assert strict.returncode == 1
    assert "orphan" in strict.stdout


def test_cli_refuses_outside_a_repository(tmp_path: Path) -> None:
    result = run_cli("--repo", str(tmp_path))
    assert result.returncode == 2
    assert "not inside a git repository" in result.stderr


def test_cli_remote_path_against_a_bare_remote(tmp_path: Path) -> None:
    """The real use: remote-tracking branches, deleted with `push --delete`."""
    bare = tmp_path / "remote.git"
    bare.mkdir()
    git(bare, "init", "--bare")
    work = tmp_path / "work"
    work.mkdir()
    git(work, "init")
    git(work, "symbolic-ref", "HEAD", "refs/heads/main")
    git(work, "config", "user.email", "test@example.invalid")
    git(work, "config", "user.name", "Test")
    write(work, "README.md", "base\n")
    commit(work, "base")
    git(work, "remote", "add", "origin", str(bare))
    git(work, "push", "-q", "-u", "origin", "main")

    # A squash-merged branch and an unmerged one, both on the remote.
    branch_with_commit(work, "done", "done\n")
    git(work, "push", "-q", "origin", "done")
    git(work, "merge", "-q", "--squash", "done")
    commit(work, "done (#4)")
    git(work, "push", "-q", "origin", "main")
    branch_with_commit(work, "pending", "pending\n")
    git(work, "push", "-q", "origin", "pending")

    result = run_cli("--repo", str(work), "--fetch", "--delete")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "deleted origin/done" in result.stdout

    on_remote = git(bare, "for-each-ref", "--format=%(refname:short)", "refs/heads")
    assert "done" not in on_remote.split()
    assert "pending" in on_remote.split()


def test_the_verdict_does_not_depend_on_a_local_ref_being_present(repo: Path) -> None:
    """A deleted ref reports 'gone' rather than raising."""
    got = verdict(repo, "no-such-branch")
    assert got.kind == "gone"
    assert got.deletable is True
