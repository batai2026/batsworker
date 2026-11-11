"""Fail the desktop bundle when its sidecar CLI has drifted from the Python source.

`surfaces/gui/src-tauri/src/lib.rs` passes flags to the frozen `openworker-server`
binary. A bundle produced before a CLI change still starts and looks fine, then
dies at launch with "unrecognized arguments" — a failure no other build step
catches, because the bundle is opaque until someone actually runs the app.

Usage:
    python scripts/check_sidecar_cli.py <path-to-bundled-openworker-server>

Exits 0 when the bundle accepts every long option the source defines, 1 on drift
(so `packaging/build_windows.ps1` / `build_dmg.sh` can fail the build), 2 on misuse.
"""

from __future__ import annotations

import re
import subprocess
import sys

# argparse renders long options as `--name` or `--name VALUE`; the help body also
# mentions `--help` in prose, which is harmless (it exists in both sides).
_OPTION_RE = re.compile(r"--[a-z][a-z0-9-]*")


def options_from_help(text: str) -> set[str]:
    """Every long option an argparse-style help screen advertises."""
    return set(_OPTION_RE.findall(text))


def missing_options(source_help: str, bundled_help: str) -> list[str]:
    """Options the Python source defines that the bundled binary does not accept."""
    return sorted(options_from_help(source_help) - options_from_help(bundled_help))


def _help(argv: list[str]) -> str:
    """argparse writes help to stdout, but capture both so a crash still compares."""
    proc = subprocess.run(argv, capture_output=True, text=True, timeout=120)
    return proc.stdout + proc.stderr


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("usage: check_sidecar_cli.py <bundled-openworker-server>", file=sys.stderr)
        return 2

    bundled = args[0]
    source_help = _help([sys.executable, "-m", "coworker.server.run", "--help"])
    bundled_help = _help([bundled, "--help"])

    missing = missing_options(source_help, bundled_help)
    if missing:
        print(
            f"sidecar CLI drift: {bundled} does not accept {', '.join(missing)}",
            file=sys.stderr,
        )
        print("rebuild it (packaging/build_windows.ps1 or build_dmg.sh) before shipping",
              file=sys.stderr)
        return 1

    print(f"sidecar CLI ok: all {len(options_from_help(source_help))} source options accepted")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
