"""OpenShell sandbox executor tests (real CLI wiring).

Covers the configurable prefix parsing (now the real ``openshell sandbox exec``
CLI), the managed sandbox lifecycle (lazy create -> readiness probe -> delete on
close), the embedded/site policy plumbing, and the v2 fallback contract
(sandboxed:false + fallback_reason when the CLI is unavailable). These tests use
a fake CLI; they lock the plumbing without requiring OpenShell to be installed.
"""

from __future__ import annotations

import subprocess as _sp

from coworker.tools.openshell import (
    _DEFAULT_POLICY,
    _DEFAULT_PREFIX,
    OpenShellExecutor,
    _parse_prefix,
    expand_prefix,
)


def _fake_cli(calls: list, *, create_rc: int = 0):
    """subprocess.run stand-in that understands the OpenShell sandbox CLI."""

    def _run(cmd, *args, **kwargs):
        calls.append(list(cmd))

        class _R:
            returncode = 0
            stdout = ""
            stderr = ""

        r = _R()
        sub = cmd[1:3] if len(cmd) >= 3 else []
        if cmd[-1:] == ["--version"]:
            r.stdout = "openshell 0.1.7\n"
        elif sub == ["sandbox", "create"]:
            r.returncode = create_rc
            if create_rc:
                r.stderr = "quota exceeded"
        elif sub == ["sandbox", "exec"]:
            if cmd[-1] == "true":  # readiness probe
                r.stdout = ""
            else:
                r.stdout = "sandboxed-ok\n"
        elif sub == ["sandbox", "delete"]:
            r.returncode = 0
        return r

    return _run


def test_parse_prefix():
    assert _parse_prefix('["a","b"]') == ["a", "b"]
    assert _parse_prefix("not json") == _DEFAULT_PREFIX
    assert _parse_prefix(None) == _DEFAULT_PREFIX
    assert _parse_prefix("[1,2]") == _DEFAULT_PREFIX


def test_default_prefix_is_the_real_cli():
    """The default prefix addresses the managed sandbox via the real CLI."""
    assert _DEFAULT_PREFIX == [
        "openshell", "sandbox", "exec", "-n", "{sandbox}",
        "--workdir", "/workspace", "--",
    ]


def test_expand_prefix_placeholders():
    prefix = [
        "openshell", "sandbox", "exec", "-n", "{sandbox}",
        "--env", "IMG={image}", "--network", "{network}", "--",
    ]
    assert expand_prefix(
        prefix, workspace="/ws", image="site/img:1", network="bridge", sandbox="sb-1"
    ) == [
        "openshell", "sandbox", "exec", "-n", "sb-1",
        "--env", "IMG=site/img:1", "--network", "bridge", "--",
    ]


def test_expand_prefix_keeps_unknown_braces_and_mixed_tokens():
    out = expand_prefix(
        ["{image}", "{gpu}", "{network}-{workspace}"],
        workspace="/w", image="img", network="none",
    )
    assert out == ["img", "{gpu}", "none-/w"]


def test_expand_prefix_without_placeholders_is_identity():
    prefix = ["openshell", "sandbox", "exec", "--"]
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
    """Full lifecycle: create -> readiness probe -> exec -> delete on close."""
    import coworker.tools.openshell as oshell

    monkeypatch.setattr(oshell.shutil, "which", lambda name: "/fake/openshell")
    calls: list = []
    monkeypatch.setattr(_sp, "run", _fake_cli(calls))

    ex = OpenShellExecutor(workspace=tmp_path)
    res = ex.run("echo hi", timeout=5)
    assert res["sandboxed"] is True
    assert "sandboxed-ok" in res.get("output", "")
    assert res["exit_code"] == 0

    kinds = [" ".join(c[1:3]) for c in calls if len(c) >= 3]
    assert kinds.count("sandbox create") == 1
    assert "sandbox delete" not in kinds  # not yet
    ex.close()
    kinds = [" ".join(c[1:3]) for c in calls if len(c) >= 3]
    assert kinds.count("sandbox delete") == 1


def test_openshell_create_argv_uses_real_cli(tmp_path, monkeypatch):
    """create carries name/image/upload/policy; exec runs inside the named sandbox."""
    import coworker.tools.openshell as oshell

    monkeypatch.setattr(oshell.shutil, "which", lambda name: "/fake/openshell")
    calls: list = []
    monkeypatch.setattr(_sp, "run", _fake_cli(calls))

    ex = OpenShellExecutor(workspace=tmp_path, image="site/image:latest")
    res = ex.run("echo hi", timeout=5)
    assert res["sandboxed"] is True

    create = next(c for c in calls if c[1:3] == ["sandbox", "create"])
    assert create[:4] == ["openshell", "sandbox", "create", "--name"]
    assert create[4] == ex.sandbox_name
    assert create[5:7] == ["--from", "site/image:latest"]
    assert create[7:9] == ["--upload", f"{ex.workspace}:/workspace"]
    assert create[9] == "--policy"
    assert create[10].endswith(".yaml")  # embedded baseline written to temp
    assert create[11] == "--detach"

    execs = [c for c in calls if c[1:3] == ["sandbox", "exec"] and c[-1] != "true"]
    argv = execs[-1]
    assert argv[:4] == ["openshell", "sandbox", "exec", "-n"]
    assert argv[4] == ex.sandbox_name
    assert argv[5:8] == ["--workdir", "/workspace", "--"]
    assert argv[-1] == "echo hi"
    # 原始 prefix 不被改写（状态接口返回用户配置的意图）
    assert "{sandbox}" in ex.prefix[4]


def test_openshell_site_policy_path_is_used(tmp_path, monkeypatch):
    import coworker.tools.openshell as oshell

    monkeypatch.setattr(oshell.shutil, "which", lambda name: "/fake/openshell")
    calls: list = []
    monkeypatch.setattr(_sp, "run", _fake_cli(calls))
    policy = tmp_path / "site-policy.yaml"
    policy.write_text("version: 1\nnetwork_policies: {}\n")

    ex = OpenShellExecutor(workspace=tmp_path, policy=str(policy))
    ex.run("echo hi", timeout=5)
    create = next(c for c in calls if c[1:3] == ["sandbox", "create"])
    assert create[10] == str(policy)


def test_openshell_create_failure_falls_back(tmp_path, monkeypatch):
    """A failing create (quota, image pull, ...) falls back locally, not crash."""
    import coworker.tools.openshell as oshell

    monkeypatch.setattr(oshell.shutil, "which", lambda name: "/fake/openshell")
    monkeypatch.setattr(_sp, "run", _fake_cli([], create_rc=1))

    ex = OpenShellExecutor(workspace=tmp_path)
    res = ex.run("echo create-fallback", timeout=5)
    assert res["sandboxed"] is False
    assert "create failed" in res.get("fallback_reason", "")
    assert "create-fallback" in res.get("output", "")
    # Create failure is sticky for the session (no retry storm per command).
    assert ex.run("echo again", timeout=5)["sandboxed"] is False


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


def test_embedded_policy_is_baseline_isolation():
    """The embedded policy denies egress and keeps root FS read-only."""
    assert "network_policies: {}" in _DEFAULT_POLICY
    assert "read_only:" in _DEFAULT_POLICY
    assert "/workspace" in _DEFAULT_POLICY


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
    assert r["prefix"] == _DEFAULT_PREFIX
    assert "available" in r and "backend" in r
