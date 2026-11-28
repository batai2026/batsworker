"""Tests for scripts/check_suppressions.py -- dead and unmapped acceptances.

The point of this check is to fail on debt that has quietly stopped mattering.
A test suite that only proves it *passes* on a clean config would let it rot
back into decoration, so every failure mode it exists for gets a case here,
built from a synthetic lockfile tree so the real one can move freely:

  * a package absent from every lockfile (the unic-* case)
  * a package present but pinned past the fixed version (the zip case)
  * an id that maps to no advisory at all (the phantom 21st entry)
  * a lockfile that cannot be read -- which must be exit 2, not "dead"
  * an unreadable/missing lockfile must never be read as "package gone"

Plus two things that keep the artefact honest: the register in docs/ must equal
what the live osv-scanner.toml produces, and the version comparison must treat a
prerelease as *not* patched.
"""

from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import check_suppressions as cs  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


# --- fixtures: a synthetic repo root ----------------------------------------


def cargo_lock(*packages: tuple[str, str]) -> str:
    blocks = [
        "version = 4\n",
        *[f'[[package]]\nname = "{n}"\nversion = "{v}"\n' for n, v in packages],
    ]
    return "\n".join(blocks)


def npm_lock(*packages: tuple[str, str]) -> str:
    return json.dumps(
        {"lockfileVersion": 3, "packages": {
            f"node_modules/{n}": {"version": v} for n, v in packages
        }},
        indent=2,
    )


def uv_lock(*packages: tuple[str, str]) -> str:
    blocks = [
        "version = 1\nrevision = 5\n",
        *[f'[[package]]\nname = "{n}"\nversion = "{v}"\n' for n, v in packages],
    ]
    return "\n".join(blocks)


def make_root(tmp_path: Path, *, config: str,
              cargo: str | None = None, npm: str | None = None,
              uv: str | None = None) -> Path:
    (tmp_path / "osv-scanner.toml").write_text(config, encoding="utf-8")
    if cargo is not None:
        (tmp_path / "surfaces/gui/src-tauri").mkdir(parents=True, exist_ok=True)
        (tmp_path / "surfaces/gui/src-tauri/Cargo.lock").write_text(cargo, encoding="utf-8")
        (tmp_path / "stt").mkdir(parents=True, exist_ok=True)
        (tmp_path / "stt/Cargo.lock").write_text(
            'version = 4\n[[package]]\nname = "nothing-here"\nversion = "1.0.0"\n',
            encoding="utf-8")
    if npm is not None:
        (tmp_path / "surfaces/gui").mkdir(parents=True, exist_ok=True)
        (tmp_path / "surfaces/gui/package-lock.json").write_text(npm, encoding="utf-8")
    if uv is not None:
        (tmp_path / "uv.lock").write_text(uv, encoding="utf-8")
    return tmp_path


def run(root: Path, *extra: str) -> tuple[int, str]:
    import io
    from contextlib import redirect_stdout, redirect_stderr
    buf = io.StringIO()
    err = io.StringIO()
    with redirect_stdout(buf), redirect_stderr(err):
        code = cs.main(["--root", str(root), *extra])
    return code, buf.getvalue() + err.getvalue()


# --- the failure modes ------------------------------------------------------


def test_package_absent_from_lockfiles_is_dead(tmp_path):
    """5 unic-* rows were exactly this: the pin was gone, the entry lingered."""
    make_root(
        tmp_path,
        config=(
            '[[IgnoredVulns]]\nid = "RUSTSEC-2025-0075"\n'
            'reason = "unic-char-range is not in the graph."\n'
            "effectiveUntil = 2027-08-01\n"
        ),
        cargo=cargo_lock(("serde", "1.0.200")),
    )
    code, out = run(tmp_path, "--today", "2026-11-18")
    assert code == 1, out
    assert "DEAD" in out
    assert "unic-char-range is in no crates.io lockfile" in out


def test_package_pinned_past_fixed_version_is_dead(tmp_path):
    """6 zip rows were this: pinned 9.6.0, advisory fixed >= 9.0.0."""
    make_root(
        tmp_path,
        config=(
            '[[IgnoredVulns]]\nid = "GHSA-q7v9-x93c-xqv3"\n'
            'reason = "zip is not reachable."\n'
            "effectiveUntil = 2027-08-01\n"
        ),
        cargo=cargo_lock(("zip", "9.6.0")),
    )
    code, out = run(tmp_path, "--today", "2026-11-18")
    assert code == 1, out
    assert "at or past the fixed version 9.0.0" in out


def test_package_pinned_below_fixed_version_stays_alive(tmp_path):
    """The mirror image: if the tree really were exposed, the entry is not dead."""
    make_root(
        tmp_path,
        config=(
            '[[IgnoredVulns]]\nid = "GHSA-q7v9-x93c-xqv3"\n'
            'reason = "zip is not reachable."\n'
            "effectiveUntil = 2027-08-01\n"
        ),
        cargo=cargo_lock(("zip", "8.2.0")),
    )
    code, out = run(tmp_path, "--today", "2026-11-18")
    assert code == 0, out
    assert "alive" in out.lower() or "Accepted" in out


def test_prerelease_does_not_count_as_patched(tmp_path):
    """`9.0.0-rc1` sorts before `9.0.0`; treating it as fixed would hide a real gap."""
    make_root(
        tmp_path,
        config=(
            '[[IgnoredVulns]]\nid = "GHSA-q7v9-x93c-xqv3"\n'
            'reason = "x"\n'
            "effectiveUntil = 2027-08-01\n"
        ),
        cargo=cargo_lock(("zip", "9.0.0-rc1")),
    )
    code, out = run(tmp_path, "--today", "2026-11-18")
    assert code == 0, "a prerelease must not be judged patched\n" + out


def test_unknown_advisory_id_is_unmapped(tmp_path):
    """The 21st entry was a comment string; a real unknown id must be named too."""
    make_root(
        tmp_path,
        config=(
            '[[IgnoredVulns]]\nid = "RUSTSEC-2099-9999"\n'
            'reason = "nobody remembers why this is here."\n'
            "effectiveUntil = 2027-08-01\n"
        ),
        cargo=cargo_lock(("serde", "1.0.200")),
    )
    code, out = run(tmp_path, "--today", "2026-11-18")
    assert code == 1, out
    assert "UNMAPPED" in out
    assert "RUSTSEC-2099-9999" in out


def test_missing_lockfile_for_an_ecosystem_exits_2_not_dead(tmp_path):
    """No Cargo.lock must not be read as 'the crate vanished' -- that would be a
    false accusation against every entry, which is worse than no check."""
    (tmp_path / "osv-scanner.toml").write_text(
        '[[IgnoredVulns]]\nid = "RUSTSEC-2024-0370"\n'
        'reason = "unmaintained."\neffectiveUntil = 2027-08-01\n',
        encoding="utf-8")
    # npm and uv exist; Cargo.lock deliberately does not.
    (tmp_path / "surfaces/gui").mkdir(parents=True, exist_ok=True)
    (tmp_path / "surfaces/gui/package-lock.json").write_text(
        npm_lock(("picomatch", "4.0.7")), encoding="utf-8")
    (tmp_path / "uv.lock").write_text(uv_lock(("requests", "2.32.0")), encoding="utf-8")

    code, out = run(tmp_path, "--today", "2026-11-18")
    assert code == 2, out
    assert "no crates.io lockfile" in out
    assert "Refusing to judge" in out


def test_unparseable_config_exits_2(tmp_path):
    """osv-scanner rejects a bad file outright, so every acceptance is already
    inactive; say that rather than reporting a clean scan."""
    make_root(tmp_path, config='[[IgnoredVulns]\nid = "broken"\n',
              cargo=cargo_lock(("serde", "1.0.200")))
    code, out = run(tmp_path)
    assert code == 2, out
    assert "does not parse" in out


def test_expiry_inside_window_warns_but_does_not_fail(tmp_path):
    """glib lapses 2027-03-01; an acceptance still valid today must not red a push."""
    make_root(
        tmp_path,
        config=(
            '[[IgnoredVulns]]\nid = "GHSA-wrw7-89jp-8q8g"\n'
            'reason = "gtk pins glib ^0.18."\n'
            "effectiveUntil = 2027-03-01\n"
        ),
        cargo=cargo_lock(("glib", "0.18.5")),
    )
    # 30 days out: inside the 90-day window.
    code, out = run(tmp_path, "--today", "2027-01-30")
    assert code == 0, out
    assert "::warning" in out
    assert "lapses 2027-03-01" in out


def test_expiry_beyond_window_is_quiet(tmp_path):
    make_root(
        tmp_path,
        config=(
            '[[IgnoredVulns]]\nid = "GHSA-wrw7-89jp-8q8g"\n'
            'reason = "gtk pins glib ^0.18."\n'
            "effectiveUntil = 2027-03-01\n"
        ),
        cargo=cargo_lock(("glib", "0.18.5")),
    )
    code, out = run(tmp_path, "--today", "2026-06-01")
    assert code == 0, out
    assert "::warning" not in out


def test_ignoreUntil_is_honoured_as_the_old_spelling(tmp_path):
    """CONTRIBUTING still allows the deprecated key; the checker must read it."""
    make_root(
        tmp_path,
        config=(
            '[[IgnoredVulns]]\nid = "GHSA-wrw7-89jp-8q8g"\n'
            'reason = "gtk pins glib ^0.18."\n'
            "ignoreUntil = 2027-03-01\n"
        ),
        cargo=cargo_lock(("glib", "0.18.5")),
    )
    code, out = run(tmp_path, "--today", "2027-01-30")
    assert code == 0, out
    assert "lapses 2027-03-01" in out


# --- --check-expiry: a close date must red the PR gate, not just warn ---------


def test_check_expiry_fails_when_lapsing_within_window(tmp_path):
    """The PR gate passes --check-expiry 30; an acceptance 45 days out is inside
    the 90-day warning window but outside the 30-day fail window, so warn not fail."""
    make_root(
        tmp_path,
        config=(
            '[[IgnoredVulns]]\nid = "GHSA-wrw7-89jp-8q8g"\n'
            'reason = "gtk pins glib ^0.18."\n'
            "effectiveUntil = 2027-03-01\n"
        ),
        cargo=cargo_lock(("glib", "0.18.5")),
    )
    # 45 days before expiry: warning window (90), not fail window (30).
    code, out = run(tmp_path, "--today", "2027-01-15", "--check-expiry", "30")
    assert code == 0, out
    assert "::warning" in out
    assert "::error" not in out


def test_check_expiry_fails_when_lapsing_within_window_default(tmp_path):
    """--check-expiry with no argument defaults to 30 days: 20 days out fails."""
    make_root(
        tmp_path,
        config=(
            '[[IgnoredVulns]]\nid = "GHSA-wrw7-89jp-8q8g"\n'
            'reason = "gtk pins glib ^0.18."\n'
            "effectiveUntil = 2027-03-01\n"
        ),
        cargo=cargo_lock(("glib", "0.18.5")),
    )
    code, out = run(tmp_path, "--today", "2027-02-09", "--check-expiry")
    assert code == 1, out
    assert "::error" in out
    assert "re-argue or upgrade" in out


def test_check_expiry_fails_when_already_lapsed(tmp_path):
    """A lapsed acceptance must fail the PR gate too (osv-scanner would surface
    it as a finding; here it is caught at pull-request time, not at the next
    scheduled run)."""
    make_root(
        tmp_path,
        config=(
            '[[IgnoredVulns]]\nid = "GHSA-wrw7-89jp-8q8g"\n'
            'reason = "gtk pins glib ^0.18."\n'
            "effectiveUntil = 2027-03-01\n"
        ),
        cargo=cargo_lock(("glib", "0.18.5")),
    )
    code, out = run(tmp_path, "--today", "2027-03-02", "--check-expiry", "30")
    assert code == 1, out
    assert "::error" in out
    assert "re-argue or upgrade before it goes red" in out
    assert "-1 day(s) away" in out  # a negative count is the lapsed signal


def test_check_expiry_does_not_fail_when_quiet(tmp_path):
    """A comfortably-future date stays green with --check-expiry set."""
    make_root(
        tmp_path,
        config=(
            '[[IgnoredVulns]]\nid = "GHSA-wrw7-89jp-8q8g"\n'
            'reason = "gtk pins glib ^0.18."\n'
            "effectiveUntil = 2027-03-01\n"
        ),
        cargo=cargo_lock(("glib", "0.18.5")),
    )
    code, out = run(tmp_path, "--today", "2026-06-01", "--check-expiry", "30")
    assert code == 0, out
    assert "::error" not in out
    assert "::warning" not in out


def test_check_expiry_is_opt_in_no_behaviour_change(tmp_path):
    """Without the flag, the existing warning-but-green semantics must not change.
    This is the compatibility tripwire for the suppression-register job."""
    make_root(
        tmp_path,
        config=(
            '[[IgnoredVulns]]\nid = "GHSA-wrw7-89jp-8q8g"\n'
            'reason = "gtk pins glib ^0.18."\n'
            "effectiveUntil = 2027-03-01\n"
        ),
        cargo=cargo_lock(("glib", "0.18.5")),
    )
    # 20 days out: inside the 90-day warning window.
    code, out = run(tmp_path, "--today", "2027-02-09")
    assert code == 0, out
    assert "::warning" in out
    assert "::error" not in out


# --- the version comparison itself ------------------------------------------


@pytest.mark.parametrize("raw, expected", [
    ("9.6.0", (9, 6)),
    ("0.20.0", (0, 20)),
    ("1.0", (1,)),
    ("1.0.0.0", (1,)),
    ("2.0.0-rc1", (2,)),
])
def test_version_parse(raw, expected):
    assert cs.Version.parse(raw).release == expected


def test_version_ordering():
    p = cs.Version.parse
    assert p("9.0.0-rc1") < p("9.0.0")
    assert p("9.0.0") < p("9.6.0")
    assert p("0.18.5") < p("0.20.0")
    assert not (p("9.6.0") < p("9.0.0"))


# --- the artefact must not drift from the config ----------------------------


def test_register_matches_live_config():
    """docs/dependency-risk-register.md is generated; a hand edit that diverges
    from osv-scanner.toml would mislead the next reader, so refuse it."""
    register = ROOT / "docs" / "dependency-risk-register.md"
    if not register.exists():
        pytest.skip("register not generated yet")

    entries = cs.parse_entries((ROOT / "osv-scanner.toml").read_text(encoding="utf-8"))
    pins = {eco: cs.load_pins(ROOT, eco)[0]
            for eco in {m["ecosystem"] for m in cs.ADVISORIES.values()}}
    expected = cs.render_markdown(cs.audit(entries, pins, dt.date(2026, 11, 18)),
                                  dt.date(2026, 11, 18))
    actual = register.read_text(encoding="utf-8")
    assert actual.strip() == expected.strip(), (
        "docs/dependency-risk-register.md is stale -- regenerate with:\n"
        "  python scripts/check_suppressions.py --markdown docs/dependency-risk-register.md"
    )


def test_register_does_not_change_with_the_calendar():
    """The register must be identical whichever day it is generated on.

    An earlier version stamped the snapshot date and a "days left" column into the
    file while this test compared against a PINNED date. The two could only agree
    on one day: from the next midnight the drift guard went red, and following the
    regeneration command in its own error message produced a file that could never
    match again. A committed countdown is a number that is wrong by tomorrow --
    the live countdown belongs in stdout. This test is the tripwire.
    """
    entries = cs.parse_entries((ROOT / "osv-scanner.toml").read_text(encoding="utf-8"))
    pins = {eco: cs.load_pins(ROOT, eco)[0]
            for eco in {m["ecosystem"] for m in cs.ADVISORIES.values()}}
    renders = {}
    for day in (dt.date(2026, 11, 18), dt.date(2026, 11, 19),
                dt.date(2027, 1, 15), dt.date(2030, 6, 30)):
        renders[day] = cs.render_markdown(cs.audit(entries, pins, day), day)
    distinct = set(renders.values())
    assert len(distinct) == 1, (
        "the generated register varies with the date -- remove the snapshot date "
        "and the days-left column from render_markdown:\n"
        + "\n\n".join(sorted(distinct))[:800]
    )


def test_register_has_no_volatile_columns():
    """A committed 'days left' column can only ever be right on one day."""
    entries = cs.parse_entries((ROOT / "osv-scanner.toml").read_text(encoding="utf-8"))
    pins = {eco: cs.load_pins(ROOT, eco)[0]
            for eco in {m["ecosystem"] for m in cs.ADVISORIES.values()}}
    text = cs.render_markdown(cs.audit(entries, pins, dt.date(2026, 11, 18)),
                              dt.date(2026, 11, 18))
    header = next(ln for ln in text.splitlines() if ln.startswith("| due"))
    assert "days left" not in header.lower(), header
    assert "Snapshot" not in text, "the snapshot date must not enter the file"


def test_every_live_entry_is_mapped():
    """A new entry added without an ADVISORIES row would show up as UNMAPPED in
    CI; assert it here too so the failure is local and legible."""
    entries = cs.parse_entries((ROOT / "osv-scanner.toml").read_text(encoding="utf-8"))
    for entry in entries:
        vid = str(entry.get("id") or "")
        assert vid.lower() in cs.ADVISORIES, (
            f"{vid} has no entry in scripts/check_suppressions.py ADVISORIES -- "
            "add the package it belongs to, or drop the suppression"
        )


def test_live_repo_is_clean():
    """The real tree must pass: every entry alive, nothing dead or unmapped.

    Deliberately no assertion on the *number* of entries. The count is a fact about
    today's debt, not an invariant, and pinning it means every legitimate deletion
    (see 2026-11-26: a withdrawn advisory) has to be accompanied by an edit here,
    which quietly turns this test into a change-detector. What must hold is the
    status of each entry; the "report every entry" assertion is what keeps this
    from passing vacuously on an empty config.
    """
    code, out = run(ROOT, "--today", "2026-11-18")
    assert code == 0, out
    assert "suppression(s):" in out
    assert "0 dead" in out
    assert "0 unmapped" in out
    # Non-vacuity: a config that suppresses nothing would satisfy the checks above.
    n = len(cs.parse_entries((ROOT / "osv-scanner.toml").read_text(encoding="utf-8")))
    assert n >= 1, "osv-scanner.toml has no [[IgnoredVulns]] at all"
    assert f"{n} suppression(s)" in out, out
