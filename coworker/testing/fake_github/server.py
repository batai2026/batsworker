"""The server behind :mod:`coworker.testing.fake_github`.

Answers the four GitHub endpoints ``scripts/merge_pr.ps1`` touches -- ``GET /pulls/N``,
``PUT /pulls/N/merge``, ``GET /git/ref/heads/main``, and ``GET /pulls/N`` again
afterwards -- with payloads shaped exactly like GitHub's.

Two properties of the real API are worth reproducing faithfully, because the merge
script's verdict depends on them:

* a successful merge answers HTTP 200 with BOTH ``"merged": true`` and
  ``"message": "Pull Request successfully merged"``, so a client that treats ``message``
  as an error signal reports success as failure;
* a head that moved between review and merge answers 409, and a conflicted PR answers
  405 with ``"message": "Pull Request is not mergeable"``.

Every request is recorded, including the ``Authorization`` header, so a test can assert
on what the client actually sent rather than on what it claims to send.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

HEAD_SHA = "f" * 40
MERGED_SHA = "d" * 40
PR_NUMBER = 9


@dataclass
class State:
    """What the fake has been told to do, and what it has seen."""

    mode: str = "success"  # success | conflict
    mergeable_state: str = "clean"
    head_sha: str = HEAD_SHA
    merged: bool = False
    requests: list[tuple[str, str]] = field(default_factory=list)
    # Every Authorization header actually received -- lets a test prove the real token
    # never reached this server, instead of trusting the client to say so.
    auth_headers: list[str | None] = field(default_factory=list)

    def seen(self, method: str) -> list[str]:
        """Paths called with `method`, in order."""
        return [path for m, path in self.requests if m == method]

    def _pull(self) -> dict[str, Any]:
        if self.merged:
            return {
                "number": PR_NUMBER,
                "state": "closed",
                "merged": True,
                "merged_at": "2026-11-10T12:00:00Z",
                "mergeable": None,
                "mergeable_state": "unknown",
                "head": {"sha": self.head_sha},
            }
        return {
            "number": PR_NUMBER,
            "state": "open",
            "merged": False,
            "merged_at": None,
            "mergeable": True,
            "mergeable_state": self.mergeable_state,
            "head": {"sha": self.head_sha},
        }


def _handler_for(state: State) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def _send(self, code: int, payload: dict[str, Any]) -> None:
            body = json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            state.requests.append(("GET", self.path))
            state.auth_headers.append(self.headers.get("Authorization"))
            if self.path.startswith("/pulls/"):
                self._send(200, state._pull())
            elif self.path == "/git/ref/heads/main":
                # Not the head sha, so a test can tell a real read apart from a guess.
                self._send(
                    200, {"object": {"sha": MERGED_SHA if state.merged else "e" * 40}}
                )
            else:
                self._send(404, {"message": "Not Found"})

        def do_PUT(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length).decode() if length else ""
            sent = json.loads(raw) if raw else {}
            state.requests.append(("PUT", self.path))
            state.auth_headers.append(self.headers.get("Authorization"))
            if sent.get("sha") != state.head_sha:
                self._send(
                    409,
                    {
                        "message": "Head branch was modified. Review and try the merge again."
                    },
                )
                return
            if state.mode == "conflict":
                self._send(
                    405, {"message": "Pull Request is not mergeable", "errors": []}
                )
                return
            state.merged = True
            self._send(
                200,
                {
                    "sha": MERGED_SHA,
                    "merged": True,
                    "message": "Pull Request successfully merged",
                },
            )

        def log_message(self, *args: Any) -> None:
            pass  # keep test output to the assertions

    return Handler


class FakeGitHub:
    """A fake GitHub, on an ephemeral port unless one is given.

    with FakeGitHub(mergeable_state="unstable") as fake:
        ...run the script against fake.base_url...
        assert fake.state.seen("PUT") == []
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 0,
        *,
        mode: str = "success",
        mergeable_state: str = "clean",
        head_sha: str = HEAD_SHA,
    ) -> None:
        self.state = State(
            mode=mode, mergeable_state=mergeable_state, head_sha=head_sha
        )
        self._server = ThreadingHTTPServer((host, port), _handler_for(self.state))
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self.base_url = f"http://{host}:{self._server.server_address[1]}"

    # -- lifecycle ---------------------------------------------------------------

    def start(self) -> str:
        self._thread.start()
        return self.base_url

    def shutdown(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)

    def __enter__(self) -> FakeGitHub:
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.shutdown()
