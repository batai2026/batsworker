"""Tests for scripts/osv_delta.py -- the push gate's "did WE add this" decision.

The cases here are the ones that produced false reds on main, plus the ways a
delta tool can quietly pass a bad push. Each test names the behaviour, not the
implementation, so a rewrite that keeps the semantics still has to satisfy them.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import osv_delta  # noqa: E402


def scan(*results) -> dict:
    return {"results": list(results)}


def result(lockfile: str, *packages) -> dict:
    return {
        "source": {"path": lockfile, "type": "lockfile"},
        "packages": list(packages),
    }


def package(name: str, version: str, *vulns, ecosystem: str = "npm") -> dict:
    return {
        "package": {"name": name, "version": version, "ecosystem": ecosystem},
        "vulnerabilities": list(vulns),
    }


def vuln(
    vid: str,
    *,
    severity: str = "",
    fixed: tuple[str, ...] = (),
    summary: str = "",
    aliases: tuple[str, ...] = (),
) -> dict:
    doc = {
        "id": vid,
        "summary": summary,
        "aliases": list(aliases),
        "database_specific": {"severity": severity} if severity else {},
    }
    if fixed:
        events = [{"introduced": "0"}]
        events += [{"fixed": v} for v in fixed]
        doc["affected"] = [{"ranges": [{"events": events}]}]
    return doc


def write(tmp_path: Path, name: str, payload: dict) -> Path:
    p = tmp_path / name
    p.write_text(json.dumps(payload), encoding="utf-8")
    return p


def keys(tmp_path: Path, payload: dict, name: str = "s.json") -> set:
    return set(osv_delta.load_results(write(tmp_path, name, payload)))


# --- the decision itself -----------------------------------------------------


def test_advisory_appearing_on_unchanged_code_is_not_new(tmp_path):
    """The picomatch red: same version both sides, OSV republished the advisory.

    Both scans ran against the same OSV data, so the finding cancels out. This is
    the case that made `main` red at 2b292064 with no lockfile change.
    """
    lock = "surfaces/gui/package-lock.json"
    same = scan(
        result(lock, package("picomatch", "4.0.4", vuln("GHSA-fc5q-39xc-vjpf")))
    )
    base = osv_delta.load_results(write(tmp_path, "base.json", same))
    # The base scan is taken in the same run, so it sees the advisory too.
    head = osv_delta.load_results(write(tmp_path, "head.json", same))
    assert osv_delta.new_findings(base, head) == []


def test_upgrading_into_a_vulnerable_version_is_new(tmp_path):
    """Moving a package onto a version the advisory covers must block.

    Version is part of the key, so 1.0.0 -> 1.1.0 where only 1.1.0 is affected is
    reported even though the advisory id and package name are unchanged.
    """
    lock = "stt/Cargo.lock"
    base = osv_delta.load_results(
        write(tmp_path, "b.json", scan(result(lock, package("ureq", "1.0.0"))))
    )
    head = osv_delta.load_results(
        write(
            tmp_path,
            "h.json",
            scan(result(lock, package("ureq", "1.1.0", vuln("RUSTSEC-2099-0001")))),
        )
    )
    found = osv_delta.new_findings(base, head)
    assert [f["id"] for f in found] == ["RUSTSEC-2099-0001"]
    assert found[0]["version"] == "1.1.0"


def test_new_lockfile_brings_all_of_its_findings(tmp_path):
    """A lockfile that did not exist before has no baseline to cancel against."""
    base = osv_delta.load_results(write(tmp_path, "b.json", scan()))
    head = osv_delta.load_results(
        write(
            tmp_path,
            "h.json",
            scan(
                result(
                    "uv.lock",
                    package("pypdf", "6.0.0", vuln("GHSA-aaaa"), ecosystem="PyPI"),
                )
            ),
        )
    )
    assert [f["id"] for f in osv_delta.new_findings(base, head)] == ["GHSA-aaaa"]


def test_same_advisory_on_two_lockfiles_is_two_findings(tmp_path):
    """Two install surfaces are two exposures; collapsing them hides one."""
    base = osv_delta.load_results(write(tmp_path, "b.json", scan()))
    head = osv_delta.load_results(
        write(
            tmp_path,
            "h.json",
            scan(
                result(
                    "a/Cargo.lock",
                    package("foo", "1.0.0", vuln("X-1"), ecosystem="crates.io"),
                ),
                result(
                    "b/Cargo.lock",
                    package("foo", "1.0.0", vuln("X-1"), ecosystem="crates.io"),
                ),
            ),
        )
    )
    found = osv_delta.new_findings(base, head)
    assert len(found) == 2
    assert {f["source"] for f in found} == {"a/Cargo.lock", "b/Cargo.lock"}


def test_alias_added_to_an_existing_advisory_is_not_a_second_finding(tmp_path):
    """OSV lists one issue under GHSA/RUSTSEC/CVE; an alias is not a new vuln."""
    lock = "stt/Cargo.lock"
    base = osv_delta.load_results(
        write(
            tmp_path,
            "b.json",
            scan(
                result(
                    lock,
                    package(
                        "chrono",
                        "0.4.45",
                        vuln("RUSTSEC-2026-0543"),
                        ecosystem="crates.io",
                    ),
                )
            ),
        )
    )
    head = osv_delta.load_results(
        write(
            tmp_path,
            "h.json",
            scan(
                result(
                    lock,
                    package(
                        "chrono",
                        "0.4.45",
                        vuln(
                            "RUSTSEC-2026-0543", aliases=("CVE-2026-99999", "GHSA-zzzz")
                        ),
                        ecosystem="crates.io",
                    ),
                )
            ),
        )
    )
    assert osv_delta.new_findings(base, head) == []


def test_dropping_an_acceptance_reports_the_finding_again(tmp_path):
    """The base scan honoured the exemption, the head scan does not -- blocking.

    Worth pinning because it is easy to mistake for a false positive: nobody
    changed a dependency, only the config. Removing an acceptance is exactly when
    a human should confirm an upgrade decision.
    """
    lock = "surfaces/gui/src-tauri/Cargo.lock"
    accepted = scan()
    unaccepted = scan(
        result(
            lock,
            package("cpal", "0.15.3", vuln("RUSTSEC-2026-0001"), ecosystem="crates.io"),
        )
    )
    base = osv_delta.load_results(write(tmp_path, "b.json", accepted))
    head = osv_delta.load_results(write(tmp_path, "h.json", unaccepted))
    assert [f["id"] for f in osv_delta.new_findings(base, head)] == [
        "RUSTSEC-2026-0001"
    ]


def test_findings_that_disappear_do_not_matter(tmp_path):
    """The diff is one-directional: fixes need no gate."""
    lock = "stt/Cargo.lock"
    base = osv_delta.load_results(
        write(
            tmp_path,
            "b.json",
            scan(result(lock, package("ureq", "1.0.0", vuln("RUSTSEC-1")))),
        )
    )
    head = osv_delta.load_results(
        write(tmp_path, "h.json", scan(result(lock, package("ureq", "2.0.0"))))
    )
    assert osv_delta.new_findings(base, head) == []


# --- path normalisation ------------------------------------------------------


def test_two_checkout_roots_do_not_each_look_new(tmp_path):
    """A scan of ./ resolves to an absolute path, different per checkout root.

    Left un-normalised, every lockfile would be "new" whenever base and head are
    scanned from different directories -- a uniform false positive.
    """
    a = scan(
        result(
            "C:/w/base/surfaces/gui/package-lock.json",
            package("vite", "7.0.0", vuln("GHSA-1")),
        )
    )
    b = scan(
        result(
            "C:/w/head/surfaces/gui/package-lock.json",
            package("vite", "7.0.0", vuln("GHSA-1")),
        )
    )
    base = osv_delta.load_results(write(tmp_path, "b.json", a), "C:/w/base")
    head = osv_delta.load_results(write(tmp_path, "h.json", b), "C:/w/head")
    assert osv_delta.new_findings(base, head) == []
    assert next(iter(head))[0] == "surfaces/gui/package-lock.json"


def test_backslashes_and_leading_dot_slash_normalise(tmp_path):
    a = scan(
        result(
            "C:\\w\\x\\uv.lock",
            package("pypdf", "6.0.0", vuln("GHSA-2"), ecosystem="PyPI"),
        )
    )
    b = scan(
        result("./uv.lock", package("pypdf", "6.0.0", vuln("GHSA-2"), ecosystem="PyPI"))
    )
    base = osv_delta.load_results(write(tmp_path, "b.json", a), "C:\\w\\x")
    head = osv_delta.load_results(write(tmp_path, "h.json", b))
    assert osv_delta.new_findings(base, head) == []


# --- fail closed -------------------------------------------------------------


@pytest.mark.parametrize(
    "payload", [None, "", "   ", "not json", "[]", "{}", '{"foo": 1}']
)
def test_unusable_scan_output_is_an_error_not_an_empty_set(tmp_path, payload):
    """A scan that died must never read as a clean diff.

    osv-scanner exits non-zero for "found vulnerabilities" and "scan broke" alike,
    and a broken scan can leave a JSON file behind. Treating that as zero findings
    would let a push that turns the scanner off pass.
    """
    p = tmp_path / "broken.json"
    if payload is not None:
        p.write_text(payload, encoding="utf-8")
    with pytest.raises(ValueError):
        osv_delta.load_results(p)


def test_missing_file_message_says_scan_did_not_run(tmp_path):
    with pytest.raises(ValueError, match="no scan output"):
        osv_delta.load_results(tmp_path / "absent.json")


def test_main_exits_2_on_broken_input(tmp_path, capsys):
    good = write(tmp_path, "g.json", scan())
    bad = tmp_path / "bad.json"
    bad.write_text("", encoding="utf-8")
    assert osv_delta.main(["--base", str(bad), "--head", str(good)]) == 2
    assert "no new findings" in capsys.readouterr().err


def test_main_exits_1_and_lists_new_findings(tmp_path, capsys):
    base = write(tmp_path, "b.json", scan())
    head = write(
        tmp_path,
        "h.json",
        scan(
            result(
                "stt/Cargo.lock",
                package(
                    "ureq",
                    "1.1.0",
                    vuln("RUSTSEC-2099-0001", severity="HIGH", fixed=("1.1.1",)),
                    ecosystem="crates.io",
                ),
            )
        ),
    )
    out = tmp_path / "found.json"
    rc = osv_delta.main(
        ["--base", str(base), "--head", str(head), "--output-found", str(out)]
    )
    text = capsys.readouterr().out
    assert rc == 1
    assert "RUSTSEC-2099-0001" in text
    assert "1.1.1" in text  # a published fix is surfaced, so the fix is obvious
    assert json.loads(out.read_text(encoding="utf-8"))["new"][0]["version"] == "1.1.0"


def test_main_exits_0_when_only_pre_existing_findings_remain(tmp_path, capsys):
    same = scan(
        result("uv.lock", package("pypdf", "6.0.0", vuln("GHSA-2"), ecosystem="PyPI"))
    )
    base = write(tmp_path, "b.json", same)
    head = write(tmp_path, "h.json", same)
    assert osv_delta.main(["--base", str(base), "--head", str(head)]) == 0
    assert "No finding is new" in capsys.readouterr().out


def test_render_reports_counts_both_sides(tmp_path):
    base = write(
        tmp_path,
        "b.json",
        scan(result("uv.lock", package("a", "1", vuln("X-1"), ecosystem="PyPI"))),
    )
    head = write(
        tmp_path,
        "h.json",
        scan(result("uv.lock", package("a", "1", vuln("X-1"), ecosystem="PyPI"))),
    )
    b = osv_delta.load_results(base)
    h = osv_delta.load_results(head)
    assert "Base scan: 1 finding(s)   Head scan: 1 finding(s)" in osv_delta.render(
        osv_delta.new_findings(b, h), len(b), len(h)
    )
