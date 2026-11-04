"""Marketplace tests — automation template marketplace + connector/plugin ecosystem.

Covers:
- ``GET /v1/automations/templates`` — catalog shape.
- ``POST /v1/automations/templates/{key}/instantiate`` — one-click creation,
  required-channel validation, unknown-template errors, consent grants.
- ``GET /v1/connectors/marketplace`` — curated MCP servers + plugin cards.
- ``POST /v1/connectors/marketplace/plugins/{name}/install`` / ``uninstall`` —
  plugin installs into the connector registry, persists to the state dir, and
  shows up in ``/v1/connectors``; uninstall removes it again.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from coworker.connectors.descriptors import get_descriptor
from coworker.connectors.marketplace import installed_plugins
from coworker.providers import ModelCapabilities, ProviderClient
from coworker.server import SessionManager, create_app


@pytest.fixture(autouse=True)
def _restore_plugin_registry():
    """Marketplace plugin installs mutate the GLOBAL in-memory connector registry.
    Uninstall anything this test file installed so later test modules (which assert
    over the full available-connector set) see the pristine shipped catalog."""
    yield
    from coworker.connectors.marketplace import uninstall_plugin

    for p in list(installed_plugins()):
        uninstall_plugin(p["name"])


class ScriptedProvider(ProviderClient):
    def __init__(self, turns=None):
        self._turns = list(turns or [])
        self.seen: list[list[dict]] = []

    def complete(self, *, model, messages, tools=None, **settings):
        self.seen.append(messages)
        return self._turns.pop(0)

    def capabilities(self, model):
        return ModelCapabilities()


def _client(tmp_path, turns=None):
    provider = ScriptedProvider(turns)
    manager = SessionManager(workspace=tmp_path, provider=provider)
    return TestClient(create_app(manager)), manager, provider


# -- automation template marketplace ------------------------------------------------


def test_template_catalog_shape(tmp_path):
    client, _m, _p = _client(tmp_path)
    body = client.get("/v1/automations/templates").json()
    assert body["templates"]
    keys = [t["key"] for t in body["templates"]]
    assert "github" in keys and "brief" in keys and "bug-triage" in keys
    tmpl = body["templates"][0]
    for field in (
        "key", "title", "blurb", "cadence", "category",
        "conns", "needs_repo", "needs_channel", "consent", "deliver",
        "day", "time",
    ):
        assert field in tmpl


def test_template_instantiate_happy_path(tmp_path):
    client, _m, _p = _client(tmp_path)
    res = client.post(
        "/v1/automations/templates/github/instantiate",
        json={"repo": "acme/worker", "channel": "#dev", "day": "mon", "time": "09:00"},
    ).json()
    assert res["ok"] is True
    task = res["task"]
    assert task["title"] == "GitHub digest"
    assert "acme/worker" in task["instructions"]
    assert "#dev" in task["instructions"]
    assert task["schedule_raw"]["cron"] == "0 9 * * 1"
    # Write recipe → the standing send_message grant to the chosen channel.
    assert any(
        e["tool"] == "send_message" and e["target"] == "#dev"
        for e in task["always_allowed"]
    )


def test_template_instantiate_validation(tmp_path):
    client, _m, _p = _client(tmp_path)
    # Channel required for channel templates.
    res = client.post(
        "/v1/automations/templates/github/instantiate", json={"repo": "acme"}
    ).json()
    assert res["ok"] is False and "channel" in res["error"].lower()
    # Unknown template.
    res = client.post(
        "/v1/automations/templates/ghost/instantiate", json={}
    ).json()
    assert res["ok"] is False and "unknown template" in res["error"]
    # News template needs no channel/repo — works with empty config.
    res = client.post(
        "/v1/automations/templates/news/instantiate", json={}
    ).json()
    assert res["ok"] is True


def test_template_created_automation_lists(tmp_path):
    client, _m, _p = _client(tmp_path)
    client.post(
        "/v1/automations/templates/pipeline/instantiate",
        json={"channel": "#sales", "day": "weekdays", "time": "08:30"},
    )
    tasks = client.get("/v1/automations").json()["tasks"]
    assert len(tasks) == 1
    assert tasks[0]["title"] == "Pipeline digest"


# -- connector/plugin marketplace -------------------------------------------------


def test_marketplace_catalog_shape(tmp_path):
    client, _m, _p = _client(tmp_path)
    body = client.get("/v1/connectors/marketplace").json()
    assert body["mcp"] and body["mcp"][0]["name"] == "granola"
    names = [p["name"] for p in body["plugins"]]
    assert "intercom" in names and "webflow" in names
    assert all(p["installed"] is False for p in body["plugins"])


def test_plugin_install_then_uninstall(tmp_path):
    client, _m, _p = _client(tmp_path)
    # Install → connector appears in the registry and persists to the state dir.
    res = client.post(
        "/v1/connectors/marketplace/plugins/intercom/install"
    ).json()
    assert res["ok"] is True
    assert get_descriptor("intercom") is not None
    names = [c["name"] for c in client.get("/v1/connectors").json()["connectors"]]
    assert "intercom" in names
    assert any(p["name"] == "intercom" for p in installed_plugins())

    # Marketplace reflects the installed state.
    body = client.get("/v1/connectors/marketplace").json()
    intercom = next(p for p in body["plugins"] if p["name"] == "intercom")
    assert intercom["installed"] is True

    # Uninstall → removed from the registry and the persisted dir.
    res = client.post(
        "/v1/connectors/marketplace/plugins/intercom/uninstall"
    ).json()
    assert res["ok"] is True
    assert get_descriptor("intercom") is None
    names = [c["name"] for c in client.get("/v1/connectors").json()["connectors"]]
    assert "intercom" not in names
    assert all(p["name"] != "intercom" for p in installed_plugins())


def test_plugin_install_collision_and_unknown(tmp_path):
    client, _m, _p = _client(tmp_path)
    # Unknown plugin.
    res = client.post(
        "/v1/connectors/marketplace/plugins/ghost/install"
    ).json()
    assert res["ok"] is False and "unknown plugin" in res["error"]
    # A shipped connector name is not a marketplace plugin → refused.
    res = client.post(
        "/v1/connectors/marketplace/plugins/slack/install"
    ).json()
    assert res["ok"] is False and res["error"]


def test_installed_plugin_registers_connector_details(tmp_path):
    """Installed plugin descriptor carries its fields so the connector is connectable."""
    client, _m, _p = _client(tmp_path)
    client.post("/v1/connectors/marketplace/plugins/webflow/install")
    connector = next(
        c for c in client.get("/v1/connectors").json()["connectors"] if c["name"] == "webflow"
    )
    assert connector["available"] is True
    assert connector["auth"] == "api_token"
    assert any(f["key"] == "api_token" for f in connector["fields"])
    # A plugin without a validator still connects (credentials stored, no API call).
    res = client.post(
        "/v1/connectors/webflow/connect",
        json={"fields": {"api_token": "wf_test"}},
    ).json()
    assert res["ok"] is True