"""Standalone FakeGitHub runner — drive scripts/merge_pr.ps1 by hand without a repo.

    python -m coworker.testing.fake_github --port 8731 --mode success

Then, in another shell:

    pwsh ./scripts/merge_pr.ps1 -Pr 9 -ExpectedSha ffff... -ApiBase http://127.0.0.1:8731

Nothing is merged anywhere: this process serves canned payloads and exits on Ctrl-C.
"""

from __future__ import annotations

import argparse
import time

from .server import HEAD_SHA, FakeGitHub


def main() -> None:
    parser = argparse.ArgumentParser(prog="coworker.testing.fake_github")
    parser.add_argument(
        "--port", type=int, default=8731, help="port to bind (default 8731)"
    )
    parser.add_argument(
        "--host", default="127.0.0.1", help="host to bind (default 127.0.0.1)"
    )
    parser.add_argument(
        "--mode",
        default="success",
        choices=("success", "conflict"),
        help="merge response",
    )
    parser.add_argument(
        "--mergeable-state",
        default="clean",
        help="value GET /pulls/N reports (try 'unstable' to watch the guard refuse)",
    )
    args = parser.parse_args()

    fake = FakeGitHub(
        host=args.host,
        port=args.port,
        mode=args.mode,
        mergeable_state=args.mergeable_state,
    )
    fake.start()
    print("FakeGitHub - local test double for the merge script")
    print(
        f"  listening on {fake.base_url}  mode={args.mode}  state={args.mergeable_state}"
    )
    print(f"  pr={9} head={HEAD_SHA}")
    print()
    print("Drive it:")
    print(f"  pwsh ./scripts/merge_pr.ps1 -Pr 9 -ExpectedSha {HEAD_SHA} \\")
    print(f"       -ApiBase {fake.base_url}")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        fake.shutdown()


if __name__ == "__main__":
    main()
