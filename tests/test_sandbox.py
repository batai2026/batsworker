"""Docker sandbox executor (tools/sandbox.py) — build command, run with mocked
subprocess, graceful fallback."""

from pathlib import Path
from subprocess import TimeoutExpired
from unittest.mock import patch

import pytest

from coworker.tools.sandbox import DockerExecutor, docker_available


@pytest.fixture()
def ws(tmp_path: Path) -> Path:
    d = tmp_path / "ws"
    d.mkdir(parents=True)
    return d


@pytest.fixture()
def executor(ws: Path) -> DockerExecutor:
    return DockerExecutor(workspace=ws)


# -- command building ------------------------------------------------------------


def test_build_command_basic(executor: DockerExecutor):
    cmd = executor._build_command("echo hello")
    assert cmd[0] == "docker"
    assert "--rm" in cmd
    assert "--network" in cmd and "none" in cmd
    assert "--read-only" in cmd
    assert "bash" in cmd
    assert "echo hello" in cmd


def test_build_command_mounts_workspace(executor: DockerExecutor):
    cmd = executor._build_command("ls")
    # Workspace mount is present
    mounts = [cmd[i + 1] for i, c in enumerate(cmd) if c == "-v"]
    assert any(executor._docker_workspace in m for m in mounts)


def test_build_command_extra_mounts(tmp_path):
    executor = DockerExecutor(
        workspace=tmp_path / "ws",
        extra_mounts=["/host/data:/container/data:ro"],
    )
    cmd = executor._build_command("ls")
    mounts = [cmd[i + 1] for i, c in enumerate(cmd) if c == "-v"]
    assert any("/host/data:/container/data:ro" in m for m in mounts)


# -- run (mocked subprocess) ------------------------------------------------------


def _mock_run(returncode=0, stdout="hello\n", stderr=""):
    """Patch subprocess.run to return a fake result."""
    from unittest.mock import MagicMock

    mock = MagicMock()
    mock.returncode = returncode
    mock.stdout = stdout
    mock.stderr = stderr
    return mock


def test_run_success(executor: DockerExecutor):
    with patch("subprocess.run", return_value=_mock_run(returncode=0, stdout="hello\n")):
        result = executor.run("echo hello")
    assert result["exit_code"] == 0
    assert result["output"] == "hello\n"
    assert result["sandboxed"] is True
    assert result["cwd"] == "/workspace"
    assert not result["timed_out"]
    assert not result["truncated"]


def test_run_nonzero_exit(executor: DockerExecutor):
    with patch("subprocess.run", return_value=_mock_run(returncode=1, stdout="", stderr="command not found")):
        result = executor.run("bad_command")
    assert result["exit_code"] == 1
    assert "command not found" in result["output"]


def test_run_stderr_appended(executor: DockerExecutor):
    with patch("subprocess.run", return_value=_mock_run(stdout="out", stderr="err")):
        result = executor.run("mixed")
    assert "out" in result["output"]
    assert "err" in result["output"]


def test_run_output_truncated(executor: DockerExecutor):
    executor.max_output_chars = 10
    with patch("subprocess.run", return_value=_mock_run(stdout="x" * 100)):
        result = executor.run("big_output")
    assert result["truncated"]
    assert len(result["output"]) <= 10 + len("\n[stderr]\n")


def test_run_timeout(executor: DockerExecutor):
    from subprocess import TimeoutExpired

    with patch("subprocess.run", side_effect=TimeoutExpired(cmd="docker", timeout=5)):
        result = executor.run("sleep 999")
    assert result["timed_out"]
    assert result["exit_code"] is None


def test_run_docker_not_found(executor: DockerExecutor):
    with patch("subprocess.run", side_effect=FileNotFoundError("docker not found")):
        result = executor.run("echo hello")
    # Docker CLI missing at runtime → graceful local fallback (same contract as
    # OpenShellExecutor): the command runs locally, marked not sandboxed with a
    # reason the UI can surface, instead of a hard error.
    assert result.get("sandboxed") is False
    assert "Docker CLI not found" in result.get("fallback_reason", "")


def test_run_no_network_flag(executor: DockerExecutor):
    """The default network mode is 'none' — sandboxed commands can't reach the network."""
    cmd = executor._build_command("curl https://evil.com")
    idx = cmd.index("--network")
    assert cmd[idx + 1] == "none"


def test_run_read_only_root(executor: DockerExecutor):
    cmd = executor._build_command("touch /root-file")
    assert "--read-only" in cmd


# -- docker availability -----------------------------------------------------------


def test_docker_available_docker_missing():
    with patch("subprocess.run", side_effect=FileNotFoundError("docker not found")):
        assert docker_available() is False


def test_docker_available_daemon_down():
    from unittest.mock import MagicMock

    mock = MagicMock()
    mock.returncode = 1
    with patch("subprocess.run", return_value=mock):
        assert docker_available() is False


def test_docker_available_ok():
    from unittest.mock import MagicMock

    mock = MagicMock()
    mock.returncode = 0
    with patch("subprocess.run", return_value=mock):
        assert docker_available() is True


# -- sandboxed flag in result -------------------------------------------------------


def test_sandboxed_flag_present(executor: DockerExecutor):
    with patch("subprocess.run", return_value=_mock_run()):
        result = executor.run("echo hello")
    assert result.get("sandboxed") is True


def test_sandboxed_flag_in_error_result(executor: DockerExecutor):
    with patch("subprocess.run", side_effect=TimeoutExpired(cmd="docker", timeout=5)):
        result = executor.run("slow")
    assert result.get("sandboxed") is True
