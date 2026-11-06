"""P2-7 gate — connector plugins and MCP servers install through the unified pipeline.

Every install response now carries the same ``consent`` shape (adapter → consent_summary),
and connector uninstall delegates to ``lifecycle.uninstall``.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from coworker.server import SessionManager, create_app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    manager = SessionManager(data_dir=tmp_path / "data")
    yield TestClient(create_app(manager))
    # Installs mutate the global in-memory connector registry; leave it pristine.
    from coworker.connectors.marketplace import installed_plugins, uninstall_plugin

    for p in list(installed_plugins()):
        uninstall_plugin(p["name"])


def test_connector_install_returns_unified_consent(client):
    res = client.post("/v1/connectors/marketplace/plugins/intercom/install").json()
    assert res["ok"] is True
    assert res["plugin"]["name"] == "intercom"
    consent = res["consent"]
    assert consent["kind"] == "connector" and consent["id"] == "intercom"
    assert consent["capability_set"] == ["connector:intercom"]
    assert consent["risks"] == ["connectors"]
    assert consent["permissions"] == {
        "auth": consent["permissions"]["auth"],
        "two_way": False,
        "mcp_backed": False,
    }
    assert consent["requires_consent"] is True


def test_connector_uninstall_stays_compatible(client):
    client.post("/v1/connectors/marketplace/plugins/intercom/install")
    res = client.post("/v1/connectors/marketplace/plugins/intercom/uninstall").json()
    assert res == {"ok": True, "kind": "connector", "id": "intercom", "name": "intercom"}


def test_mcp_add_returns_unified_consent(client):
    res = client.post(
        "/v1/mcp",
        json={"name": "fs", "config": {"command": "echo", "args": ["x"]}},
    ).json()
    assert res["ok"] is True and res["name"] == "fs"
    consent = res["consent"]
    assert consent["kind"] == "mcp" and consent["id"] == "fs"
    assert consent["capability_set"] == ["mcp:fs"]
    assert consent["risks"] == ["mcp"]
    assert consent["permissions"]["transport"] == "stdio"
    assert consent["permissions"]["requires_approval"] is True


def test_mcp_patch_returns_consent_with_stable_diff(client):
    client.post("/v1/mcp", json={"name": "fs", "config": {"command": "echo"}})
    res = client.patch("/v1/mcp/fs", json={"enabled": False}).json()
    assert res["ok"] is True
    consent = res["consent"]
    # Capability of an MCP server is the server itself → an update never grows it.
    assert consent["diff"] == {"grew": [], "shrank": [], "same": ["mcp:fs"]}
    assert consent["requires_consent"] is False
    assert client.get("/v1/mcp").json()["servers"][0]["enabled"] is False

    assert client.patch("/v1/mcp/ghost", json={"enabled": False}).json() == {
        "ok": False,
        "name": "ghost",
    }


def test_mcp_patch_widening_requires_confirm(client):
    client.post("/v1/mcp", json={"name": "fs", "config": {"command": "echo"}})
    res = client.patch("/v1/mcp/fs", json={"requires_approval": False})
    assert res.status_code == 409
    body = res.json()
    assert body["ok"] is False and body["requires_consent"] is True
    assert body["consent"]["permission_diff"]["widened"] == [
        "requires_approval: true -> false"
    ]
    cfg = client.get("/v1/mcp").json()["servers"][0]["config"]
    assert cfg.get("requires_approval", True) is True  # not applied

    ok = client.patch("/v1/mcp/fs", json={"requires_approval": False, "confirm": True})
    assert ok.status_code == 200 and ok.json()["ok"] is True
    cfg = client.get("/v1/mcp").json()["servers"][0]["config"]
    assert cfg["requires_approval"] is False
    assert "confirm" not in cfg  # request control never lands in mcp.json


def test_mcp_patch_tool_surface_widening(client):
    client.post(
        "/v1/mcp",
        json={"name": "fs", "config": {"command": "echo", "include_tools": ["read"]}},
    )
    added = client.patch("/v1/mcp/fs", json={"include_tools": ["read", "write"]})
    assert added.status_code == 409
    assert added.json()["consent"]["permission_diff"]["widened"] == [
        "include_tools added: write"
    ]
    everything = client.patch("/v1/mcp/fs", json={"include_tools": None})
    assert everything.status_code == 409
    assert everything.json()["consent"]["permission_diff"]["widened"] == [
        "include_tools: allowlist -> all"
    ]


def test_mcp_patch_narrowing_and_benign_fields_apply(client):
    client.post("/v1/mcp", json={"name": "fs", "config": {"command": "echo"}})
    # All tools → explicit allowlist is a narrowing: applies without confirmation.
    narrow = client.patch("/v1/mcp/fs", json={"include_tools": ["read"]})
    assert narrow.status_code == 200 and narrow.json()["ok"] is True
    assert narrow.json()["consent"]["requires_consent"] is False
    # GUI's two PATCH fields (enabled / auth) never trigger the gate.
    assert client.patch("/v1/mcp/fs", json={"enabled": False}).json()["ok"] is True
    assert client.patch("/v1/mcp/fs", json={"auth": "oauth"}).json()["ok"] is True
    # Adding exclusions narrows; removing one widens again.
    assert client.patch("/v1/mcp/fs", json={"exclude_tools": ["drop"]}).json()["ok"] is True
    widened = client.patch("/v1/mcp/fs", json={"exclude_tools": []})
    assert widened.status_code == 409
    assert widened.json()["consent"]["permission_diff"]["widened"] == [
        "exclude_tools removed: drop"
    ]


def test_permission_diff_flags_connector_two_way():
    from coworker.plugins.consent import permission_diff
    from coworker.plugins.manifest import KIND_CONNECTOR, PluginManifest

    old = PluginManifest(
        kind=KIND_CONNECTOR, id="x", name="x", permissions={"two_way": False}
    )
    new = PluginManifest(
        kind=KIND_CONNECTOR, id="x", name="x", permissions={"two_way": True}
    )
    assert permission_diff(old, new) == {"widened": ["two_way: false -> true"]}
    assert permission_diff(new, old) == {"widened": []}  # narrowing is free

