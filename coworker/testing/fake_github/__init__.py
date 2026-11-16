"""FakeGitHub — a local test double for the handful of REST endpoints we script against.

See :mod:`coworker.testing.fake_github.server`. Used by ``tests/test_merge_pr_script.py``
to exercise ``scripts/merge_pr.ps1`` (an irreversible operation) without a real
repository, and runnable standalone to poke at the script by hand.
"""

from __future__ import annotations

from .server import FakeGitHub, State

__all__ = ["FakeGitHub", "State"]
