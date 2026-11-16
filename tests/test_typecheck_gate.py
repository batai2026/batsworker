"""Tests for the ratchet gate's own logic (tools/typecheck.py).

The gate decides whether every branch is green, so its logic deserves the same
scrutiny as the code it guards. A gate that silently fails to enforce what its
docstring promises is worse than no gate, because the team believes it is covered.

Every test here corresponds to a failure mode that the first version of the gate
actually had: `--update` could not lock in an improvement, a newly added clean module
was never protected, and deleting a module read as a regression.

mypy is stubbed out: these tests are about baseline arithmetic, not about mypy.
"""
from __future__ import annotations

import importlib.util
import json
import pathlib
from typing import Any

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]


@pytest.fixture()
def gate(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """Load tools/typecheck.py with mypy and the filesystem stubbed out."""
    spec = importlib.util.spec_from_file_location("typecheck_gate", ROOT / "tools" / "typecheck.py")
    assert spec and spec.loader, "tools/typecheck.py must exist"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    monkeypatch.setattr(mod, "BASELINE", tmp_path / "baseline.json")
    files: list[str] = []
    counts: dict[str, int] = {}

    monkeypatch.setattr(mod, "all_targets", lambda: list(files))
    monkeypatch.setattr(mod, "run_mypy", lambda extra, plat: (dict(counts), ""))

    mod._files = files  # type: ignore[attr-defined]
    mod._counts = counts  # type: ignore[attr-defined]
    return mod


def gate_main(mod: Any, *args: str) -> int:
    """Run the gate as if on win32.

    Every test here writes a win32 baseline, and the gate picks its platform from
    `platform.system()`. Without an explicit flag the same assertions pass on a Windows
    developer machine and fail on the Linux CI runner, where "this platform" became
    linux, no record existed for it, and the run silently compared against the merged
    view instead. Pinning the platform is what makes these tests platform-independent,
    which is the property the gate exists to have.
    """
    return mod.main([*args, "--platform", "win32"])


def write_baseline(mod: Any, clean: list[str], dirty: dict[str, int],
                   totals: dict[str, int] | None = None, platform: str = "win32") -> None:
    """Write a single-platform baseline in the current (per-platform) layout."""
    mod.BASELINE.write_text(json.dumps({
        "platforms": {
            platform: {
                "total_errors": (totals or {"win32": sum(dirty.values())}).get(platform, 0),
                "clean_files": clean,
                "dirty_files": dirty,
            }
        }
    }), encoding="utf-8")


def read_baseline(mod: Any) -> dict:
    return json.loads(mod.BASELINE.read_text(encoding="utf-8"))


def win32_record(mod: Any) -> dict:
    return read_baseline(mod)["platforms"]["win32"]


# -- the three enforcement rules -------------------------------------------------

def test_regression_in_clean_file_fails(gate: Any) -> None:
    gate._files[:] = ["a.py", "b.py"]
    write_baseline(gate, clean=["a.py", "b.py"], dirty={})
    gate._counts.update({"a.py": 1})
    assert gate_main(gate) == 1


def test_more_errors_in_dirty_file_fails(gate: Any) -> None:
    gate._files[:] = ["a.py"]
    write_baseline(gate, clean=[], dirty={"a.py": 3})
    gate._counts.update({"a.py": 4})
    assert gate_main(gate) == 1


def test_total_growth_fails(gate: Any) -> None:
    gate._files[:] = ["a.py", "b.py"]
    write_baseline(gate, clean=[], dirty={"a.py": 2, "b.py": 2}, totals={"win32": 4})
    gate._counts.update({"a.py": 3, "b.py": 2})
    assert gate_main(gate) == 1


def test_unchanged_tree_passes(gate: Any) -> None:
    gate._files[:] = ["a.py", "b.py"]
    write_baseline(gate, clean=["b.py"], dirty={"a.py": 2})
    gate._counts.update({"a.py": 2})
    assert gate_main(gate) == 0


# -- regressions that the first version of the gate had --------------------------

def test_new_clean_file_is_adopted_by_update(gate: Any) -> None:
    """A newly added module that type-checks must end up protected.

    The docstring promises "adding a new module is free as long as it type-checks,
    and it is protected from then on". The first version merged the clean set with an
    intersection against a stale list, so the new module never entered it.
    """
    gate._files[:] = ["a.py", "new.py"]
    write_baseline(gate, clean=["a.py"], dirty={})
    gate._counts.clear()
    assert gate_main(gate, "--update") == 0

    assert "new.py" in win32_record(gate)["clean_files"], "new clean module not protected"

    # now break it: the gate must notice
    gate._counts.update({"new.py": 1})
    assert gate_main(gate) == 1, "error in a newly added module went unnoticed"


def test_improvement_can_be_locked_in(gate: Any) -> None:
    """`--update` must record the measurement, not max(old, new).

    The first version kept the old (higher) number, so the gate told you to run
    `--update` to lock an improvement in and then silently disbelieved it.
    """
    gate._files[:] = ["a.py"]
    write_baseline(gate, clean=[], dirty={"a.py": 5})
    gate._counts.update({"a.py": 2})
    assert gate_main(gate, "--update") == 0
    assert win32_record(gate)["dirty_files"]["a.py"] == 2


def test_deleting_a_clean_module_does_not_fail(gate: Any) -> None:
    """Removing a file is not a type regression."""
    gate._files[:] = ["a.py"]
    write_baseline(gate, clean=["a.py", "gone.py"], dirty={})
    assert gate_main(gate) == 0, "deleting a module was reported as a type regression"


def test_update_prunes_deleted_module_from_baseline(gate: Any) -> None:
    gate._files[:] = ["a.py"]
    write_baseline(gate, clean=["a.py", "gone.py"], dirty={"gone.py": 3})
    gate._counts.clear()
    assert gate_main(gate, "--update") == 0
    assert "gone.py" not in win32_record(gate)["clean_files"]
    assert "gone.py" not in win32_record(gate)["dirty_files"]


# -- cross-platform derivation --------------------------------------------------

def test_platform_difference_is_not_a_regression(gate: Any) -> None:
    """A file counted higher on another platform must not fail this one.

    Measured reality: 398 errors on win32, 399 on linux, differing only in
    `sys.platform`-gated code. Recording the max as the ceiling is what keeps the
    gate green on both.
    """
    gate._files[:] = ["run.py", "shell.py"]
    gate.BASELINE.write_text(json.dumps({"platforms": {
        "win32": {"total_errors": 5, "clean_files": ["shell.py"],
                  "dirty_files": {"run.py": 0}},
        "linux": {"total_errors": 6, "clean_files": [],
                  "dirty_files": {"run.py": 1, "shell.py": 1}},
    }}), encoding="utf-8")

    # measuring win32: run.py has no error here, shell.py is clean here
    gate._counts.update({"shell.py": 0})
    gate._counts.clear()
    assert gate_main(gate) == 0, "a linux-only error failed the win32 run"

    # but an extra error on THIS platform still fails
    gate._counts.update({"run.py": 2})
    assert gate_main(gate) == 1


def test_update_on_one_platform_preserves_the_other(gate: Any) -> None:
    """Lowering a count on win32 must not erase what linux last measured."""
    gate._files[:] = ["a.py"]
    gate.BASELINE.write_text(json.dumps({"platforms": {
        "win32": {"total_errors": 5, "clean_files": [], "dirty_files": {"a.py": 5}},
        "linux": {"total_errors": 6, "clean_files": [], "dirty_files": {"a.py": 6}},
    }}), encoding="utf-8")

    gate._counts.update({"a.py": 3})
    assert gate_main(gate, "--update") == 0

    data = read_baseline(gate)["platforms"]
    assert data["win32"]["dirty_files"]["a.py"] == 3, "win32 improvement not recorded"
    assert data["linux"]["dirty_files"]["a.py"] == 6, "linux record was clobbered"
    # the derived ceiling is still the max
    assert gate.enforced_dirty(data)["a.py"] == 6


def test_legacy_flat_baseline_is_still_read(gate: Any) -> None:
    """The first layout must keep working, so an old checkout does not hard-fail."""
    gate._files[:] = ["a.py", "b.py"]
    gate.BASELINE.write_text(json.dumps({
        "total_errors": {"win32": 3},
        "clean_files": ["b.py"],
        "dirty_files": {"a.py": 3},
    }), encoding="utf-8")

    gate._counts.update({"a.py": 3})
    assert gate_main(gate) == 0


def test_this_platform_record_decides_not_the_merged_view(gate: Any) -> None:
    """Enforcement must use this platform's own record, not the cross-platform merge.

    Real shape of the bug: `run.py` is clean on win32 and has one error on linux, so
    the merged clean set excludes it. Comparing a win32 run against that merge let a
    win32-only error in `run.py` pass unnoticed -- the gate was blind exactly on the
    files the platforms disagree about.
    """
    gate._files[:] = ["run.py"]
    gate.BASELINE.write_text(json.dumps({"platforms": {
        "win32": {"total_errors": 0, "clean_files": ["run.py"], "dirty_files": {}},
        "linux": {"total_errors": 1, "clean_files": [], "dirty_files": {"run.py": 1}},
    }}), encoding="utf-8")

    gate._counts.clear()
    assert gate_main(gate) == 0

    gate._counts.update({"run.py": 1})
    assert gate_main(gate) == 1, "a win32 error went unnoticed because linux disagrees"


def test_update_without_a_record_for_this_platform_uses_the_merge(gate: Any) -> None:
    """A platform with no record of its own cannot be judged per-platform yet.

    It must fall back to the merged view (never invent an empty baseline) and say so,
    rather than reporting every clean file as "newly clean".
    """
    gate._files[:] = ["a.py"]
    gate.BASELINE.write_text(json.dumps({"platforms": {
        "linux": {"total_errors": 1, "clean_files": [], "dirty_files": {"a.py": 1}},
    }}), encoding="utf-8")

    gate._counts.update({"a.py": 1})
    assert gate_main(gate) == 0, "fell through to an empty baseline and failed falsely"
    assert gate.basis_note({"linux": {}}, "win32") is not None


def test_reporting_does_not_claim_a_cross_platform_gap_as_an_improvement(
        gate: Any, capsys: pytest.CaptureFixture[str]) -> None:
    """"run --update to lock that in" must not be offered for a clean-on-here file.

    Following that advice would add the file to this platform's clean set and then fail
    the next check on the platform that has the error. The message must instead name
    why it stays out of the merged view.
    """
    gate._files[:] = ["run.py"]
    gate.BASELINE.write_text(json.dumps({"platforms": {
        "win32": {"total_errors": 0, "clean_files": [], "dirty_files": {}},
        "linux": {"total_errors": 1, "clean_files": [], "dirty_files": {"run.py": 1}},
    }}), encoding="utf-8")

    gate._counts.clear()
    assert gate_main(gate) == 0
    out = capsys.readouterr().out
    assert "another platform" in out, "cross-platform gap reported as a plain improvement"


def test_an_empty_clean_set_is_still_a_measurement(gate: Any) -> None:
    """A platform that recorded zero clean files must still constrain the merge.

    `if r.get("clean_files")` skipped empty records, so the merged clean set became
    just the platforms that happened to have one. With linux recording none, every file
    win32 recorded as clean looked clean on both -- which is how a run reported a file
    as "clean" while the same file was dirty on the other platform.
    """
    merged = gate.enforced_clean({
        "win32": {"clean_files": ["a.py", "b.py", "dirty.py"]},
        "linux": {"clean_files": []},
    })
    assert merged == set(), "an empty clean set was ignored instead of intersecting"


def test_unresolvable_project_is_a_loud_error(gate: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """An empty measurement must not be mistaken for a clean tree."""
    gate._files[:] = ["a.py"]
    write_baseline(gate, clean=[], dirty={"a.py": 3})
    monkeypatch.setattr(gate, "run_mypy", lambda extra, plat: (_ for _ in ()).throw(
        SystemExit("Cannot find implementation or library stub")))
    with pytest.raises(SystemExit):
        gate_main(gate)
