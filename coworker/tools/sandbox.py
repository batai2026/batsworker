"""Docker-backed sandbox executor — the ContainerExecutor hedge, realized.

Runs each shell command inside a Docker container with:
  - the workspace mounted read-write at ``/workspace``
  - **no network** by default (``--network none``)
  - a **read-only root filesystem** (``--read-only``) — the workspace is the
    only writable path
  - a non-interactive environment (same env sanitization as LocalExecutor)
  - a hard timeout enforced by both Docker and the subprocess call

Design decisions:
  - **Per-command ``docker run``** for foreground commands: simpler, no
    state to manage, each command gets a clean environment. The tradeoff is
    that ``cd`` and ``export`` don't persist across commands — the sandbox
    trades convenience for isolation. If persistence is needed, commands can
    be chained with ``&&``.
  - **Background tasks (v2)**: run inside a long-lived container started
    lazily (``docker run -d --rm … sleep infinity``), with each task's output captured
    to a workspace file and read back via ``docker exec`` — the workspace stays
    the only writable path (root FS remains read-only).
  - **Graceful fallback**: if the Docker CLI is not found at construction
    time, the manager falls back to LocalExecutor (a sandbox that doesn't
    work is worse than no sandbox — the user is told via a notice).
  - **Config-driven**: ``sandbox_enabled`` master switch, ``sandbox_image``
    (default ``python:3.12-slim``), ``sandbox_network`` (default ``none``).

What this does NOT cover (v1):
  - File tools (read_file, write_file, …) still operate directly on the
    filesystem — they are already scoped to writable roots by the permission
    engine (path scoping + root checking), which is a different (soft) layer.
  - Network egress from tools like web_fetch is governed by the permission
    system, not the sandbox (the sandbox only covers shell commands).
"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any, Optional

from .shell import (
    UTF8_TEXT_IO,
    Executor,
    LocalExecutor,
    _DEFAULT_TIMEOUT,
    _MAX_TIMEOUT,
    _NONINTERACTIVE_ENV,
)

_IS_WINDOWS = sys.platform == "win32"

_DOCKER_TIMEOUT = 10.0  # for the `docker` CLI availability check


class DockerExecutor(Executor):
    """Runs shell commands inside a Docker container for sandbox isolation."""

    def __init__(
        self,
        *,
        workspace: str | Path,
        image: str = "python:3.12-slim",
        network: str = "none",
        default_timeout: float = _DEFAULT_TIMEOUT,
        max_output_chars: int = 20_000,
        extra_mounts: Optional[list[str]] = None,
    ) -> None:
        self.workspace = str(Path(workspace).expanduser().resolve())
        self.image = image
        self.network = network
        self.default_timeout = default_timeout
        self.max_output_chars = max_output_chars
        self.extra_mounts = extra_mounts or []
        # Lazily-created LocalExecutor used when Docker becomes unavailable AFTER
        # engine build (per-command probe + runtime fallback, sandbox v2). Commands
        # that fall back run locally and are marked `sandboxed: false` with a reason.
        self._fallback: Optional[LocalExecutor] = None
        # Normalize to POSIX path for Docker -v flag (Windows paths need /c/... or C:\...).
        self._docker_workspace = self.workspace.replace("\\", "/")
        # Long-lived container for background tasks (sandbox v2): started lazily,
        # reused across `docker exec` calls, stopped in close().
        self._container_id: Optional[str] = None
        self._bg_tasks: dict[str, dict[str, str]] = {}
        self._bg_dir = "/workspace/.coworker-bg"
        # Extra session roots mounted into the container: host path → container prefix
        # (e.g. "/Users/me/scratch" → "/roots/0"). Registered before the container
        # starts so `_ensure_container` picks them up as extra `-v` mounts.
        self._mounts: dict[str, str] = {}

    def _build_command(self, command: str) -> list[str]:
        """Build the full docker run command for one shell invocation."""
        cmd = [
            "docker", "run",
            "--rm",                       # clean up after each command
            "--network", self.network,    # isolate network
            "--read-only",                # read-only root filesystem
            "--memory", "512m",           # memory cap
            "--cpus", "2",                # CPU cap
            "-v", f"{self._docker_workspace}:/workspace",
            "-w", "/workspace",
            "-e", "GIT_TERMINAL_PROMPT=0",
            "-e", "DEBIAN_FRONTEND=noninteractive",
            "-e", "PYTHONUNBUFFERED=1",
            "-e", "PIP_NO_INPUT=1",
            "-e", "HOME=/tmp",
        ]
        for mount in self.extra_mounts:
            cmd.extend(["-v", mount])
        cmd.extend([self.image, "bash", "-c", command])
        return cmd

    def run(self, command: str, timeout: Optional[float] = None) -> dict[str, Any]:
        timeout = min(timeout or self.default_timeout, _MAX_TIMEOUT)
        docker_cmd = self._build_command(command)

        try:
            proc = subprocess.run(
                docker_cmd,
                capture_output=True,
                text=True,
                **UTF8_TEXT_IO,
                timeout=timeout,
                env={**os.environ, **_NONINTERACTIVE_ENV},
            )
        except subprocess.TimeoutExpired:
            return self._result(command, None, "", timed_out=True)
        except FileNotFoundError:
            # Per-command probe: Docker vanished after engine build (CLI removed /
            # daemon stopped). Fall back to the local executor and say so loudly.
            return self._fallback_result(
                command,
                timeout,
                "Docker CLI not found — ran locally (sandbox unavailable)",
            )

        # A daemon that went down after the build-time check makes `docker run`
        # exit non-zero with this stderr marker; fall back rather than wedging the
        # agent with an opaque error.
        if proc.returncode != 0 and "cannot connect to the docker daemon" in (
            proc.stderr or ""
        ).lower():
            return self._fallback_result(
                command,
                timeout,
                "Docker daemon unreachable — ran locally (sandbox unavailable)",
            )

        output = proc.stdout or ""
        if proc.stderr:
            output += "\n[stderr]\n" + proc.stderr
        if len(output) > self.max_output_chars:
            output = output[: self.max_output_chars]
            truncated = True
        else:
            truncated = False

        return self._result(
            command,
            proc.returncode,
            output,
            truncated=truncated,
        )

    def _ensure_fallback(self) -> LocalExecutor:
        """A local executor sharing the workspace, created on first fallback."""
        if self._fallback is None:
            self._fallback = LocalExecutor(
                cwd=self.workspace,
                default_timeout=self.default_timeout,
                max_output_chars=self.max_output_chars,
            )
        return self._fallback

    def _fallback_result(
        self, command: str, timeout: float, reason: str
    ) -> dict[str, Any]:
        """Run the command locally when the sandbox is unavailable, marking the
        result so the UI can show the command was NOT sandboxed and why."""
        res = self._ensure_fallback().run(command, timeout=timeout)
        res["sandboxed"] = False
        res["fallback_reason"] = reason
        return res

    # -- background tasks (sandbox v2: long-lived container + docker exec) ---------

    def _docker_exec(
        self, args: list[str], timeout: float = 30.0, input: Optional[str] = None
    ) -> tuple[Optional[int], str, str]:
        """Run a `docker <args>` command; returns (rc, stdout, stderr)."""
        try:
            proc = subprocess.run(
                ["docker", *args],
                capture_output=True,
                text=True,
                **UTF8_TEXT_IO,
                timeout=timeout,
                input=input,
                env={**os.environ, **_NONINTERACTIVE_ENV},
            )
        except FileNotFoundError:
            return None, "", "Docker CLI not found"
        except subprocess.TimeoutExpired:
            return None, "", "docker command timed out"
        return proc.returncode, proc.stdout or "", proc.stderr or ""

    def _ensure_container(self) -> Optional[str]:
        """A long-lived, sandboxed container for background tasks; reuses it while
        it stays running, otherwise starts a fresh one."""
        if self._container_id:
            rc, out, _ = self._docker_exec(
                ["inspect", "-f", "{{.State.Running}}", self._container_id], timeout=10
            )
            if rc == 0 and out.strip() == "true":
                return self._container_id
            self._container_id = None
        cmd = [
            "run", "-d", "--rm",
            "--network", self.network,
            "--read-only",
            "--memory", "512m",
            "--cpus", "2",
            "-v", f"{self._docker_workspace}:/workspace",
            "-w", "/workspace",
            "-e", "GIT_TERMINAL_PROMPT=0",
            "-e", "DEBIAN_FRONTEND=noninteractive",
            "-e", "PYTHONUNBUFFERED=1",
            "-e", "PIP_NO_INPUT=1",
            "-e", "HOME=/tmp",
            *[f"-v{m}" for m in self.extra_mounts],
            self.image,
            "sleep", "infinity",
        ]
        rc, out, err = self._docker_exec(cmd, timeout=60)
        if rc != 0 or not out.strip():
            return None
        self._container_id = out.strip().splitlines()[0]
        return self._container_id

    def run_background(self, command: str) -> dict[str, Any]:
        """Start a command in the long-lived container, detached, with output
        captured to a workspace file that background_output() reads."""
        cid = self._ensure_container()
        if not cid:
            return {
                "error": (
                    "sandbox background tasks need a running Docker container — "
                    "start Docker or disable sandbox mode"
                )
            }
        task_id = f"bg-{uuid.uuid4().hex[:8]}"
        log_path = f"{self._bg_dir}/{task_id}.log"
        script = (
            f"mkdir -p {self._bg_dir} && cd /workspace && "
            f"{{ {command}; }} > {log_path} 2>&1 & echo $!"
        )
        rc, out, err = self._docker_exec(["exec", cid, "bash", "-c", script], timeout=15)
        if rc != 0 or not out.strip():
            return {"error": f"failed to start background task: {err or out}"}
        pid = out.strip().splitlines()[-1].strip()
        self._bg_tasks[task_id] = {"pid": pid, "log": log_path, "container": cid}
        return {
            "task_id": task_id,
            "command": command,
            "status": "running",
            "note": (
                "use shell_task_output to read its output, shell_task_kill to stop it"
            ),
        }

    def background_output(self, task_id: str) -> dict[str, Any]:
        info = self._bg_tasks.get(task_id)
        if not info:
            return {"error": f"unknown task: {task_id}"}
        cid = info["container"]
        rc, _, _ = self._docker_exec(
            ["exec", cid, "bash", "-c", f"kill -0 {info['pid']} 2>/dev/null"]
        )
        alive = rc == 0
        _rc, out, _err = self._docker_exec(
            ["exec", cid, "bash", "-c", f"cat {info['log']} 2>/dev/null"]
        )
        output = out or ""
        truncated = len(output) > self.max_output_chars
        if truncated:
            output = output[-self.max_output_chars :]
        return {
            "task_id": task_id,
            "status": "running" if alive else "exited",
            "exit_code": None if alive else 0,
            "output": output,
            "truncated": truncated,
        }

    def background_kill(self, task_id: str) -> dict[str, Any]:
        info = self._bg_tasks.get(task_id)
        if not info:
            return {"error": f"unknown task: {task_id}"}
        cid = info["container"]
        self._docker_exec(
            ["exec", cid, "bash", "-c", f"kill {info['pid']} 2>/dev/null; rm -f {info['log']}"]
        )
        self._bg_tasks.pop(task_id, None)
        return {"task_id": task_id, "status": "killed"}

    # -- file channel (sandbox v2: file tools run inside the container) -------------

    @staticmethod
    def _safe_rel(path: str) -> Optional[str]:
        """Normalize a workspace-relative path and reject traversal/absolute paths.

        The container's `/workspace` is the mounted workspace; ``..`` could climb to
        the container's (read-only) image filesystem, so we refuse it outright.
        """
        raw = (path or "").replace("\\", "/").strip("/")
        if not raw:
            return None
        parts = raw.split("/")
        if any(p in ("", ".", "..") for p in parts):
            return None
        return "/".join(parts)

    def add_mount(self, host_path: str) -> str:
        """Register an extra session root as a container mount; returns its
        container prefix (e.g. ``/roots/0``). Call before the container starts."""
        host = str(Path(host_path).expanduser().resolve())
        if host in self._mounts:
            return self._mounts[host]
        prefix = f"/roots/{len(self._mounts)}"
        self._mounts[host] = prefix
        self.extra_mounts = [
            *self.extra_mounts,
            f"{host}:{prefix}",
        ]
        return prefix

    def container_prefix_for(self, host_path: str) -> Optional[str]:
        """The container prefix for a host path, or None if not mounted."""
        host = str(Path(host_path).expanduser().resolve())
        return self._mounts.get(host)

    @staticmethod
    def _safe_container_path(path: str) -> Optional[str]:
        """Validate a container-absolute path: must live under /workspace or /roots/*
        and contain no '..' or empty segments. Returns the normalized path."""
        raw = (path or "").replace("\\", "/").rstrip("/")
        if raw == "/workspace" or raw == "/roots":
            return raw
        if not (raw.startswith("/workspace/") or raw.startswith("/roots/")):
            return None
        parts = raw.lstrip("/").split("/")
        if any(p in ("", ".", "..") for p in parts):
            return None
        return raw

    def _container(self) -> Optional[str]:
        cid = self._ensure_container()
        if not cid:
            raise RuntimeError(
                "sandbox file access needs a running Docker container — "
                "start Docker or disable sandbox mode"
            )
        return cid

    def read_container_file(self, container_path: str) -> tuple[Optional[str], Optional[str]]:
        """Read a container-absolute file path; returns (content, error)."""
        safe = self._safe_container_path(container_path)
        if safe is None:
            return None, f"unsafe path: {container_path}"
        cid = self._container()
        rc, out, err = self._docker_exec(
            ["exec", cid, "bash", "-c", f"cat {safe} 2>/dev/null"],
            timeout=30,
        )
        if rc != 0:
            return None, f"not a file: {container_path}"
        return out, None

    def write_container_file(self, container_path: str, content: str) -> Optional[str]:
        """Write a container-absolute file path; returns an error string."""
        safe = self._safe_container_path(container_path)
        if safe is None:
            return f"unsafe path: {container_path}"
        cid = self._container()
        parent = "/".join(safe.split("/")[:-1])
        rc, _out, err = self._docker_exec(
            ["exec", "-i", cid, "bash", "-c", f"mkdir -p {parent} && cat > {safe}"],
            timeout=30,
            input=content or "",
        )
        if rc != 0:
            return err or "write failed in sandbox"
        return None

    def list_container_dir(self, container_path: str) -> tuple[Optional[str], Optional[str]]:
        """List entries under a container-absolute directory; returns (listing, error)."""
        safe = self._safe_container_path(container_path.rstrip("/") + "/") if container_path else "/workspace"
        if safe is None:
            return None, f"unsafe path: {container_path}"
        cid = self._container()
        rc, out, err = self._docker_exec(
            ["exec", cid, "bash", "-c", f"find {safe} -maxdepth 1 -mindepth 1 2>/dev/null | sort"],
            timeout=30,
        )
        if rc != 0:
            return None, err or "list failed in sandbox"
        return out, None

    def delete_container_file(self, container_path: str) -> Optional[str]:
        """Delete a container-absolute file path; returns an error string."""
        safe = self._safe_container_path(container_path)
        if safe is None:
            return f"unsafe path: {container_path}"
        cid = self._container()
        rc, _out, err = self._docker_exec(
            ["exec", cid, "bash", "-c", f"rm -f {safe}"],
            timeout=30,
        )
        if rc != 0:
            return err or "delete failed in sandbox"
        return None

    def read_workspace_file(self, rel: str) -> tuple[Optional[str], Optional[str]]:
        """Read a file under /workspace in the container; returns (content, error)."""
        safe = self._safe_rel(rel)
        if safe is None:
            return None, f"unsafe path: {rel}"
        return self.read_container_file(f"/workspace/{safe}")

    def write_workspace_file(self, rel: str, content: str) -> Optional[str]:
        """Write a file under /workspace in the container; returns an error string."""
        safe = self._safe_rel(rel)
        if safe is None:
            return f"unsafe path: {rel}"
        return self.write_container_file(f"/workspace/{safe}", content)

    def list_workspace_dir(self, rel: str) -> tuple[Optional[str], Optional[str]]:
        """List entries under /workspace/<rel>; returns (listing, error)."""
        safe = self._safe_rel(rel) if rel else ""
        target = "/workspace" if not safe else f"/workspace/{safe}"
        return self.list_container_dir(target)

    def delete_workspace_file(self, rel: str) -> Optional[str]:
        """Delete a file under /workspace in the container; returns an error string."""
        safe = self._safe_rel(rel)
        if safe is None:
            return f"unsafe path: {rel}"
        return self.delete_container_file(f"/workspace/{safe}")

    def close(self) -> None:  # pragma: no cover - lifecycle, exercised via docker
        if self._container_id:
            self._docker_exec(["stop", self._container_id], timeout=15)
            self._container_id = None
        self._bg_tasks.clear()

    def _result(
        self, command, exit_code, output, *, timed_out=False, truncated=False, error=None
    ) -> dict[str, Any]:
        result: dict[str, Any] = {
            "command": command,
            "cwd": "/workspace",
            "exit_code": exit_code,
            "output": output,
            "timed_out": timed_out,
            "truncated": truncated,
            "sandboxed": True,
        }
        if error:
            result["error"] = error
        return result


def docker_available() -> bool:
    """Check that the Docker CLI is on PATH and the daemon is reachable."""
    try:
        proc = subprocess.run(
            ["docker", "info", "--format", "{{.ServerVersion}}"],
            capture_output=True, text=True, timeout=_DOCKER_TIMEOUT,
            **UTF8_TEXT_IO,
        )
        return proc.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False
