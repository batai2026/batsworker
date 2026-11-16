"""Tests for the SID-based ACL reader used by the secrets test.

The point of `_win_acl` is that the Windows secrets assertion must not depend on localized
text. So these tests check the reader against ACLs built for the purpose, and each one
pins a property the old text-parsing assertion got wrong.

Everything here is Windows-only by nature; on other platforms the module is not exercised
and the tests skip.
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

import _win_acl

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows ACLs")


def _everyone_sid() -> str:
    return "S-1-1-0"


def test_reads_a_restricted_acl(tmp_path):
    """A file restricted to this user looks exactly like this -- and nothing else."""
    p = tmp_path / "secrets.json"
    p.write_text("{}", encoding="utf-8")
    me = _win_acl.read_acl(tmp_path).me
    r = subprocess.run(
        ["icacls", str(p), "/inheritance:r", "/grant:r", f"*{me}:F"],
        capture_output=True,
    )
    assert r.returncode == 0, r.stderr.decode("gbk", "replace")

    acl = _win_acl.read_acl(p)
    assert acl.inheritance_off is True
    assert acl.inherited_aces == []
    # The current user is granted, and nobody else beyond the identities that can read
    # any file anyway. Asserting allow_sids == {me} instead assumed a bare temp directory
    # with only one ACE, which is what a developer machine looks like and not what a CI
    # runner looks like -- real files carry SYSTEM / Administrators / OWNER RIGHTS.
    assert acl.me in acl.allow_sids
    assert acl.broad_allow_sids == set(), acl.describe()


def test_detects_an_acl_that_grants_someone_else(tmp_path):
    """The decisive regression: an ACL granting only Everyone is NOT restricted to me.

    The assertion the old test used (`USERNAME in icacls output`) passes on exactly this
    file, because icacls prints the path, the path is under the user's profile, and the
    path therefore contains the username. That makes the old check unable to fail. This
    test asserts the reader can tell the two apart.
    """
    p = tmp_path / "secrets.json"
    p.write_text("{}", encoding="utf-8")
    r = subprocess.run(
        ["icacls", str(p), "/inheritance:r", "/grant:r", f"*{_everyone_sid()}:F"],
        capture_output=True,
    )
    assert r.returncode == 0, r.stderr.decode("gbk", "replace")

    acl = _win_acl.read_acl(p)
    # Everyone is still granted -- that is the leak this test is about -- regardless of
    # whatever else the platform added (SYSTEM, Administrators, OWNER RIGHTS).
    assert _everyone_sid() in acl.allow_sids, acl.describe()
    assert acl.allow_sids != {acl.me}, acl.describe()
    assert _everyone_sid() in acl.broad_allow_sids, acl.describe()


def test_detects_inheritance_left_intact(tmp_path):
    """An untouched file under temp inherits ACEs; the reader must report them."""
    p = tmp_path / "loose.json"
    p.write_text("{}", encoding="utf-8")
    acl = _win_acl.read_acl(p)
    # Fresh files inherit from tmp_path's parent, so inheritance is protected=False.
    assert acl.inheritance_off is False
    if acl.inherited_aces:  # normally true; empty on unusual parent DACLs
        assert all(a.inherited for a in acl.inherited_aces)


def test_allow_sids_counts_only_allow_aces(tmp_path):
    """Deny ACEs must not be mistaken for grants -- they are the opposite."""
    p = tmp_path / "mixed.json"
    p.write_text("{}", encoding="utf-8")
    me = _win_acl.read_acl(tmp_path).me
    subprocess.run(
        ["icacls", str(p), "/inheritance:r", "/grant:r", f"*{me}:F"],
        capture_output=True,
        check=True,
    )
    subprocess.run(
        ["icacls", str(p), "/deny", f"*{_everyone_sid()}:(DE,WDAC)"],
        capture_output=True,
        check=True,
    )

    acl = _win_acl.read_acl(p)
    assert acl.me in acl.allow_sids, acl.describe()
    assert acl.broad_allow_sids == set(), acl.describe()
    assert any(a.type == "Deny" and a.sid == _everyone_sid() for a in acl.aces)


def test_a_failed_read_is_an_error_not_an_empty_acl(tmp_path):
    """If the ACL can't be read we must raise: an empty ACL would make 'nothing is
    inherited' pass vacuously, which is the same class of bug as the text matching."""
    with pytest.raises(RuntimeError):
        _win_acl.read_acl(tmp_path / "does-not-exist" / "nope.json")


def test_reader_rejects_non_windows(monkeypatch, tmp_path):
    """Guard the guard: the POSIX branch must not silently 'measure' anything."""
    monkeypatch.setattr(os, "name", "posix")
    with pytest.raises(RuntimeError):
        _win_acl.read_acl(tmp_path)
