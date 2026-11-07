"""Security workflow tests — editable threat model + finding closure loop.

Covers:
- ``GET/PUT /v1/security/threat-model`` — default content, save, read back.
- ``GET/POST /v1/security/findings`` + PATCH/DELETE — finding tracking lifecycle.
- The ``security`` catalog capability registers the workflow tools for security
  personas, and the tools operate on the same store.
"""

from __future__ import annotations

import sys

import pytest
from fastapi.testclient import TestClient

from coworker.providers import ModelCapabilities, ProviderClient
from coworker.security import SecurityStore
from coworker.server import SessionManager, create_app
from coworker.tools.security import security_tools


class _StubProvider(ProviderClient):
    def complete(self, **kwargs):  # pragma: no cover
        raise NotImplementedError

    def capabilities(self, model):
        return ModelCapabilities()


def _fixture(tmp_path):
    manager = SessionManager(workspace=tmp_path, provider=_StubProvider())
    return TestClient(create_app(manager)), manager


# -- API: threat model -------------------------------------------------------------


def test_threat_model_default_and_save(tmp_path):
    client, _ = _fixture(tmp_path)
    body = client.get("/v1/security/threat-model").json()
    assert "Threat Model" in body["content"]
    assert "Reachability rules" in body["content"]

    new_model = "# My Model\n\n## In scope\n- Only payment paths.\n"
    res = client.put("/v1/security/threat-model", json={"content": new_model}).json()
    assert res["ok"] is True
    assert client.get("/v1/security/threat-model").json()["content"] == new_model


def test_threat_model_reveal(tmp_path):
    client, _ = _fixture(tmp_path)
    res = client.post("/v1/security/threat-model/reveal", json={}).json()
    assert res["ok"] is True
    assert res["path"].endswith("security")


# -- API: finding tracker ----------------------------------------------------------


def test_finding_lifecycle(tmp_path):
    client, _ = _fixture(tmp_path)
    added = client.post(
        "/v1/security/findings",
        json={
            "title": "SQL injection in reports module",
            "severity": "high",
            "file": "src/reports.py",
            "description": "User-controlled order_by reaches the query builder.",
        },
    ).json()
    assert added["ok"] is True
    fid = added["finding"]["id"]
    assert added["finding"]["status"] == "open"

    listed = client.get("/v1/security/findings").json()["findings"]
    assert len(listed) == 1 and listed[0]["id"] == fid

    # Link the fix PR + mark fixed.
    updated = client.patch(
        f"/v1/security/findings/{fid}",
        json={"status": "fixed", "pr_url": "https://github.com/acme/repo/pull/42"},
    ).json()
    assert updated["ok"] is True
    assert updated["finding"]["status"] == "fixed"
    assert updated["finding"]["pr_url"].endswith("/42")

    # Filter by status.
    open_ = client.get("/v1/security/findings?status=open").json()["findings"]
    fixed = client.get("/v1/security/findings?status=fixed").json()["findings"]
    assert open_ == [] and len(fixed) == 1

    # Delete.
    assert client.delete(f"/v1/security/findings/{fid}").json()["ok"] is True
    assert client.get("/v1/security/findings").json()["findings"] == []


def test_finding_validation_and_unknown(tmp_path):
    client, _ = _fixture(tmp_path)
    bad = client.post("/v1/security/findings", json={"title": "  "}).json()
    assert bad["ok"] is False and "title" in bad["error"].lower()
    res = client.patch("/v1/security/findings/ghost", json={"status": "closed"}).json()
    assert res["ok"] is False and "unknown" in res["error"]


# -- tools + catalog capability ---------------------------------------------------


def test_security_tools_operate_on_store(tmp_path):
    store = SecurityStore(tmp_path / "security")
    tools = {t.__name__: t for t in security_tools(store)}

    tm = tools["threat_model_read"]()
    assert "Threat Model" in tm["content"]

    tools["threat_model_update"](content="# Edited")
    assert tools["threat_model_read"]()["content"] == "# Edited"

    logged = tools["security_finding_log"](
        title="Hardcoded key", severity="critical", file=".env.example"
    )
    assert logged["ok"] is True
    fid = logged["finding"]["id"]

    listed = tools["security_finding_list"]()["findings"]
    assert len(listed) == 1

    updated = tools["security_finding_update"](
        finding_id=fid, status="closed", pr_url="https://example.com/pr/1"
    )
    assert updated["finding"]["status"] == "closed"

    # Validation via the tool surface.
    assert tools["security_finding_log"](title="")["error"]


def test_security_capability_available_to_security_personas(tmp_path):
    from coworker.personas.registry import PersonaRegistry

    reg = PersonaRegistry(state_path=tmp_path / "personas.json")
    for pid in ("security", "cloud-posture", "dep-audit"):
        entry = reg.get(pid)
        assert entry is not None
        assert "security" in entry.manifest.tools


# -- GUI sandbox toggle -------------------------------------------------------------


def test_sandbox_settings_api_roundtrip(tmp_path):
    client, _ = _fixture(tmp_path)
    defaults = client.get("/v1/settings/sandbox").json()
    assert defaults["enabled"] is False
    assert defaults["backend"] == "docker"

    updated = client.put(
        "/v1/settings/sandbox",
        json={"enabled": True, "image": "python:3.13-slim", "network": "none"},
    ).json()
    assert updated["enabled"] is True
    assert updated["image"] == "python:3.13-slim"

    # The persisted JSON file backs the read.
    from coworker.sandbox_settings import read_sandbox_settings

    persisted = read_sandbox_settings()
    assert persisted["enabled"] is True
    assert persisted["image"] == "python:3.13-slim"


def test_docker_executor_per_command_fallback(tmp_path, monkeypatch):
    """v2 per-command probe: if Docker vanished after build, the command runs
    locally and the result is marked sandboxed:false with a reason."""
    import subprocess as _sp

    from coworker.tools.sandbox import DockerExecutor

    def _no_docker(*args, **kwargs):
        raise FileNotFoundError("docker not found")

    monkeypatch.setattr(_sp, "run", _no_docker)

    ex = DockerExecutor(workspace=tmp_path)
    res = ex.run("echo sandbox-fallback-ok", timeout=5)
    assert res["sandboxed"] is False
    assert "Docker" in res.get("fallback_reason", "")
    assert "sandbox-fallback-ok" in res.get("output", "")


def test_docker_executor_background_tasks(tmp_path, monkeypatch):
    """v2 background tasks: a long-lived container + docker exec round-trip —
    start, read output, check liveness, kill."""
    import subprocess as _sp

    from coworker.tools.sandbox import DockerExecutor

    def _fake_docker(cmd, *args, **kwargs):
        class _R:
            returncode = 0
            stdout = ""
            stderr = ""

        r = _R()
        if len(cmd) >= 2:
            sub = cmd[1]
            joined = " ".join(cmd[2:])
            if sub == "inspect":
                r.stdout = "true"
            elif sub == "run":
                r.stdout = "container-abc\n"
            elif sub == "exec":
                if "echo $!" in joined:
                    r.stdout = "123\n"
                elif "kill -0" in joined:
                    r.returncode = 0  # alive
                elif "cat " in joined:
                    r.stdout = "build output\n"
                elif "kill " in joined:
                    r.returncode = 0
        return r

    monkeypatch.setattr(_sp, "run", _fake_docker)

    ex = DockerExecutor(workspace=tmp_path)
    bg = ex.run_background("echo hi")
    assert bg["status"] == "running"
    tid = bg["task_id"]

    out = ex.background_output(tid)
    assert out["status"] == "running"
    assert "build output" in out["output"]

    killed = ex.background_kill(tid)
    assert killed["status"] == "killed"
    assert ex.background_output(tid)["error"]  # unknown after kill


@pytest.mark.skipif(
    sys.platform == "win32"
    or not __import__("coworker.tools.sandbox", fromlist=["docker_available"]).docker_available(),
    reason="requires Docker with Linux containers "
    "(Windows runners only offer Windows containers, which reject --read-only)",
)
def test_docker_executor_real_sandbox_run(tmp_path):
    """Windows/Docker CI validation: with Docker present, a real command runs sandboxed
    (no network, read-only root) and the result carries sandboxed: true."""
    from coworker.tools.sandbox import DockerExecutor

    ex = DockerExecutor(workspace=tmp_path)
    res = ex.run("echo sandboxed-ok", timeout=30)
    assert res.get("sandboxed") is True
    assert "sandboxed-ok" in res.get("output", "")


def test_docker_executor_file_channel(tmp_path, monkeypatch):
    """v2 file channel: read/write/list inside the container, with traversal guard."""
    import subprocess as _sp

    from coworker.tools.sandbox import DockerExecutor

    def _fake_docker(cmd, *args, **kwargs):
        class _R:
            returncode = 0
            stdout = ""
            stderr = ""

        r = _R()
        joined = " ".join(cmd)
        if "inspect" in joined:
            r.stdout = "true"
        elif "run -d" in joined:
            r.stdout = "container-abc\n"
        elif "cat /workspace/src/app.py" in joined:
            r.stdout = "hello\nworld\n"
        elif "find /workspace" in joined:
            r.stdout = "/workspace/src/app.py\n"
        elif "mkdir -p" in joined or "cat >" in joined:
            r.returncode = 0
        return r

    monkeypatch.setattr(_sp, "run", _fake_docker)
    ex = DockerExecutor(workspace=tmp_path)

    content, err = ex.read_workspace_file("src/app.py")
    assert err is None and content == "hello\nworld\n"

    listing, err = ex.list_workspace_dir("")
    assert err is None and listing == "/workspace/src/app.py\n"

    err = ex.write_workspace_file("src/app.py", "x")
    assert err is None

    # Traversal is refused before any docker call.
    content, err = ex.read_workspace_file("../etc/passwd")
    assert err and "unsafe" in err


def test_sandboxed_file_tools_roundtrip():
    """The sandboxed file tools expose read/write/replace/list/delete with
    host-compatible names."""
    from coworker.tools.sandbox_files import sandboxed_file_tools

    class FakeExecutor:
        def __init__(self):
            self.files: dict[str, str] = {}

        def read_workspace_file(self, rel):
            if rel in self.files:
                return self.files[rel], None
            return None, f"not a file: {rel}"

        def write_workspace_file(self, rel, content):
            self.files[rel] = content
            return None

        def delete_workspace_file(self, rel):
            if rel in self.files:
                del self.files[rel]
            return None

        def list_workspace_dir(self, rel):
            return "\n".join(f"/workspace/{k}" for k in sorted(self.files)), None

    ex = FakeExecutor()
    tools = {t.__name__: t for t in sandboxed_file_tools(ex)}
    assert {
        "read_file",
        "write_file",
        "list_files",
        "replace_in_file",
        "delete_file",
        "apply_patch",
    } <= set(tools)

    assert tools["write_file"]("src/app.py", "print('hi')\n")["ok"] is True
    r = tools["read_file"]("src/app.py")
    assert r["content"] == "     1\tprint('hi')"
    assert r["total_lines"] == 1

    # Apply a unified diff patch inside the container-backed flow.
    patch = (
        "--- a/app.py\n+++ b/app.py\n"
        "@@ -1,1 +1,1 @@\n"
        "-print('hi')\n"
        "+print('hello')\n"
    )
    assert tools["apply_patch"]("src/app.py", patch)["ok"] is True
    assert tools["read_file"]("src/app.py")["content"] == "     1\tprint('hello')"

    # Replace a unique substring, then delete.
    assert tools["replace_in_file"]("src/app.py", "'hello'", "'bye'")["ok"] is True
    assert tools["read_file"]("src/app.py")["content"] == "     1\tprint('bye')"
    assert tools["delete_file"]("src/app.py")["ok"] is True
    assert "not a file" in tools["read_file"]("src/app.py")["error"]

    listing = tools["list_files"]("")
    assert listing["entries"] == []


def test_sandboxed_file_tools_host_fallback(tmp_path):
    """When Docker dies mid-session (executor raises RuntimeError), the tools fall
    back to the host workspace and mark the result sandboxed:false."""
    from coworker.tools.sandbox_files import sandboxed_file_tools

    class DeadExecutor:
        def read_workspace_file(self, rel):
            raise RuntimeError("Docker daemon unreachable — sandbox unavailable")

        def write_workspace_file(self, rel, content):
            raise RuntimeError("Docker daemon unreachable — sandbox unavailable")

        def list_workspace_dir(self, rel):
            raise RuntimeError("Docker daemon unreachable — sandbox unavailable")

        def delete_workspace_file(self, rel):
            raise RuntimeError("Docker daemon unreachable — sandbox unavailable")

    tools = {t.__name__: t for t in sandboxed_file_tools(DeadExecutor(), workspace=tmp_path)}

    w = tools["write_file"]("notes.txt", "fallback content\n")
    assert w["ok"] is True
    assert w["sandboxed"] is False
    assert "Docker" in w["fallback_reason"]

    r = tools["read_file"]("notes.txt")
    assert r["sandboxed"] is False
    assert "fallback content" in r["content"]

    repl = tools["replace_in_file"]("notes.txt", "fallback", "host")
    assert repl["sandboxed"] is False
    assert "host content" in tools["read_file"]("notes.txt")["content"]

    listing = tools["list_files"]("")
    assert listing["sandboxed"] is False
    assert any("notes.txt" in e for e in listing["entries"])

    d = tools["delete_file"]("notes.txt")
    assert d["sandboxed"] is False
    assert "error" in tools["read_file"]("notes.txt")  # deleted → read fails


def test_sandboxed_file_tools_multi_root(tmp_path):
    """Multi-root sessions: absolute paths under a mounted root are mapped into the
    container (/roots/N) and work alongside workspace-relative paths."""
    from coworker.tools.sandbox_files import sandboxed_file_tools

    class FakeExecutor:
        def __init__(self):
            self.files: dict[str, str] = {}

        def add_mount(self, host_path):
            return f"/roots/{len({})}"  # unused in this fake; root_map is built by factory

        def container_prefix_for(self, host_path):  # pragma: no cover
            return None

        def read_container_file(self, cp):
            if cp in self.files:
                return self.files[cp], None
            return None, f"not a file: {cp}"

        def write_container_file(self, cp, content):
            self.files[cp] = content
            return None

        def list_container_dir(self, cp):
            return "\n".join(k for k in sorted(self.files) if k.startswith(cp.rstrip("/") + "/")), None

        def delete_container_file(self, cp):
            self.files.pop(cp, None)
            return None

        def read_workspace_file(self, rel):
            return self.read_container_file(f"/workspace/{rel}")

        def write_workspace_file(self, rel, content):
            return self.write_container_file(f"/workspace/{rel}", content)

        def list_workspace_dir(self, rel):
            base = "/workspace" if not rel else f"/workspace/{rel}"
            return self.list_container_dir(base)

        def delete_workspace_file(self, rel):
            return self.delete_container_file(f"/workspace/{rel}")

    ws = tmp_path / "ws"
    root = tmp_path / "root"
    root.mkdir()
    ex = FakeExecutor()
    tools = {
        t.__name__: t
        for t in sandboxed_file_tools(
            ex,
            workspace=ws,
            roots=[type("RootDir", (), {"path": str(root)})()],
        )
    }

    # Absolute path under the mounted root is mapped into the container.
    abs_file = str(root / "sub" / "x.txt")
    assert tools["write_file"](abs_file, "root content\n")["ok"] is True
    r = tools["read_file"](abs_file)
    assert "root content" in r["content"]
    assert tools["list_files"](str(root))["entries"]  # non-empty

    # Workspace-relative paths still work.
    assert tools["write_file"]("ws.txt", "ws content\n")["ok"] is True
    assert "ws content" in tools["read_file"]("ws.txt")["content"]

    # A path outside every root is refused.
    outside = str(tmp_path / "outside" / "x")
    assert "outside" in tools["read_file"](outside)["error"]


def test_apply_unified_diff():
    """The minimal unified-diff applier handles context/removal/additions and
    fails loudly on mismatches."""
    from coworker.tools.unified_diff import apply_unified_diff

    content = "line1\nline2\nline3\nline4\n"
    patch = (
        "--- a/file\n+++ b/file\n"
        "@@ -1,4 +1,4 @@\n"
        " line1\n"
        "-line2\n"
        "+LINE2\n"
        " line3\n"
        " line4\n"
    )
    new, err = apply_unified_diff(content, patch)
    assert err is None
    assert new == "line1\nLINE2\nline3\nline4\n"

    # A mismatch fails loudly instead of corrupting content.
    bad, err = apply_unified_diff("line1\nOTHER\nline3\nline4\n", patch)
    assert err is not None and "mismatch" in err

    # No hunks → error.
    bad, err = apply_unified_diff(content, "--- a\n+++ b\n")
    assert err is not None and "no hunks" in err