"""Tests for the session environment context block (system-prompt injection)."""

from __future__ import annotations

import subprocess
import sys

from coworker.environment import environment_context


def _git_repo(tmp_path):
    ws = tmp_path / "repo"
    ws.mkdir()
    def run(*a):
        return subprocess.run(
            ["git", "-C", str(ws), *a], capture_output=True, check=True
        )
    run("init", "-q", "-b", "main")
    run("config", "user.email", "t@t.io")
    run("config", "user.name", "T")
    (ws / "f.txt").write_text("1", encoding="utf-8")
    run("add", "-A")
    run("commit", "-qm", "first commit")
    return ws


def test_context_includes_workspace_platform_and_date(tmp_path):
    block = environment_context(tmp_path)
    assert str(tmp_path.resolve()) in block
    assert sys.platform in block
    assert "Today's date:" in block
    assert "<environment>" in block and "</environment>" in block


def test_context_outside_git_repo(tmp_path):
    assert "not a git repository" in environment_context(tmp_path)


def test_context_with_git_repo(tmp_path):
    ws = _git_repo(tmp_path)
    block = environment_context(ws)
    assert "Git branch: main" in block
    assert "Git status: clean" in block
    assert "first commit" in block


def test_context_shows_dirty_status(tmp_path):
    ws = _git_repo(tmp_path)
    (ws / "f.txt").write_text("2", encoding="utf-8")
    (ws / "new.txt").write_text("x", encoding="utf-8")
    block = environment_context(ws)
    assert "Git status (2 changed):" in block
    assert "f.txt" in block and "new.txt" in block


def test_context_survives_non_ascii_git_output(tmp_path):
    """Windows ANSI code pages (GBK) cannot decode UTF-8 git output — the subprocess
    reader thread dies and stdout comes back None. A repo with a Chinese commit
    subject must still produce a snapshot instead of killing the WebSocket."""
    ws = _git_repo(tmp_path)
    (ws / "中文.txt").write_text("1", encoding="utf-8")
    subprocess.run(["git", "-C", str(ws), "add", "-A"], capture_output=True, check=True)
    subprocess.run(
        ["git", "-C", str(ws), "commit", "-qm", "中文提交：文档归档"],
        capture_output=True,
        check=True,
    )
    block = environment_context(ws)
    assert "Git branch: main" in block
    assert "Recent commits:" in block


def test_git_helper_tolerates_missing_stdout(tmp_path, monkeypatch):
    """Defensive guard: a dead reader thread yields stdout=None, not an exception."""

    class _R:
        returncode = 0
        stdout = None

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _R())
    from coworker.environment import _git

    assert _git(tmp_path, "status") == ""


class _Stub:
    def complete(self, **kwargs):  # pragma: no cover
        raise NotImplementedError

    def capabilities(self, model):
        from coworker.providers import ModelCapabilities

        return ModelCapabilities()


def test_build_engine_injects_environment(tmp_path):
    from coworker.agent import build_engine
    from coworker.agents import code_agent

    engine = build_engine(agent=code_agent(), workspace=tmp_path, provider=_Stub())
    try:
        system = engine.messages[0]
        assert system["role"] == "system"
        assert "<environment>" in system["content"]
        assert str(tmp_path.resolve()) in system["content"]
    finally:
        engine.executor.close()
