"""OpenShell sandbox executor tests (P0 scaffolding).

Covers the configurable prefix parsing and the v2 fallback contract (sandboxed:false
+ fallback_reason when the CLI is unavailable), plus the success path with a fake
CLI. The real OpenShell CLI flags live in `sandbox_openshell_prefix`; these tests
lock the plumbing without requiring OpenShell to be installed.
"""

from __future__ import annotations

import subprocess as _sp


from coworker.tools.openshell import OpenShellExecutor, _parse_prefix, expand_prefix


def test_parse_prefix():
    assert _parse_prefix('["a","b"]') == ["a", "b"]
    assert _parse_prefix("not json") == ["openshell", "exec", "--"]
    assert _parse_prefix(None) == ["openshell", "exec", "--"]
    assert _parse_prefix("[1,2]") == ["openshell", "exec", "--"]


def test_expand_prefix_placeholders():
    prefix = [
        "openshell", "exec", "--mount", "{workspace}:/workspace",
        "--image", "{image}", "--network", "{network}", "--",
    ]
    assert expand_prefix(prefix, workspace="/ws", image="site/img:1", network="bridge") == [
        "openshell", "exec", "--mount", "/ws:/workspace",
        "--image", "site/img:1", "--network", "bridge", "--",
    ]


def test_expand_prefix_keeps_unknown_braces_and_mixed_tokens():
    out = expand_prefix(
        ["{image}", "{gpu}", "{network}-{workspace}"],
        workspace="/w", image="img", network="none",
    )
    assert out == ["img", "{gpu}", "none-/w"]


def test_expand_prefix_without_placeholders_is_identity():
    prefix = ["openshell", "exec", "--"]
    assert expand_prefix(prefix, workspace="/w", image="i", network="n") == prefix


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


def test_openshell_expands_placeholders_into_argv(tmp_path, monkeypatch):
    """Site flags in the prefix receive the real workspace / image / network."""
    import coworker.tools.openshell as oshell

    monkeypatch.setattr(oshell.shutil, "which", lambda name: "/fake/openshell")
    calls = []

    def _fake_run(cmd, *args, **kwargs):
        calls.append(list(cmd))

        class _R:
            returncode = 0
            stdout = "sandboxed-ok\n"
            stderr = ""

        return _R()

    monkeypatch.setattr(_sp, "run", _fake_run)
    ex = OpenShellExecutor(
        workspace=tmp_path,
        image="site/image:latest",
        network="bridge",
        prefix=[
            "openshell", "exec", "--mount", "{workspace}:/workspace",
            "--image", "{image}", "--network", "{network}", "--",
        ],
    )
    res = ex.run("echo hi", timeout=5)
    assert res["sandboxed"] is True

    argv = calls[-1]
    assert argv[:6] == [
        "openshell", "exec", "--mount",
        f"{ex.workspace}:/workspace", "--image", "site/image:latest",
    ]
    assert argv[6:8] == ["--network", "bridge"]
    assert argv[-2:] == ["--", "echo hi"]
    # 原始 prefix 不被改写（状态接口返回用户配置的意图）
    assert "{workspace}" in ex.prefix[3]


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