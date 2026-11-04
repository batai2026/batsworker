"""Egress policy tests (P0) — persistent domain allowlist + request_egress tool.

Covers:
- ``EgressPolicy`` add/remove/is_allowed with subdomain matching + persistence.
- The ``request_egress`` tool (approval-gated) writes to the policy store.
- The ``egress`` catalog capability exposes the tool to security personas.
- ``GET/POST/DELETE /v1/egress/policy`` API round-trip.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from coworker.egress import EgressPolicy
from coworker.providers import ModelCapabilities, ProviderClient
from coworker.server import SessionManager, create_app
from coworker.tools.egress import request_egress_tool


class _StubProvider(ProviderClient):
    def complete(self, **kwargs):  # pragma: no cover
        raise NotImplementedError

    def capabilities(self, model):
        return ModelCapabilities()


def _fixture(tmp_path):
    manager = SessionManager(workspace=tmp_path, provider=_StubProvider())
    return TestClient(create_app(manager)), manager


@pytest.fixture
def isolated_policy(tmp_path, monkeypatch):
    """Point the egress policy at a temp dir and reset the permission cache."""
    from coworker import egress as egress_mod

    monkeypatch.setattr(egress_mod, "state_dir", lambda: tmp_path)

    import coworker.permissions as perms

    perms._egress_policy_store = None
    return EgressPolicy()


def test_policy_add_remove_allow(isolated_policy):
    p = isolated_policy
    assert p.add_domain("API.Example.com", "needed")  # normalized to lowercase
    assert p.is_allowed("api.example.com")
    assert p.is_allowed("sub.api.example.com")  # subdomain matches
    assert not p.is_allowed("example.com.evil.net")  # suffix trick does NOT match
    assert not p.is_allowed("other.example.org")
    assert p.remove_domain("api.example.com")
    assert not p.is_allowed("api.example.com")


def test_policy_persists(tmp_path, monkeypatch):
    from coworker import egress as egress_mod

    monkeypatch.setattr(egress_mod, "state_dir", lambda: tmp_path)
    p = EgressPolicy()
    p.add_domain("persist.example.com", "keep")
    p2 = EgressPolicy()  # reload from disk
    assert p2.is_allowed("persist.example.com")


def test_request_egress_tool(isolated_policy):
    tools = {t.__name__: t for t in request_egress_tool(isolated_policy)}
    assert "request_egress" in tools

    res = tools["request_egress"]("data.example.com", "fetch reports")
    assert res["ok"] is True and res["allowed"] is True
    assert res["domain"] == "data.example.com"
    assert isolated_policy.is_allowed("data.example.com")

    # Already allowed → no-op with a clear note.
    res2 = tools["request_egress"]("data.example.com")
    assert "already" in res2["note"]

    # Invalid domain → error.
    bad = tools["request_egress"]("", "x")
    assert bad["ok"] is False


def test_egress_capability(tmp_path):
    from coworker.agents.base import AgentContext
    from coworker.catalog import expand
    from coworker.tools.shell import LocalExecutor

    ctx = AgentContext(
        workspace=tmp_path,
        executor=LocalExecutor(cwd=str(tmp_path)),
        todo=None,
    )
    tools = {t.__name__: t for t in expand(["egress"], ctx)}
    assert "request_egress" in tools


def test_code_capabilities_include_egress(tmp_path):
    """The Code persona's capability set includes request_egress."""
    from coworker.agents.base import AgentContext
    from coworker.agents.code import CODE_CAPABILITIES
    from coworker.catalog import expand
    from coworker.tools.shell import LocalExecutor

    ctx = AgentContext(
        workspace=tmp_path,
        executor=LocalExecutor(cwd=str(tmp_path)),
        todo=None,
    )
    assert "egress" in CODE_CAPABILITIES
    tools = {t.__name__: t for t in expand(CODE_CAPABILITIES, ctx)}
    assert "request_egress" in tools


def test_egress_policy_api(tmp_path, monkeypatch):
    from coworker import egress as egress_mod

    monkeypatch.setattr(egress_mod, "state_dir", lambda: tmp_path)

    import coworker.permissions as perms

    perms._egress_policy_store = None

    client, _ = _fixture(tmp_path)
    assert client.get("/v1/egress/policy").json()["ok"] is True

    r = client.post(
        "/v1/egress/policy",
        json={"domain": "api.acme.com", "reason": "needed for the drill"},
    ).json()
    assert r["ok"] is True

    listed = client.get("/v1/egress/policy").json()["domains"]
    assert any(d["domain"] == "api.acme.com" and "drill" in d["reason"] for d in listed)

    r = client.delete("/v1/egress/policy/api.acme.com").json()
    assert r["ok"] is True
    assert client.get("/v1/egress/policy").json()["domains"] == []


def test_permission_engine_consults_policy(tmp_path, monkeypatch):
    """web_fetch to a policy-approved domain is allowed without a session grant."""
    from coworker import egress as egress_mod
    from coworker.permissions import Mode, PermissionEngine

    monkeypatch.setattr(egress_mod, "state_dir", lambda: tmp_path)

    import coworker.permissions as perms

    perms._egress_policy_store = None
    EgressPolicy().add_domain("policy.example.com", "approved")

    engine = PermissionEngine(workspace_root=tmp_path, mode=Mode.INTERACTIVE)
    decision = engine.evaluate("web_fetch", {"url": "https://policy.example.com/x"})
    assert decision.allowed is True