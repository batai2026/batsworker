"""宿主侧桥接：服务 guest 进程的 stdio 协议，把每次 ``__call__`` 过审批门后执行。

- 进程级：``docker run --rm -i --network none --read-only``（沿用 DockerExecutor 的
  资源上限约定；不挂工作区——文件操作全部走桥接工具）。
- 审批：每个桥接调用构造 ``PermissionRequest`` 过该会话的 ``Approver``；``exec``
  代码本体由 exec 工具另行审批（preview 为全文）。
- 上限：单次 exec 桥接调用 ≤ ``MAX_CALLS``；整体 ``timeout`` 到点杀进程；
  stdout/stderr 各截 ``MAX_OUTPUT``。
- fail-closed：docker CLI 缺失或 daemon 不可达 → ``sandbox_unavailable``，
  绝不在宿主执行模型代码。
"""

from __future__ import annotations

import asyncio
import inspect
import json
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from ..engine import ApprovalOutcome, PermissionRequest

MAX_CALLS = 16
MAX_OUTPUT = 64 * 1024  # stdout / stderr 各自的截断上限（字节近似，按字符截）
DEFAULT_TIMEOUT = 60.0

_SANDBOX_UNAVAILABLE = "sandbox_unavailable"


@dataclass
class CodeModeRuntime:
    """exec 工具的运行时依赖；``bind`` 在引擎装配完成后注入 registry 与 approver。

    ``hidden_tools`` 非空时构成私有桥接白名单（工具面坍缩：模型只见 exec，
    文件/shell 只能经桥接调用）；为空则桥接白名单 = 会话 registry。
    """

    executor: Any = None
    hidden_tools: list = field(default_factory=list)
    registry: Any = None
    approver: Any = None
    trajectory: Any = None
    max_calls: int = MAX_CALLS
    timeout: float = DEFAULT_TIMEOUT
    image: str = "python:3.12-slim"

    def bind(self, *, registry: Any, approver: Any) -> None:
        if approver is None:
            from ..engine import _deny_all

            approver = _deny_all
        self.approver = approver
        if self.hidden_tools:
            from ..tools.registry import ToolRegistry

            priv = ToolRegistry()
            priv.register_all(self.hidden_tools)
            self.registry = priv
        else:
            self.registry = registry
        self._rewrite_exec_description(registry)

    # -- 工具面坍缩：exec 的 description 携带可用工具的紧凑签名 ----------------
    _PY_TYPES = {
        "integer": "int",
        "string": "str",
        "number": "float",
        "boolean": "bool",
        "array": "list",
        "object": "dict",
    }

    def signatures(self) -> str:
        reg = self.registry
        if reg is None:
            return ""
        lines: list[str] = []
        for name in sorted(reg.names()):
            if name == "exec":
                continue
            spec = reg.get(name)
            fn = (spec.schema or {}).get("function", {})
            params = (fn.get("parameters") or {}).get("properties") or {}
            required = set((fn.get("parameters") or {}).get("required") or [])
            args = ", ".join(
                f"{p}: {self._PY_TYPES.get((v or {}).get('type', ''), 'any')}"
                f"{'' if p in required else '?'}"
                for p, v in params.items()
            )
            doc = (fn.get("description") or "").strip().splitlines()
            summary = f"  # {doc[0][:80]}" if doc and doc[0] else ""
            lines.append(f"{name}({args}){summary}")
        return "\n".join(lines)

    def _rewrite_exec_description(self, session_registry: Any) -> None:
        spec = session_registry.get("exec") if session_registry else None
        if spec is None:
            return
        sigs = self.signatures()
        desc = spec.schema.get("function", {}).get("description", "")
        base = desc.split("\n\nAvailable code API:")[0]
        spec.schema["function"]["description"] = (
            f"{base}\n\nAvailable code API:\n{sigs}" if sigs else base
        )


def _docker_cmd(image: str, runner: str) -> list[str]:
    return [
        "docker", "run",
        "--rm",
        "-i",                    # stdin 承载协议
        "--network", "none",     # egress 零开放：桥接是唯一通道
        "--read-only",
        "--memory", "512m",
        "--cpus", "2",
        "-e", "PYTHONUNBUFFERED=1",
        "-e", "HOME=/tmp",
        image, "python", "-c", runner,
    ]


def _send(proc: subprocess.Popen, obj: dict[str, Any]) -> None:
    proc.stdin.write(json.dumps(obj, default=str) + "\n")
    proc.stdin.flush()


@dataclass
class _Pump:
    """后台线程持续 drain stderr，避免管道缓冲写满死锁。"""

    proc: subprocess.Popen
    chunks: list[str] = field(default_factory=list)

    def start(self) -> None:
        def drain() -> None:
            try:
                for line in self.proc.stderr:
                    self.chunks.append(line)
            # Pipe boundary: the child may exit mid-read; a closed/short stderr is not an error.
            except (OSError, ValueError):
                pass

        threading.Thread(target=drain, daemon=True).start()

    def text(self) -> str:
        return "".join(self.chunks)


async def run_guest(
    code: str,
    *,
    runtime: CodeModeRuntime,
    runner: str,
) -> dict[str, Any]:
    """异步入口：把阻塞协议循环放进 worker 线程，审批/异步工具回调进事件循环。"""
    loop = asyncio.get_running_loop()
    return await asyncio.to_thread(_run_guest_sync, code, runtime, runner, loop)


def _await_in_loop(coro: Any, loop: asyncio.AbstractEventLoop) -> Any:
    return asyncio.run_coroutine_threadsafe(coro, loop).result()


def _run_guest_sync(
    code: str,
    runtime: CodeModeRuntime,
    runner: str,
    loop: asyncio.AbstractEventLoop,
) -> dict[str, Any]:
    from .guest import guest_source  # 延迟：guest 与宿主解耦，便于单测替换 runner

    cmd = _docker_cmd(runtime.image, runner or guest_source())
    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
    except FileNotFoundError:
        return {
            "error": _SANDBOX_UNAVAILABLE,
            "reason": "docker CLI not found",
            "sandboxed": False,
        }

    stderr_pump = _Pump(proc)
    stderr_pump.start()
    timed_out = False

    def watchdog() -> None:
        nonlocal timed_out
        deadline = time.monotonic() + runtime.timeout
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                return
            time.sleep(0.05)
        if proc.poll() is None:
            timed_out = True
            proc.kill()

    threading.Thread(target=watchdog, daemon=True).start()

    calls = 0
    envelope: Optional[dict[str, Any]] = None
    try:
        _send(proc, {"__code__": code})
        while True:
            line = proc.stdout.readline()
            if not line:  # EOF：guest 退出或被杀
                break
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue  # 非协议行（不应出现：guest print 已被捕获）——丢弃
            if "__result__" in msg:
                envelope = msg["__result__"]
                break
            call = msg.get("__call__")
            if call:
                calls += 1
                _send(proc, _handle_call(call, runtime, loop, calls))
    finally:
        try:
            proc.stdin.close()
        except (OSError, ValueError):
            pass
        try:
            proc.wait(timeout=5)
        # The daemon may already be gone (OSError) or refuse to exit in time
        # (TimeoutExpired is a SubprocessError) — either way we escalate to kill().
        except (OSError, subprocess.SubprocessError):
            proc.kill()

    stderr_text = stderr_pump.text()
    if not timed_out and _daemon_unreachable(stderr_text, proc.returncode):
        return {
            "error": _SANDBOX_UNAVAILABLE,
            "reason": "docker daemon unreachable",
            "sandboxed": False,
        }
    out = {
        "stdout": (envelope or {}).get("stdout", "")[:MAX_OUTPUT],
        "stderr": ((envelope or {}).get("error") or stderr_text)[:MAX_OUTPUT],
        "exit_code": proc.returncode,
        "bridge_calls": calls,
        "timed_out": timed_out,
        "sandboxed": True,
    }
    if envelope is None and not out["stderr"]:
        out["stderr"] = "guest exited without a result envelope"
    return out


def _daemon_unreachable(stderr: str, returncode: Optional[int]) -> bool:
    return bool(returncode) and "cannot connect to the docker daemon" in stderr.lower()


def _handle_call(
    call: dict[str, Any],
    runtime: CodeModeRuntime,
    loop: asyncio.AbstractEventLoop,
    seq: int,
) -> dict[str, Any]:
    name = str(call.get("name", ""))
    args = call.get("args") or {}
    reg = runtime.registry
    if reg is None or reg.get(name) is None or name == "exec":
        return {"ok": False, "error": f"unknown tool: {name}"}
    if seq > runtime.max_calls:
        return {"ok": False, "error": "call_limit"}
    outcome = _await_in_loop(
        runtime.approver(
            PermissionRequest(
                tool_name=name,
                arguments=args,
                metadata=reg.get(name).metadata,
                reason=f"code-mode exec bridge call #{seq}",
                tool_call_id=f"codemode-{seq}",
            )
        ),
        loop,
    )
    if outcome is ApprovalOutcome.DENY:
        _traj(runtime, "tool_call", name=name, bridge=True, approved=False)
        return {"ok": False, "error": "denied by user"}
    _traj(runtime, "tool_call", name=name, bridge=True, approved=True)
    try:
        result = reg.execute(name, args)
        if inspect.isawaitable(result):
            result = _await_in_loop(result, loop)
    # Registry boundary: tool implementations are arbitrary code (subprocesses, HTTP, model
    # calls). Any failure becomes this call's error payload — that is how the sandboxed script
    # learns the tool failed instead of dying with it.
    except Exception as exc:
        _traj(runtime, "tool_result", name=name, bridge=True, error=str(exc)[:200])
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    _traj(runtime, "tool_result", name=name, bridge=True, ok=True)
    try:
        json.dumps(result, default=str)
    except (TypeError, ValueError):
        result = str(result)
    return {"ok": True, "result": result}


def _traj(runtime: CodeModeRuntime, kind: str, **data: Any) -> None:
    tj = runtime.trajectory
    if tj is None:
        return
    try:
        tj.record(kind, **data)
    except Exception:
        pass  # 账本绝不影响执行
