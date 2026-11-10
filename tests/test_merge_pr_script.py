"""scripts/merge_pr.ps1: the guards and the verdict, exercised against a fake API.

The script performs the one irreversible step in this repo's workflow (merging a PR),
so it is tested the way it is used: run as a subprocess, against a local double, with
its exit code as the assertion. Two properties matter, and neither is obvious from
reading it --

  * success is decided by ``merged = true``, not by the presence of a ``message`` field.
    GitHub returns both on success, so the naive check reports a completed merge as a
    failure (that was the original bug);
  * it refuses rather than guesses when the head moved or the PR is not clean.

PowerShell is needed to run it: ``pwsh`` on Linux/macOS and in CI, ``powershell`` on a
Windows box without PowerShell 7. Where neither exists the tests skip, rather than pass
vacuously.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from coworker.testing.fake_github import FakeGitHub
from coworker.testing.fake_github.server import HEAD_SHA, MERGED_SHA

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "merge_pr.ps1"


def _powershell() -> str | None:
    for name in ("pwsh", "powershell"):
        found = shutil.which(name)
        if found:
            return found
    return None


PS = _powershell()
pytestmark = pytest.mark.skipif(
    PS is None or not SCRIPT.exists(),
    reason="needs PowerShell and scripts/merge_pr.ps1",
)


def run(*args: str, timeout: int = 90) -> subprocess.CompletedProcess[str]:
    assert PS is not None
    return subprocess.run(
        [PS, "-NoProfile", "-NonInteractive", "-File", str(SCRIPT), *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


# -- the verdict, without any network ------------------------------------------------


def test_selftest_passes():
    """The four known response shapes resolve the right way, including the old bug."""
    result = run("-Pr", "1", "-SelfTest")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "SELFTEST=PASS" in result.stdout
    # The regression itself: a success payload must not be reported as a failure.
    assert "success_shape       -> ok=True" in result.stdout
    assert "conflict_shape      -> ok=False" in result.stdout
    assert "head_moved_shape    -> ok=False" in result.stdout
    assert "empty_shape         -> ok=False" in result.stdout


# -- the guards, against the fake ---------------------------------------------------


def test_merges_and_exits_zero():
    with FakeGitHub() as fake:
        result = run("-Pr", "9", "-ExpectedSha", HEAD_SHA, "-ApiBase", fake.base_url)

    assert result.returncode == 0, result.stdout + result.stderr
    assert f"merged=True sha={MERGED_SHA}" in result.stdout
    assert "merge_failed" not in result.stdout
    # It really merged, and it read main back afterwards.
    assert fake.state.merged is True
    assert fake.state.seen("PUT") == ["/pulls/9/merge"]
    assert "/git/ref/heads/main" in fake.state.seen("GET")


def test_conflict_exits_nonzero_and_reports_why():
    with FakeGitHub(mode="conflict") as fake:
        result = run("-Pr", "9", "-ExpectedSha", HEAD_SHA, "-ApiBase", fake.base_url)

    assert result.returncode == 1, result.stdout + result.stderr
    assert "merge_failed=Pull Request is not mergeable" in result.stdout
    assert fake.state.merged is False


def test_refuses_when_the_head_moved():
    """A force-push after review must not be merged silently."""
    with FakeGitHub(head_sha="a" * 40) as fake:
        result = run("-Pr", "9", "-ExpectedSha", HEAD_SHA, "-ApiBase", fake.base_url)

    assert result.returncode != 0
    assert "head moved since the reviewed run" in (result.stdout + result.stderr)
    assert fake.state.seen("PUT") == [], "merged despite the head having moved"


def test_refuses_when_not_clean():
    with FakeGitHub(mergeable_state="unstable") as fake:
        result = run("-Pr", "9", "-ExpectedSha", HEAD_SHA, "-ApiBase", fake.base_url)

    assert result.returncode != 0
    assert "mergeable_state=unstable" in (result.stdout + result.stderr)
    assert fake.state.seen("PUT") == []


def test_dry_run_runs_the_guards_but_never_puts():
    with FakeGitHub() as fake:
        result = run(
            "-Pr", "9", "-ExpectedSha", HEAD_SHA, "-ApiBase", fake.base_url, "-DryRun"
        )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "skipping the PUT" in result.stdout
    assert fake.state.seen("PUT") == []
    assert fake.state.merged is False


def test_the_real_token_is_never_sent_to_a_test_base():
    """Only api.github.com receives the credential; a local base gets a placeholder.

    Asserted from the server side, so it cannot be satisfied by the script merely
    claiming it: the fake records the Authorization header it actually received.
    """
    with FakeGitHub() as fake:
        run("-Pr", "9", "-ExpectedSha", HEAD_SHA, "-ApiBase", fake.base_url, "-DryRun")
        seen = list(fake.state.auth_headers)

    assert seen, "the fake saw no request at all"
    assert all(h and "not-a-real-token" in h for h in seen), seen
