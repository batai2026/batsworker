#!/usr/bin/env python3
"""Type-check gate: no new type errors, and the error-free set never shrinks.

Why a ratchet instead of `mypy coworker` with a zero-error threshold.

The tree was measured before this gate existed: 241 modules, ~90.7% of code annotated,
398 errors over 71 files -- the bulk of them one systemic Optional-narrowing pattern
(`Value of type "dict[str, Any] | None" is not indexable`, 96 occurrences), not 96
independent bugs. A gate demanding zero today would be red from its first run, and a
permanently red gate is worse than none: people learn to ignore it.

What this gate enforces:

  * **Every file that type-checks today must keep type-checking.** This is the rule
    that earns its keep -- a careless edit in a typed module is caught immediately.
    It is also platform-independent, which matters because the code branches on
    `sys.platform` (`os.getpgid`/`killpg` on POSIX, `CREATE_NEW_PROCESS_GROUP`/`WinDLL`
    on Windows) and the measurements genuinely differ: 398 errors on Windows vs 399 on
    Linux. A gate on the raw count would be red on one of the two platforms no matter
    what.
  * **Total error count may not grow** (per platform).
  * **No file may exceed its own baseline count**, though it may improve.

Adding a new module is free as long as it type-checks, and it is protected from then
on: the enforcement sets are recomputed from the per-platform records on every
`--update`.

Baseline layout (`typecheck_baseline.json`):

    {
      "platforms": {
        "win32": {"total_errors": 398,
                  "clean_files": ["coworker/a.py", ...],
                  "dirty_files": {"coworker/b.py": 4, ...}},
        "linux": {...}
      }
    }

Each platform's numbers are that platform's OWN measurement, so `--update` on one
platform can lower a count without erasing what another platform last saw.

**Enforcement is per platform**: a run compares against the record for the platform it
measured, so a win32 run is never judged by a count that only exists on linux. That is
what makes a platform difference invisible without opening a hole -- a file that this
platform recorded as clean must still be clean *here*, even if another platform is
known to have an error in it. Only when the platform being measured has no record at
all (a first measurement, or a platform nobody has recorded yet) does the run fall back
to the merged view:

    clean_files = intersection over platforms    # must stay clean everywhere
    dirty_files = per-file max over platforms    # no record here to compare against
    total_errors = per platform

Reporting follows the same rule as enforcement: "newly clean" and "improved" are
relative to this platform's record, because those are exactly what `--update` would
rewrite. Reporting against the merged view instead produced messages like "run --update
to lock that in" for a file that was simply clean here and dirty on linux -- advice
that, if followed, would have added the file to *this* platform's clean set and then
failed the next time it was checked on linux.

An earlier single-level layout could not express that, and it silently broke three
things: an improvement could never be locked in (`max(old, new)` kept `old`), a newly
added clean module was never added to the enforced set (intersection with a stale
list), and deleting a module read as a regression. `tests/test_typecheck_gate.py`
pins all three.

Usage:
    python tools/typecheck.py            # check against the baseline
    python tools/typecheck.py --update   # after a deliberate improvement
    python tools/typecheck.py --report   # print per-file status, do not fail
    python tools/typecheck.py --platform linux   # measure as if on that platform
"""

from __future__ import annotations

import argparse
import json
import platform
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASELINE = Path(__file__).resolve().with_name("typecheck_baseline.json")
TARGET = "coworker"

ERROR_RE = re.compile(r"^(?P<file>[^:]+\.py):\d+: error:", re.MULTILINE)


def normalise(name: str) -> str:
    """Forward slashes, so the baseline is identical on Windows and Linux."""
    return name.replace("\\", "/")


def run_mypy(extra: list[str], platform_flag: str | None) -> tuple[dict[str, int], str]:
    """Run mypy and return ({file: error_count}, raw output)."""
    cmd = [sys.executable, "-m", "mypy", "--no-incremental", "--show-error-codes"]
    if platform_flag:
        cmd += ["--platform", platform_flag]
    cmd += extra
    cmd.append(TARGET)
    proc = subprocess.run(
        cmd, cwd=str(ROOT), capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    output = (proc.stdout or "") + (proc.stderr or "")

    counts: dict[str, int] = {}
    for match in ERROR_RE.finditer(output):
        name = normalise(match.group("file"))
        counts[name] = counts.get(name, 0) + 1

    if "Cannot find implementation or library stub" in output and not counts:
        raise SystemExit(
            "mypy could not resolve the project's own packages, so this run measured "
            "nothing.\nInstall the project (`pip install -e .`) and run the gate with "
            "that interpreter, or pass\n  --mypy-arg=--python-executable --mypy-arg=<venv>/bin/python\n"
            + output[-2000:]
        )
    return counts, output


def load_baseline() -> dict:
    """Load the baseline, normalising paths and tolerating the legacy flat layout."""
    if not BASELINE.exists():
        raise SystemExit("missing %s -- generate it with --update" % BASELINE.name)
    data = json.loads(BASELINE.read_text(encoding="utf-8"))

    if "platforms" in data:
        per_platform = data["platforms"]
    else:
        # legacy single-level layout: it described the union/max across platforms, so
        # attribute it to every platform it recorded and let the derived sets sort it out
        totals = data.get("total_errors")
        totals = totals if isinstance(totals, dict) else {"win32": totals or 0}
        per_platform = {
            plat: {
                "total_errors": total,
                "clean_files": data.get("clean_files", []),
                "dirty_files": data.get("dirty_files", {}),
            }
            for plat, total in totals.items()
        }

    for record in per_platform.values():
        record["clean_files"] = [normalise(f) for f in record.get("clean_files", [])]
        record["dirty_files"] = {normalise(k): v for k, v in record.get("dirty_files", {}).items()}
    return {**data, "platforms": per_platform}


def enforced_clean(per_platform: dict) -> set[str]:
    """A file is enforced clean only if it is clean on every platform measured.

    A platform whose clean set is EMPTY is still a measurement, and must count as one:
    skipping it silently narrowed the intersection and made every file another platform
    recorded as clean look clean here too. Only a platform with no record at all is
    skipped.
    """
    sets = [set(r.get("clean_files", [])) for r in per_platform.values()]
    if not sets:
        return set()
    return set.intersection(*sets)


def enforced_dirty(per_platform: dict) -> dict[str, int]:
    """Per-file ceiling: the highest count any platform recorded, so a platform
    difference can never read as a regression."""
    out: dict[str, int] = {}
    for record in per_platform.values():
        for name, n in record.get("dirty_files", {}).items():
            out[name] = max(out.get(name, 0), n)
    return out


def platform_view(per_platform: dict, this_platform: str) -> tuple[set[str], dict[str, int]]:
    """The (clean, dirty) record to compare THIS run against.

    Prefer this platform's own measurement: a win32 run must not be excused by an
    error that only exists on linux, and must not be told to "lock in" a file that is
    merely clean here. Fall back to the merged view only when this platform has no
    record at all -- a first measurement cannot invent a per-platform baseline.
    """
    record = per_platform.get(this_platform)
    if record:
        return set(record.get("clean_files", [])), dict(record.get("dirty_files", {}))
    return enforced_clean(per_platform), enforced_dirty(per_platform)


def basis_note(per_platform: dict, this_platform: str) -> str | None:
    """Say which record was compared against, when it is not this platform's own."""
    if this_platform in per_platform:
        return None
    others = ", ".join(sorted(per_platform)) or "none"
    return ("no %s record in the baseline yet -- compared against the merged view "
            "(records present: %s)" % (this_platform, others))


def all_targets() -> list[str]:
    return sorted(normalise(p.relative_to(ROOT).as_posix())
                  for p in (ROOT / TARGET).rglob("*.py"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="mypy ratchet for the coworker package")
    parser.add_argument("--update", action="store_true",
                        help="rewrite the baseline from the current state")
    parser.add_argument("--report", action="store_true",
                        help="print status but always exit 0")
    parser.add_argument("--platform", choices=["win32", "linux", "darwin"],
                        help="measure as if on this platform (default: this machine)")
    parser.add_argument("--mypy-arg", action="append", default=[],
                        help="extra argument passed through to mypy (repeatable)")
    args = parser.parse_args(argv)

    this_platform = args.platform or ("win32" if platform.system() == "Windows" else "linux")
    counts, output = run_mypy(args.mypy_arg, args.platform)
    total = sum(counts.values())
    baseline = load_baseline()
    per_platform = baseline["platforms"]

    targets = set(all_targets())
    clean_baseline, dirty_ceiling = platform_view(per_platform, this_platform)
    merged_dirty = enforced_dirty(per_platform)
    clean_now = {f for f in targets if f not in counts}

    # a module that no longer exists is not a type regression
    lost = sorted((clean_baseline & targets) - clean_now)
    gained = sorted(clean_now - clean_baseline)

    # a file may improve, never regress
    worse = {f: (n, dirty_ceiling.get(f, 0))
             for f, n in counts.items()
             if n > dirty_ceiling.get(f, 0)}
    better = {f: (dirty_ceiling[f], n)
              for f, n in counts.items()
              if f in dirty_ceiling and n < dirty_ceiling[f]}

    allowed_total = per_platform.get(this_platform, {}).get("total_errors")
    print("type gate (%s): %d errors in %d files" % (this_platform, total, len(counts)))
    if allowed_total is not None:
        print("  baseline for %s: %d errors, %d error-free files"
              % (this_platform, allowed_total, len(clean_baseline)))
    print("  error-free files now: %d" % len(clean_now))
    note = basis_note(per_platform, this_platform)
    if note:
        print("  note: %s" % note)

    if gained:
        print("\nnewly clean (%d) -- lower the baseline with --update:" % len(gained))
        for f in gained:
            if merged_dirty.get(f):
                print("  + %-56s (clean here, %d error(s) on another platform -- "
                      "stays out of the merged clean set)" % (f, merged_dirty[f]))
            else:
                print("  + %s" % f)
    if better:
        print("\nimproved (%d):" % len(better))
        for f, (was, now) in sorted(better.items()):
            print("  ~ %-56s %d -> %d" % (f, was, now))
            if merged_dirty.get(f, 0) > now:
                print("    (another platform still records %d there -- the merged "
                      "ceiling stays)" % merged_dirty[f])

    if args.report:
        print("\n--report: not failing the build")
        return 0

    if args.update:
        # Record THIS platform's own numbers. Storing the raw measurement (not a max
        # over the old value) is what lets an improvement be locked in; the max is
        # re-derived below, only for the cross-platform ceiling.
        per_platform[this_platform] = {
            "total_errors": total,
            "clean_files": sorted(clean_now),
            "dirty_files": dict(sorted(counts.items())),
        }
        for record in per_platform.values():
            # drop entries for modules that no longer exist
            record["clean_files"] = [f for f in record["clean_files"] if f in targets]
            record["dirty_files"] = {f: n for f, n in record["dirty_files"].items() if f in targets}

        new = {
            "_comment": baseline.get("_comment", []),
            "platforms": dict(sorted(per_platform.items())),
        }
        BASELINE.write_text(json.dumps(new, indent=2) + "\n", encoding="utf-8")
        print("\nbaseline updated for %s -> %s" % (this_platform, BASELINE.name))
        print("  enforced error-free files (clean on every platform): %d"
              % len(enforced_clean(per_platform)))
        print("  per-file ceiling (max over platforms): %d files"
              % len(enforced_dirty(per_platform)))
        return 0

    failed = False

    if lost:
        failed = True
        print("\nFAIL: these files type-checked before and no longer do:")
        for f in lost:
            print("  - %-56s %d error(s)" % (f, counts.get(f, 0)))

    if worse:
        failed = True
        print("\nFAIL: more errors than the baseline allows:")
        for f, (now, allowed) in sorted(worse.items()):
            print("  ! %-56s %d (baseline %d)" % (f, now, allowed))

    if allowed_total is not None and total > allowed_total:
        failed = True
        print("\nFAIL: %d new type error(s) (baseline %d, found %d)"
              % (total - allowed_total, allowed_total, total))

    if failed:
        print("\nFix the errors. Only for a deliberate, explained trade-off, run"
              "\n  python tools/typecheck.py --update")
        print("\n--- mypy said ---")
        print(output[-6000:])
        return 1

    print("\nOK: no new type errors.")
    if better or gained:
        print("Tip: this branch improved things too -- run --update to lock that in.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
