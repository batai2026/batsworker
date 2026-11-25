"""Fail CI if a requirements-*.lock loses its artifact hashes.

Why this check exists (F-c5e5c266): requirements-dev.lock and
requirements-build.lock are the files CI installs from (`pip install -r ...`),
but until 2026-11-25 they were exported with `uv export --no-hashes`, so pip
verified package names and versions only -- a renamed or replaced tarball on the
index side would install silently. uv.lock (the universal source) kept hashes
the whole time; the *exports* were the gap.

That happened because `--no-hashes` was an explicit flag in the export command.
Nothing failed when it was there, so the next regeneration would quietly drop
hashes again. This script is the tripwire: the lockfile headers now document
the hash-preserving command, and CI runs this check so a future export that
drops them turns the PR red instead of drifting by.

The check is deliberately structural, not a hash recomputation: it verifies the
files *claim* hashes. Verifying the hashes themselves is pip's job at install
time (the points where CI actually does `pip install -r`). Recomputing them
here would be a second re-implementation of uv's resolver output with no
additional security.

Usage:
    python scripts/check_lock_hashes.py [--root .]

Exit 0 when every distribution line in every requirements-*.lock carries at
least one --hash=, 1 when any carries none, 2 on unreadable input.

Edge cases handled:
  * lines inside the header comment are ignored (they look like package lines
    only by accident);
  * `pkg==ver ; marker` -- the environment marker is on the first line, the
    hashes on the continuation lines -- is one requirement, checked as a whole;
  * a blank `pkg==ver` continuation block with no hash lines must still fail.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# A requirement entry starts at the left margin. Continuation lines are indented
# (either the `--hash=...` line or the `# via ...` comment under it). We split
# the file into entries by "a line that starts a new distribution spec".
_HASH_RE = re.compile(r"--hash=")
_ENTRY_START_RE = re.compile(r"^[A-Za-z0-9_.\-\[\]]+==.*(?:\\\\)?$")
_COMMENT_RE = re.compile(r"^\s*#")


def check_file(path: Path) -> list[str]:
    """Return the names of distributions in `path` that carry no hash."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:  # pragma: no cover - trivial wrapper
        raise SystemExit(f"cannot read {path}: {exc}") from exc

    bad: list[str] = []
    current: str | None = None      # name of the entry being accumulated
    current_hashed = False

    def flush() -> None:
        nonlocal current, current_hashed
        if current and not current_hashed:
            bad.append(current)
        current = None
        current_hashed = False

    for raw in text.splitlines():
        # File-header comment block: comments at column 0 (the `# via` notes
        # inside an entry are indented and are continuation, not comments).
        if raw.startswith("#"):
            # A comment ends the previous entry only when a real entry started.
            if current is not None:
                flush()
            continue
        if not raw.strip():
            flush()
            continue
        # A new entry starts at column 0.
        if not raw.startswith((" ", "\t")) and _ENTRY_START_RE.match(raw):
            flush()
            current = raw.split("==", 1)[0].split("[", 1)[0].strip()
            current_hashed = bool(_HASH_RE.search(raw))
            continue
        # Continuation of the current entry.
        if current is not None:
            if _HASH_RE.search(raw):
                current_hashed = True
            continue
        # A line that is neither a comment, blank, entry start, nor continuation
        # is unexpected but not fatal; leaving it alone is safer than guessing.
    flush()
    return bad


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", default=".", help="repository root")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    failures: list[str] = []
    for lock in sorted(root.glob("requirements-*.lock")):
        for pkg in check_file(lock):
            failures.append(f"{lock.name}: {pkg} has no --hash= line")
    for msg in failures:
        print(msg, file=sys.stderr)
    if failures:
        print(f"{len(failures)} distribution(s) without hashes — regenerate the "
              "lockfile WITHOUT --no-hashes (uv exports hashes by default)",
              file=sys.stderr)
        return 1
    print(f"all {len(list(root.glob('requirements-*.lock')))} lockfile(s) carry "
          "hashes on every distribution")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
