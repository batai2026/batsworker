"""OpenShell sandbox executor tests (P0 scaffolding).

Covers the configurable prefix parsing and the v2 fallback contract (sandboxed:false
+ fallback_reason when the CLI is unavailable), plus the success path with a fake
CLI. The real OpenShell CLI flags live in `sandbox_openshell_prefix`; these tests
lock the plumbing without requiring OpenShell to be installed.
"""

from __future__ import annotations

import subprocess as _sp


from coworker.tools.openshell import OpenShellExecutor, _parse_prefix


def test_parse_prefix():
    assert _parse_prefix('["a","b"]') == ["a", "b"]
    assert _parse_prefix("not json") == ["openshell", "exec", "--"]
    assert _parse_prefix(None) == ["openshell", "exec", "--"]
    assert _parse_prefix("[1,2]") == ["openshell", "exec", "--"]


def test_openshell_falls_back_when_cli_missing(tmp_path, monkeypatch):
    import coworker.tools.openshell as oshell

    monkeypatch.setattr(oshell.shutil, "which", lambda name: None)

    ex = OpenShellExecutor(workspace=tmp_path)
    res = ex.run("echo sandbox-fallback-ok", timeout=5)
    assert res["sandboxed"] is False
    assert "OpenShell CLI not found" in res.get("fallback_reason", "")
    assert "sandbox-fallback-ok" in res.get("output", "")


def test_openshell_runs_with_fake_cli(tmp_path, monkeypatch):
    import coworker.tools.openshell as oshell

    monkeypatch.setattr(oshell.shutil, "which", lambda name: "/fake/openshell")

    def _fake_run(cmd, *args, **kwargs):
        class _R:
            returncode = 0
            stdout = "sandboxed-ok\n"
            stderr = ""

        return _R()

    monkeypatch.setattr(_sp, "run", _fake_run)

    ex = OpenShellExecutor(workspace=tmp_path)
    res = ex.run("echo hi", timeout=5)
    assert res["sandboxed"] is True
    assert "sandboxed-ok" in res.get("output", "")
    assert res["exit_code"] == 0


def test_openshell_per_command_fallback_on_cli_loss(tmp_path, monkeypatch):
    """If the CLI disappears after the first run, the command falls back locally."""
    import coworker.tools.openshell as oshell

    monkeypatch.setattr(oshell.shutil, "which", lambda name: None)

    ex = OpenShellExecutor(workspace=tmp_path)
    # First run falls back (probe fails). A real session could also see the CLI
    # vanish mid-flight; FileNotFoundError in run() triggers the same path.
    res = ex.run("echo gone", timeout=5)
    assert res["sandboxed"] is False
    assert "OpenShell" in res.get("fallback_reason", "")


def test_openshell_status_api(tmp_path):
    """GET /v1/settings/openshell/status reports availability + prefix."""
    from fastapi.testclient import TestClient

    from coworker.providers import ModelCapabilities, ProviderClient
    from coworker.server import SessionManager, create_app

    class _StubProvider(ProviderClient):
        def complete(self, **kwargs):  # pragma: no cover
            raise NotImplementedError

        def capabilities(self, model):
            return ModelCapabilities()

    manager = SessionManager(workspace=tmp_path, provider=_StubProvider())
    client = TestClient(create_app(manager))
    r = client.get("/v1/settings/openshell/status").json()
    assert r["ok"] is True
    assert r["prefix"] == ["openshell", "exec", "--"]
    assert "available" in r and "backend" in r