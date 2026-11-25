"""The webview diagnostics channel must be safe to expose to a webview.

Three kinds of test here, in increasing order of how easily they are got wrong:

1. happy path -- an entry arrives and lands on disk;
2. abuse       -- oversized, over-count, unknown fields, junk types;
3. failure     -- a broken state dir must not fail the request.

The bounded-file tests matter most: this endpoint is writable by whatever the
webview runs, and its output is a file a human will hand to a maintainer.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from coworker import ui_log
from coworker.server import SessionManager, create_app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    manager = SessionManager(data_dir=tmp_path / "data")
    return TestClient(create_app(manager))


def _log_path(tmp_path):
    return tmp_path / "state" / ui_log.LOG_FILENAME


def test_an_entry_is_stored_and_readable(client, tmp_path):
    r = client.post(
        "/v1/ui/log",
        json={
            "events": [
                {
                    "command": "pick_folder",
                    "message": 'invoke("pick_folder") failed; treating it as unavailable',
                    "level": "error",
                    "origin": "http://127.0.0.1:51234",
                }
            ]
        },
    )
    assert r.status_code == 200
    assert r.json() == {"ok": True, "kept": 1}

    rows = client.get("/v1/ui/log").json()["events"]
    assert len(rows) == 1
    assert rows[0]["command"] == "pick_folder"
    assert "not allowed" in rows[0]["message"] or "failed" in rows[0]["message"]
    assert isinstance(rows[0]["ts"], float)


def test_unknown_fields_are_dropped_not_stored(client, tmp_path):
    client.post(
        "/v1/ui/log",
        json={
            "events": [
                {
                    "command": "pick_folder",
                    # A webview could try to land anything in this file; the token
                    # especially must never be storable.
                    "token": "super-secret-sidecar-token",
                    "cookie": "session=abc",
                    "arbitrary": {"nested": "payload"},
                }
            ]
        },
    )
    raw = _log_path(tmp_path).read_text(encoding="utf-8")
    assert "super-secret-sidecar-token" not in raw
    assert "session=abc" not in raw
    assert "arbitrary" not in raw
    assert "pick_folder" in raw


def test_long_strings_are_truncated(client, tmp_path):
    huge = "A" * 200_000
    client.post("/v1/ui/log", json={"events": [{"command": "x", "message": huge}]})
    row = json.loads(_log_path(tmp_path).read_text(encoding="utf-8").splitlines()[0])
    assert len(row["message"]) == ui_log.MAX_TEXT


def test_batch_is_capped(client, tmp_path):
    events = [{"command": f"cmd{i}", "message": "boom"} for i in range(200)]
    r = client.post("/v1/ui/log", json={"events": events})
    assert r.json()["kept"] == ui_log.MAX_BATCH
    lines = _log_path(tmp_path).read_text(encoding="utf-8").splitlines()
    assert len(lines) == ui_log.MAX_BATCH


def test_oversized_entry_is_dropped(client, tmp_path):
    # A single entry over MAX_ENTRY_BYTES is refused even though each field is
    # individually under its own cap.
    big = {"command": "c" * ui_log.MAX_TEXT, "stack": "s" * ui_log.MAX_STACK,
           "message": "m" * ui_log.MAX_TEXT, "origin": "o" * ui_log.MAX_TEXT,
           "platform": "p" * ui_log.MAX_TEXT, "app_version": "v" * ui_log.MAX_TEXT}
    client.post("/v1/ui/log", json={"events": [big]})
    size = len(json.dumps({**big, "ts": 1.0}).encode("utf-8"))
    if size > ui_log.MAX_ENTRY_BYTES:
        assert not _log_path(tmp_path).exists() or _log_path(tmp_path).read_text(
            encoding="utf-8"
        ).strip() == ""


def test_junk_payloads_never_500(client):
    for body in [
        {"events": "not-a-list"},
        {"events": [None, 42, "string", [], {}]},
        {"no-events-key": True},
        {},
    ]:
        r = client.post("/v1/ui/log", json=body)
        assert r.status_code == 200
        assert r.json()["ok"] is True


def test_file_is_bounded_and_rotated(client, tmp_path):
    # Push several times the cap through; the file must never exceed MAX_BYTES
    # and the rotated companion must have been used. `stack` is used because it
    # is the largest per-field allowance, so a few rounds cross 1 MiB without
    # needing thousands of requests.
    filler = "x" * 3500
    events = [{"command": "flood", "stack": filler} for _ in range(ui_log.MAX_BATCH)]
    per_round = len(json.dumps({**events[0], "ts": 1.0}).encode("utf-8")) * ui_log.MAX_BATCH
    rounds = (ui_log.MAX_BYTES * 3) // per_round + 2
    for _ in range(rounds):
        client.post("/v1/ui/log", json={"events": events})

    path = _log_path(tmp_path)
    assert path.stat().st_size <= ui_log.MAX_BYTES
    rotated = path.with_suffix(path.suffix + ".1")
    assert rotated.exists(), "rotation never happened despite flooding"
    assert rotated.stat().st_size <= ui_log.MAX_BYTES


# -- export: the file a user hands to a maintainer ---------------------------


def test_export_is_a_downloadable_text_file(client):
    r = client.get("/v1/ui/log/export")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/plain")
    disposition = r.headers["content-disposition"]
    assert "attachment" in disposition
    # A dated name, so two exports don't silently overwrite each other.
    assert "openworker-diagnostics-" in disposition and ".txt" in disposition


def test_export_contains_the_event_and_its_context(client):
    client.post(
        "/v1/ui/log",
        json={
            "events": [
                {
                    "command": "pick_folder",
                    "message": "pick_folder is not allowed by ACL",
                    "level": "error",
                    "origin": "http://127.0.0.1:5000",
                }
            ]
        },
    )
    body = client.get("/v1/ui/log/export").text
    assert "pick_folder" in body
    assert "not allowed by ACL" in body
    # The reader needs to know which build and box produced this.
    assert "App version:" in body
    assert "Platform:" in body


def test_export_never_contains_the_sidecar_token(tmp_path, monkeypatch):
    """The file is meant to be pasted into a public issue tracker verbatim.

    The token is a bearer credential for the local sidecar; it must not reach a
    file whose entire purpose is to leave the machine.
    """
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("COWORKER_API_TOKEN", "expected-token")
    manager = SessionManager(data_dir=tmp_path / "data")
    client = TestClient(create_app(manager))
    headers = {"X-OpenWorker-Token": "expected-token"}

    client.post(
        "/v1/ui/log",
        json={"events": [{"command": "pick_folder", "message": "boom"}]},
        headers=headers,
    )
    body = client.get("/v1/ui/log/export", headers=headers).text
    assert "expected-token" not in body
    assert "pick_folder" in body  # the useful part did survive


def test_export_of_an_empty_log_says_so(client):
    body = client.get("/v1/ui/log/export").text
    assert "No webview failures were recorded" in body


def test_export_is_readable_when_the_log_is_corrupt(tmp_path, monkeypatch):
    """A half-written line must not turn the export into a 500.

    This is the file a user reaches for precisely when something is broken, so
    it has to degrade rather than fail.
    """
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    path = tmp_path / "state" / ui_log.LOG_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"command": "ok", "ts": 1.0}\n{"command": "torn"', encoding="utf-8")

    from coworker.server import SessionManager as Manager

    client = TestClient(create_app(Manager(data_dir=tmp_path / "data")))
    r = client.get("/v1/ui/log/export")
    assert r.status_code == 200
    assert "ok" in r.text


def test_broken_state_dir_does_not_fail_the_request(client, tmp_path, monkeypatch):
    # Point state at a path whose parent is a FILE: mkdir must fail.
    blocker = tmp_path / "state"
    blocker.write_text("not a directory", encoding="utf-8")
    monkeypatch.setenv("COWORKER_STATE_DIR", str(blocker))
    r = client.post("/v1/ui/log", json={"events": [{"command": "pick_folder"}]})
    assert r.status_code == 200
    assert r.json() == {"ok": True, "kept": 0}


def test_torn_line_does_not_void_the_ledger(tmp_path, monkeypatch):
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    path = tmp_path / "state" / ui_log.LOG_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"command": "first", "ts": 1.0}) + "\n" + '{"command": "torn"\n',
        encoding="utf-8",
    )
    rows = ui_log.read()
    assert [r["command"] for r in rows] == ["first"]


def test_get_reports_the_file_path_to_attach(client, tmp_path):
    # "Attach the log" is only actionable if the user is told which file, and it
    # lives in app-data where nobody would look on their own.
    body = client.get("/v1/ui/log").json()
    assert body["path"].endswith(ui_log.LOG_FILENAME)
    assert str(tmp_path) in body["path"]


def test_endpoint_requires_the_sidecar_token(tmp_path, monkeypatch):
    """The log is not a tokenless path: it must sit behind the same auth as the rest."""
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("COWORKER_API_TOKEN", "expected-token")
    manager = SessionManager(data_dir=tmp_path / "data")
    client = TestClient(create_app(manager))

    assert client.post("/v1/ui/log", json={"events": [{"command": "x"}]}).status_code == 401
    ok = client.post(
        "/v1/ui/log",
        json={"events": [{"command": "x"}]},
        headers={"X-OpenWorker-Token": "expected-token"},
    )
    assert ok.status_code == 200
