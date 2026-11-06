"""P2-10 gate — the consent audit ledger records installs and permission decisions."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from coworker.server import SessionManager, create_app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    manager = SessionManager(data_dir=tmp_path / "data")
    yield TestClient(create_app(manager))
    from coworker.connectors.marketplace import installed_plugins, uninstall_plugin

    for p in list(installed_plugins()):
        uninstall_plugin(p["name"])


def _events(client, **params):
    return client.get("/v1/plugins/audit", params=params).json()["events"]


def test_install_and_uninstall_are_recorded(client):
    client.post("/v1/connectors/marketplace/plugins/intercom/install")
    ev = _events(client)[0]
    assert (ev["kind"], ev["id"], ev["action"], ev["decision"]) == (
        "connector",
        "intercom",
        "install",
        "accepted",
    )
    assert ev["capability_set"] == ["connector:intercom"]
    assert ev["risks"] == ["connectors"]
    assert ev["source"] == "api" and isinstance(ev["ts"], float)

    client.post("/v1/connectors/marketplace/plugins/intercom/uninstall")
    assert [e["action"] for e in _events(client, id="intercom")] == [
        "uninstall",
        "install",
    ]


def test_mcp_permission_decisions_are_recorded(client):
    client.post("/v1/mcp", json={"name": "fs", "config": {"command": "echo"}})
    denied = client.patch("/v1/mcp/fs", json={"requires_approval": False})
    assert denied.status_code == 409
    ev = _events(client, id="fs")
    assert [(e["action"], e["decision"]) for e in ev] == [
        ("update", "denied"),
        ("install", "accepted"),
    ]
    assert ev[0]["widened"] == ["requires_approval: true -> false"]

    ok = client.patch("/v1/mcp/fs", json={"requires_approval": False, "confirm": True})
    assert ok.json()["ok"] is True
    ev = _events(client, id="fs")
    assert (ev[0]["action"], ev[0]["decision"]) == ("update", "accepted")

    client.delete("/v1/mcp/fs")
    assert _events(client, id="fs")[0]["action"] == "uninstall"


def test_audit_never_records_env_secrets(client):
    client.post(
        "/v1/mcp",
        json={
            "name": "fs",
            "config": {"command": "echo", "env": {"TOKEN": "s3cret-value"}},
        },
    )
    assert "s3cret-value" not in json.dumps(_events(client))


def test_limit_and_id_filter(client):
    for name in ("a", "b", "c"):
        client.post("/v1/mcp", json={"name": name, "config": {"command": "echo"}})
    assert [e["id"] for e in _events(client, limit=2)] == ["c", "b"]
    assert [e["id"] for e in _events(client)] == ["c", "b", "a"]
    assert [e["id"] for e in _events(client, id="b")] == ["b"]


def test_torn_lines_are_skipped(tmp_path, client):
    client.post("/v1/mcp", json={"name": "fs", "config": {"command": "echo"}})
    path = tmp_path / "state" / "plugin_audit.jsonl"
    with path.open("a", encoding="utf-8") as fh:
        fh.write('{"broken":\n')
    assert len(_events(client)) == 1
