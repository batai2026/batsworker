#!/usr/bin/env python3
"""Type-check gate: no new type errors, and the error-free set never shrinks.

Why a ratchet instead of `mypy coworker` with a zero-error threshold.

The tree was measured before this gate existed: 241 modules, ~90.7% of code annotated,
436 errors over 82 files -- the bulk of them one systemic Optional-narrowing pattern
(`Value of type "dict[str, Any] | None" is not indexable`, 96 occurrences), not 96
independent bugs. A gate demanding zero today would be red from its first run, and a
permanently red gate is worse than none: people learn to ignore it.

What this gate enforces:

  * **Every file that type-checks today must keep type-checking.** This is the rule
    that earns its keep -- a careless edit in a typed module is caught immediately.
    It is also platform-independent, which matters because the code branches on
    `sys.platform` (`os.getpgid`/`killpg` on POSIX, `CREATE_NEW_PROCESS_GROUP`/`WinDLL`
    on Windows) and the measurements genuinely differ: 436 errors on Windows vs 437 on
    Linux, from 3 win-only and 4 linux-only errors. A gate on the raw count would be
    red on one of the two platforms no matter what.
  * **Total error count may not grow** (per platform, recorded in the baseline).
  * **No file may exceed its own baseline count**, though it may improve.

Adding a new module is free as long as it type-checks. The clean set therefore grows
by default.

Baseline layout (`typecheck_baseline.json`):

    {
      "clean_files": [...],              # platform-independent
      "dirty_files": {"path": n, ...},   # platform-independent: max over platforms
      "total_errors": {"win32": 436, "linux": 437}
    }
`dirty_files` holds the HIGHER of the two platform counts, so the check only fires on a
real regression, never on a platform difference.

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
    if not BASELINE.exists():
        raise SystemExit("missing %s -- generate it with --update" % BASELINE.name)
    data = json.loads(BASELINE.read_text(encoding="utf-8"))
    data["clean_files"] = [normalise(f) for f in data.get("clean_files", [])]
    data["dirty_files"] = {normalise(k): v for k, v in data.get("dirty_files", {}).items()}
    if not isinstance(data.get("total_errors"), dict):
        # tolerate the original single-number form
        data["total_errors"] = {"win32": data.get("total_errors", 0)}
    return data


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

    targets = all_targets()
    clean_baseline = set(baseline["clean_files"])
    clean_now = [f for f in targets if f not in counts]
    lost = sorted(clean_baseline - set(clean_now))
    gained = sorted(set(clean_now) - clean_baseline)

    # a file may improve, never regress
    worse = {f: (n, baseline["dirty_files"].get(f, 0))
             for f, n in counts.items()
             if n > baseline["dirty_files"].get(f, 0)}
    better = {f: (baseline["dirty_files"][f], n)
              for f, n in counts.items()
              if f in baseline["dirty_files"] and n < baseline["dirty_files"][f]}

    allowed_total = baseline["total_errors"].get(this_platform)
    print("type gate (%s): %d errors in %d files" % (this_platform, total, len(counts)))
    if allowed_total is not None:
        print("  baseline for %s: %d errors, %d error-free files"
              % (this_platform, allowed_total, len(clean_baseline)))
    print("  error-free files now: %d" % len(clean_now))

    if gained:
        print("\nnewly clean (%d) -- lower the baseline with --update:" % len(gained))
        for f in gained:
            print("  + %s" % f)
    if better:
        print("\nimproved (%d):" % len(better))
        for f, (was, now) in sorted(better.items()):
            print("  ~ %-56s %d -> %d" % (f, was, now))

    if args.report:
        print("\n--report: not failing the build")
        return 0

    if args.update:
        # clean_files = intersection over every platform measured so far: a file is only
        # enforced as "must stay clean" if it is clean everywhere, otherwise the gate
        # would fire on a platform difference rather than on a real regression.
        merged_clean = sorted(clean_baseline & set(clean_now)) if clean_baseline \
            else sorted(clean_now)

        # dirty_files = per-file max over platforms, pruned when a file is clean everywhere
        merged = dict(baseline["dirty_files"])
        for f, n in counts.items():
            merged[f] = max(n, merged.get(f, 0))
        for f in list(merged):
            if f in merged_clean:
                merged.pop(f)

        totals = dict(baseline["total_errors"])
        totals[this_platform] = total

        new = {
            "_comment": baseline.get("_comment", []),
            "total_errors": totals,
            "clean_files": merged_clean,
            "dirty_files": dict(sorted(merged.items(), key=lambda kv: (-kv[1], kv[0]))),
        }
        BASELINE.write_text(json.dumps(new, indent=2) + "\n", encoding="utf-8")
        print("\nbaseline updated for %s -> %s" % (this_platform, BASELINE.name))
        print("  clean_files (enforced): %d" % len(merged_clean))
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
