"""Container layout self-check -- run INSIDE the built image (CI `docker-image` job).

Why a script and not a pytest: every property below is created by the Dockerfile (uid,
`COWORKER_STATE_DIR`, directory ownership) and is invisible from the host, so it can only
be asserted from inside the image. `docker.yml` builds on tags only, which is how an
unsafe layout could reach GHCR without ever being exercised.

Three assertions, each paired with the failure it prevents:

1. **Baked layout** -- `COWORKER_STATE_DIR` resolves outside the agent's session root
   (`/workspace`), the runtime uid is not 0, and the state dir is writable by it.
   A state dir under `/workspace` hands `read_file` both `secrets.json` (connector and
   MCP credentials) and `sidecar-<port>.token` (authenticates every protected `/v1/*`
   route), and `read_file` runs unapproved in every mode.
2. **Read guard** -- with the state dir forced back inside `/workspace`, `read_file`
   refuses it outright. This is the second layer: it holds for existing deployments that
   still use the old layout, and it is not satisfied by the mere "path escapes the
   session's directories" check or by the file being absent.
3. **Start guard** -- same override, the server refuses to start (fail-closed) rather
   than silently serving the dangerous layout; and it does *not* refuse the baked layout,
   so the check cannot pass by refusing everything.

Usage (inside the image):
    python /check/check_container_state_isolation.py

Exit 0 only if every assertion holds.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

WORKSPACE = Path("/workspace")
REFUSAL = "refusing to read OpenWorker's own state directory"

_failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    suffix = f"  -- {detail}" if detail else ""
    print(f"{'PASS' if ok else 'FAIL'}  {name}{suffix}")
    if not ok:
        _failures.append(name)


def check_baked_layout() -> None:
    from coworker.secrets import state_dir

    state = state_dir().resolve()
    print(f"      COWORKER_STATE_DIR resolves to {state}")
    check(
        "state dir is outside the session root",
        not state.is_relative_to(WORKSPACE),
        f"state={state} workspace={WORKSPACE}",
    )

    uid = os.getuid() if hasattr(os, "getuid") else None
    check("runtime user is not root", uid is not None and uid != 0, f"uid={uid}")

    probe = state / ".write-probe"
    try:
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        error = ""
    except OSError as exc:
        error = str(exc)
    check("state dir is writable by the runtime user", not error, error)


def check_read_guard() -> None:
    """Force the state dir back inside the session root and try to read the secret."""
    forced = WORKSPACE / ".coworker-state"
    forced.mkdir(parents=True, exist_ok=True)
    secret = forced / "secrets.json"
    secret.write_text('{"probe": "placeholder, not a credential"}', encoding="utf-8")

    os.environ["COWORKER_STATE_DIR"] = str(forced)
    from coworker.tools.files import file_tools

    read_file = file_tools(str(WORKSPACE))[0]
    for path in (".coworker-state/secrets.json", str(secret)):
        result = read_file(path)
        error = str(result.get("error", ""))
        check(f"read_file refuses {path}", REFUSAL in error, error or str(result)[:160])


def check_start_guard(baked_state_dir: str | None) -> None:
    from coworker.server.run import _refuse_state_dir_inside_workspace

    # (a) inside the workspace -> must exit, not serve.
    os.environ["COWORKER_STATE_DIR"] = str(WORKSPACE / ".coworker-state")
    try:
        _refuse_state_dir_inside_workspace(str(WORKSPACE))
    except SystemExit as exc:
        check("server refuses to start (state dir inside workspace)", "refusing to start" in str(exc))
    else:
        check("server refuses to start (state dir inside workspace)", False, "no SystemExit")

    # (b) the baked layout -> must NOT trip the guard.
    if baked_state_dir:
        os.environ["COWORKER_STATE_DIR"] = baked_state_dir
    else:
        os.environ.pop("COWORKER_STATE_DIR", None)
    try:
        _refuse_state_dir_inside_workspace(str(WORKSPACE))
    except SystemExit as exc:
        check("baked layout starts normally", False, f"guard tripped: {exc}")
    else:
        check("baked layout starts normally", True)


def main() -> int:
    baked = os.environ.get("COWORKER_STATE_DIR")
    print(f"checking image layout (uid={os.getuid() if hasattr(os, 'getuid') else 'n/a'})")
    check_baked_layout()
    check_read_guard()
    check_start_guard(baked)

    if _failures:
        print(f"\n{len(_failures)} assertion(s) failed: {'; '.join(_failures)}")
        return 1
    print("\nall container isolation assertions passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
