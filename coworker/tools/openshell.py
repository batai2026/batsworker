"""OpenShell-backed sandbox executor (P0 — v0.3.1 gap scaffolding).

OpenShell is NVIDIA's container sandbox; the exact CLI is site-deployed and varies
between installations. This executor makes the integration point explicit and
testable without the real CLI:

- The argv prefix is configurable via ``sandbox_openshell_prefix`` (a JSON list in
  config.toml, overridable by the GUI sandbox settings / env). It defaults to
  ``["openshell", "exec", "--"]`` — everything up to the command placeholder.
  Tokens may reference ``{workspace}`` / ``{image}`` / ``{network}``; they are
  expanded at construction so a site's real CLI flags can mount the workspace,
  pick the image and isolate the network without hardcoding paths here.
- Availability is probed with ``<prefix[0]> --version`` at first use (cached), so a
  machine without OpenShell never wedges the agent.
- Each command runs as ``<prefix> <command>`` with the same non-interactive env and
  hard timeout as DockerExecutor; the workspace is mounted read-write, network is
  isolated, root FS is read-only (all expressed by the prefix owner's CLI flags).
- When OpenShell is unavailable (or the CLI vanishes mid-session), it falls back
  to LocalExecutor and marks the result ``sandboxed: false`` + ``fallback_reason``,
  exactly like the Docker v2 per-command probe.

NOTE: the real OpenShell CLI flags belong in ``sandbox_openshell_prefix``; the
fallback/selection/result plumbing here is complete and unit-tested."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, Optional

from .shell import Executor, LocalExecutor, _DEFAULT_TIMEOUT, _MAX_TIMEOUT, _NONINTERACTIVE_ENV

_DEFAULT_PREFIX = ["openshell", "exec", "--"]
_PROBE_TIMEOUT = 3.0


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


def expand_prefix(prefix: list[str], *, workspace: str, image: str, network: str) -> list[str]:
    """Render ``{workspace}`` / ``{image}`` / ``{network}`` inside each argv token.

    Plain literal replacement (not ``str.format``): unknown braces such as ``{gpu}``
    stay untouched instead of raising, and placeholders may sit inside a token
    (``--mount={workspace}:/workspace``).
    """
    values = {"{workspace}": workspace, "{image}": image, "{network}": network}
    rendered: list[str] = []
    for token in prefix:
        for key, value in values.items():
            token = token.replace(key, value)
        rendered.append(token)
    return rendered


class OpenShellExecutor(Executor):
    """Runs shell commands inside an OpenShell sandbox (configurable CLI prefix)."""

    def __init__(
        self,
        *,
        workspace: str | Path,
        image: str = "python:3.12-slim",
        network: str = "none",
        prefix: Optional[str | list[str]] = None,
        default_timeout: float = _DEFAULT_TIMEOUT,
        max_output_chars: int = 20_000,
    ) -> None:
        self.workspace = str(Path(workspace).expanduser().resolve())
        self.image = image
        self.network = network
        self.prefix = (
            list(prefix)
            if isinstance(prefix, list)
            else _parse_prefix(prefix if isinstance(prefix, str) else None)
        )
        self.argv_prefix = expand_prefix(
            self.prefix, workspace=self.workspace, image=self.image, network=self.network
        )
        self.default_timeout = default_timeout
        self.max_output_chars = max_output_chars
        self._fallback: Optional[LocalExecutor] = None
        self._probe_cache: Optional[bool] = None
        self._probe_at: float = 0.0

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