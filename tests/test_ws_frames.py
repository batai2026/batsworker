"""Frame-type matrix for the session WebSocket's inbound dispatch.

`routes/ws.py` answers inbound frames with a long `if/elif` chain. Before that chain can be
turned into a dispatch table, its *decisions* have to be pinned: which frames the socket
accepts, which it rejects with `input_rejected`, and what the rejection says.

The tricky part is that most accepted frames answer nothing — the socket only speaks when a
turn runs or a prompt resolves. So every case sends a **probe** frame after the candidate:
`__probe__` is never a known type, so it always produces an `input_rejected`. Whatever the
socket says BEFORE that rejection belongs to the candidate. That makes "accepted silently"
distinguishable from "never answered", without sleep-and-hope.

Deliberately shallow: each row asserts only the dispatch decision, never the deep effect.
The deep effects (does a turn run, does the mode notice persist) already have their own
tests in test_server.py; duplicating them here would make the matrix brittle for no coverage.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from coworker.providers import AssistantTurn, ModelCapabilities, ProviderClient
from coworker.server import SessionManager, create_app

PROBE = "__probe__"
PROBE_MARK = "Unknown WebSocket message type"


class _Provider(ProviderClient):
    """Answers if a turn happens to start; the matrix never depends on its content."""

    def __init__(self) -> None:
        self.calls = 0

    def complete(self, *, model, messages, tools=None, **settings):
        self.calls += 1
        return AssistantTurn(text="unused", finish_reason="stop")

    def capabilities(self, model):
        return ModelCapabilities()


@pytest.fixture()
def ws(tmp_path):
    """A connected session socket; `send` returns the events the candidate frame produced."""
    manager = SessionManager(workspace=tmp_path, provider=_Provider())
    client = TestClient(create_app(manager))
    with client.websocket_connect("/ws/session/frames") as socket:
        assert socket.receive_json()["type"] == "ready"

        def send(frame, *, raw: bool = False) -> list[dict]:
            if raw:
                socket.send_text(frame)
            else:
                socket.send_json(frame)
            socket.send_json({"type": PROBE})

            seen: list[dict] = []
            for _ in range(8):
                event = socket.receive_json()
                if event["type"] == "input_rejected" and PROBE in event["data"]["error"]:
                    return seen
                seen.append(event)
            raise AssertionError(f"probe rejection never arrived; saw {seen}")

        yield send


def _rejections(events: list[dict]) -> list[dict]:
    return [event for event in events if event["type"] == "input_rejected"]


# Frames the socket must accept. Payloads are the minimal shape each handler needs to take
# its branch; the assertion is only that nothing comes back as `input_rejected`.
ACCEPTED = [
    ("approval", {"decision": "once"}),
    ("directory_response", {"granted": False, "path": "", "writable": False}),
    ("tool_response", {"approved": False}),
    ("plan_response", {"approved": False, "mode": "interactive", "feedback": ""}),
    ("team_response", {"approved": False, "feedback": ""}),
    ("items_response", {"approved": False, "feedback": ""}),
    ("question_response", {"answer": "yes"}),
    ("allow_anyway", {"name": "shell", "arguments": {"command": "ls"}}),
    ("interrupt", {}),
    ("set_mode", {"mode": "interactive"}),
    ("set_model", {"model": "gpt-5.6-sol"}),
]


@pytest.mark.parametrize("kind,payload", ACCEPTED, ids=[kind for kind, _ in ACCEPTED])
def test_known_frame_types_are_dispatched(ws, kind, payload):
    events = ws({"type": kind, **payload})
    assert not _rejections(events), f"{kind} was rejected: {_rejections(events)}"


def test_unknown_frame_type_is_rejected_by_name(ws):
    rejections = _rejections(ws({"type": "definitely-not-a-frame"}))
    assert rejections, "an unknown frame type must be answered"
    error = rejections[0]["data"]["error"]
    assert PROBE_MARK in error
    assert "definitely-not-a-frame" in error


@pytest.mark.parametrize("frame", [[], "text", 42, None], ids=["list", "str", "int", "null"])
def test_non_object_frames_are_rejected(ws, frame):
    rejections = _rejections(ws(frame))
    assert rejections, f"{frame!r} must be answered"
    assert "expected an object" in rejections[0]["data"]["error"]


@pytest.mark.parametrize("frame", [{}, {"type": 1}, {"type": None}])
def test_frames_without_a_string_type_are_rejected(ws, frame):
    rejections = _rejections(ws(frame))
    assert rejections, f"{frame!r} must be answered"
    assert "missing string type" in rejections[0]["data"]["error"]


@pytest.mark.parametrize(
    "kind,payload,fragment",
    [
        ("user_message", {"text": 5}, "text: expected a string"),
        ("user_message", {"text": "hi", "attachments": "nope"}, "attachments: expected a list"),
        ("user_message", {"text": "hi", "attachments": [1]}, "attachment: expected an object"),
        ("user_message", {"text": "hi", "model": 7}, "model: expected a string"),
        ("user_message", {"text": "hi", "skill": 7}, "skill: expected a name"),
        ("allow_anyway", {"name": "", "arguments": {}}, "missing tool name"),
        ("allow_anyway", {"name": "shell", "arguments": "nope"}, "arguments must be an object"),
    ],
)
def test_malformed_payloads_are_rejected_with_the_field_named(ws, kind, payload, fragment):
    rejections = _rejections(ws({"type": kind, **payload}))
    assert rejections, f"{kind} {payload} must be answered"
    assert fragment in rejections[0]["data"]["error"], rejections[0]["data"]["error"]


def test_invalid_json_is_rejected_without_closing_the_socket(ws):
    rejections = _rejections(ws("{not json", raw=True))
    assert rejections, "invalid JSON must be answered"
    assert "expected JSON" in rejections[0]["data"]["error"]
    # The socket survives: a later valid frame is still accepted.
    assert not _rejections(ws({"type": "interrupt"}))


def test_oversized_text_is_rejected_by_the_ingress_cap(ws):
    from coworker.server.routes.ws import MAX_MESSAGE_TEXT_CHARS

    rejections = _rejections(
        ws({"type": "user_message", "text": "x" * (MAX_MESSAGE_TEXT_CHARS + 1)})
    )
    assert rejections, "an oversized message must be answered"
    assert "too long" in rejections[0]["data"]["error"].lower()
