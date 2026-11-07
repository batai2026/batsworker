"""OpenShell-backed sandbox executor (P0 — v0.3.1 gap, real CLI wired).

OpenShell is NVIDIA's sandbox runtime (https://github.com/NVIDIA/OpenShell). Its
real CLI is sandbox-oriented rather than one-shot: commands execute inside a
named sandbox that must be created first and deleted afterwards:

- ``openshell sandbox create --name <n> --from <image> --upload <host>:/workspace
  [--policy <policy.yaml>] --detach`` — provisions the sandbox (Ready phase).
- ``openshell sandbox exec -n <n> [--workdir /workspace] -- <command>`` — runs one
  command inside it.
- ``openshell sandbox delete <n>`` — tears it down (positional name).

This executor owns that lifecycle:

- The exec argv prefix is configurable via ``sandbox_openshell_prefix`` (a JSON
  list in config.toml). It defaults to the real CLI above. Tokens may reference
  ``{workspace}`` / ``{image}`` / ``{network}`` / ``{sandbox}``; they are expanded
  at construction so a site's real CLI flags can mount the workspace, pick the
  image, isolate the network and address the managed sandbox without hardcoding.
- The sandbox is created lazily on the first command and deleted on ``close()``.
  Readiness is probed by re-running ``true`` through the exec prefix (the CLI
  reports not-ready errors until the sandbox leaves Provisioning).
- Isolation defaults come from an embedded policy (no network rules = egress
  denied, root FS read-only, only /workspace + /tmp writable). A site can point
  ``sandbox_openshell_policy`` at its own YAML to override it.
- Availability is probed with ``<prefix[0]> --version`` at first use (cached), so
  a machine without OpenShell never wedges the agent. When unavailable (or the
  CLI vanishes mid-session, or create fails), commands fall back to LocalExecutor
  with ``sandboxed: false`` + ``fallback_reason``, like the Docker v2 probe.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, Optional

from .shell import Executor, LocalExecutor, _DEFAULT_TIMEOUT, _MAX_TIMEOUT, _NONINTERACTIVE_ENV

_DEFAULT_PREFIX = [
    "openshell", "sandbox", "exec", "-n", "{sandbox}",
    "--workdir", "/workspace", "--",
]
_PROBE_TIMEOUT = 3.0
_CREATE_TIMEOUT = 60.0
_READY_TIMEOUT = 30.0
_READY_INTERVAL = 0.5

# Baseline isolation for the managed sandbox: Landlock hides everything not
# listed, the root FS mounts read-only except /workspace + /tmp, and an empty
# network_policies map denies egress. Sites override via sandbox_openshell_policy.
_DEFAULT_POLICY = """\
version: 1
filesystem_policy:
  include_workdir: false
  read_only: [/usr, /lib, /lib64, /etc, /bin, /sbin]
  read_write: [/workspace, /tmp]
landlock:
  compatibility: hard_requirement
process:
  run_as_user: sandbox
network_policies: {}
"""


def _parse_prefix(raw: Optional[str]) -> list[str]:
    """Parse the configured OpenShell argv prefix (JSON list), with a sane fallback."""
    if not raw:
        return list(_DEFAULT_PREFIX)
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list) and parsed and all(isinstance(x, str) for x in parsed):
            return parsed
    except (ValueError, TypeError):
        pass
    return list(_DEFAULT_PREFIX)


def expand_prefix(
    prefix: list[str], *, workspace: str, image: str, network: str, sandbox: str = ""
) -> list[str]:
    """Render ``{workspace}`` / ``{image}`` / ``{network}`` / ``{sandbox}`` tokens.

    Plain literal replacement (not ``str.format``): unknown braces such as ``{gpu}``
    stay untouched instead of raising, and placeholders may sit inside a token
    (``-n{sandbox}``).
    """
    values = {
        "{workspace}": workspace,
        "{image}": image,
        "{network}": network,
        "{sandbox}": sandbox,
    }
    rendered: list[str] = []
    for token in prefix:
        for key, value in values.items():
            token = token.replace(key, value)
        rendered.append(token)
    return rendered


class OpenShellExecutor(Executor):
    """Runs shell commands inside an OpenShell sandbox (configurable CLI prefix).

    Owns the sandbox lifecycle: created lazily on the first command, deleted on
    ``close()``. One sandbox serves every command of the session, so the mounted
    workspace persists between calls.
    """

    def __init__(
        self,
        *,
        workspace: str | Path,
        image: str = "python:3.12-slim",
        network: str = "none",
        prefix: Optional[str | list[str]] = None,
        policy: Optional[str] = None,
        default_timeout: float = _DEFAULT_TIMEOUT,
        max_output_chars: int = 20_000,
    ) -> None:
        self.workspace = str(Path(workspace).expanduser().resolve())
        self.image = image
        self.network = network
        self.policy = policy or ""
        digest = hashlib.md5(self.workspace.encode()).hexdigest()[:6]
        self.sandbox_name = f"coworker-{os.getpid()}-{digest}"
        self.prefix = (
            list(prefix)
            if isinstance(prefix, list)
            else _parse_prefix(prefix if isinstance(prefix, str) else None)
        )
        self.argv_prefix = expand_prefix(
            self.prefix,
            workspace=self.workspace,
            image=self.image,
            network=self.network,
            sandbox=self.sandbox_name,
        )
        self.default_timeout = default_timeout
        self.max_output_chars = max_output_chars
        self._fallback: Optional[LocalExecutor] = None
        self._probe_cache: Optional[bool] = None
        self._probe_at: float = 0.0
        self._sandbox_ready = False
        self._sandbox_error: Optional[str] = None
        self._policy_path: Optional[str] = None

    # -- availability ----------------------------------------------------------

    def _available(self) -> bool:
        """Cached probe: is the OpenShell CLI on PATH and responsive?"""
        now = time.monotonic()
        if self._probe_cache is not None and now - self._probe_at < 30:
            return self._probe_cache
        exe = self.argv_prefix[0]
        if shutil.which(exe) is None:
            self._probe_cache = False
            self._probe_at = now
            return False
        try:
            proc = subprocess.run(
                [exe, "--version"],
                capture_output=True,
                text=True,
                timeout=_PROBE_TIMEOUT,
                env={**os.environ, **_NONINTERACTIVE_ENV},
            )
            ok = proc.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            ok = False
        self._probe_cache = ok
        self._probe_at = now
        return ok

    # -- sandbox lifecycle -----------------------------------------------------

    def _resolve_policy(self) -> Optional[str]:
        """Return the policy YAML path for create: the configured one, or the
        embedded baseline written to a temp file (created once, reused)."""
        if self.policy:
            return self.policy
        if self._policy_path is None:
            digest = hashlib.md5(_DEFAULT_POLICY.encode()).hexdigest()[:8]
            path = Path(tempfile.gettempdir()) / f"coworker-openshell-policy-{digest}.yaml"
            if not path.exists() or path.read_text(encoding="utf-8") != _DEFAULT_POLICY:
                path.write_text(_DEFAULT_POLICY, encoding="utf-8")
            self._policy_path = str(path)
        return self._policy_path

    def _create_argv(self) -> list[str]:
        return [
            "openshell", "sandbox", "create",
            "--name", self.sandbox_name,
            "--from", self.image,
            "--upload", f"{self.workspace}:/workspace",
            "--policy", self._resolve_policy() or "",
            "--detach",
        ]

    def _ensure_sandbox(self) -> bool:
        """Create the managed sandbox on first use and wait for Ready. False (with
        ``_sandbox_error`` set) means the caller should fall back locally."""
        if self._sandbox_ready:
            return True
        if self._sandbox_error is not None:
            return False
        try:
            proc = subprocess.run(
                self._create_argv(),
                capture_output=True,
                text=True,
                timeout=_CREATE_TIMEOUT,
                env={**os.environ, **_NONINTERACTIVE_ENV},
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            self._sandbox_error = f"OpenShell sandbox create failed: {exc}"
            return False
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip()
            self._sandbox_error = f"OpenShell sandbox create failed: {detail}"
            return False
        # The sandbox passes Provisioning -> Ready; exec fails until then, so
        # probe readiness by running a no-op through the real exec argv.
        deadline = time.monotonic() + _READY_TIMEOUT
        while True:
            try:
                probe = subprocess.run(
                    [*self.argv_prefix, "true"],
                    capture_output=True,
                    text=True,
                    timeout=_PROBE_TIMEOUT + 2,
                    env={**os.environ, **_NONINTERACTIVE_ENV},
                )
                if probe.returncode == 0:
                    self._sandbox_ready = True
                    return True
            except (OSError, subprocess.TimeoutExpired):
                pass
            if time.monotonic() >= deadline:
                self._sandbox_error = "OpenShell sandbox never became Ready"
                return False
            time.sleep(_READY_INTERVAL)

    def close(self) -> None:
        """Best-effort teardown of the managed sandbox (never raises)."""
        if not self._sandbox_ready and self._sandbox_error is None:
            pass  # never created
        else:
            try:
                subprocess.run(
                    ["openshell", "sandbox", "delete", self.sandbox_name],
                    capture_output=True,
                    text=True,
                    timeout=_PROBE_TIMEOUT + 5,
                    env={**os.environ, **_NONINTERACTIVE_ENV},
                )
            except (OSError, subprocess.TimeoutExpired):
                pass
        self._sandbox_ready = False
        if self._fallback is not None:
            self._fallback.close()

    # -- result plumbing (same contract as DockerExecutor) ---------------------

    def _result(
        self,
        command: str,
        exit_code: Optional[int],
        output: str,
        *,
        timed_out: bool = False,
        truncated: bool = False,
        error: Optional[str] = None,
    ) -> dict[str, Any]:
        result: Dict[str, Any] = {
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

    def _ensure_fallback(self) -> LocalExecutor:
        if self._fallback is None:
            self._fallback = LocalExecutor(
                cwd=self.workspace,
                default_timeout=self.default_timeout,
                max_output_chars=self.max_output_chars,
            )
        return self._fallback

    def _fallback_result(self, command: str, timeout: float, reason: str) -> dict[str, Any]:
        res = self._ensure_fallback().run(command, timeout=timeout)
        res["sandboxed"] = False
        res["fallback_reason"] = reason
        return res

    # -- Executor ----------------------------------------------------------------

    def run(self, command: str, timeout: Optional[float] = None) -> dict[str, Any]:
        timeout = min(timeout or self.default_timeout, _MAX_TIMEOUT)
        if not self._available():
            return self._fallback_result(
                command,
                timeout,
                "OpenShell CLI not found — ran locally (sandbox unavailable)",
            )
        if not self._ensure_sandbox():
            return self._fallback_result(
                command,
                timeout,
                f"{self._sandbox_error} — ran locally (sandbox unavailable)",
            )
        cmd = [*self.argv_prefix, command]
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                env={**os.environ, **_NONINTERACTIVE_ENV},
            )
        except subprocess.TimeoutExpired:
            return self._result(command, None, "", timed_out=True)
        except FileNotFoundError:
            # CLI vanished after the probe (uninstalled / PATH changed).
            self._probe_cache = False
            self._sandbox_ready = False
            return self._fallback_result(
                command,
                timeout,
                "OpenShell CLI disappeared — ran locally (sandbox unavailable)",
            )

        output = proc.stdout or ""
        if proc.stderr:
            output += "\n[stderr]\n" + proc.stderr
        truncated = False
        if len(output) > self.max_output_chars:
            output = output[: self.max_output_chars]
            truncated = True
        return self._result(
            command,
            proc.returncode,
            output,
            truncated=truncated,
        )


def openshell_available() -> bool:
    """Best-effort check that the default OpenShell CLI is on PATH (for build-time
    selection and tests that want to skip when OpenShell isn't installed)."""
    try:
        return (
            shutil.which(_DEFAULT_PREFIX[0]) is not None
            and subprocess.run(
                [_DEFAULT_PREFIX[0], "--version"],
                capture_output=True,
                text=True,
                timeout=_PROBE_TIMEOUT,
            ).returncode
            == 0
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
