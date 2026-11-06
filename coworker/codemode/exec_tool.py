"""exec 工具：code-mode 会话对模型暴露的唯一工具。

模型写一个 Python 程序，程序内用 ``tools.<name>(**kwargs)`` 调工具（桥接过审批门）。
fail-closed：会话执行器不是 DockerExecutor 时直接返回 ``sandbox_unavailable``。
"""

from __future__ import annotations

from typing import Any

from ..engine import ApprovalOutcome, PermissionRequest
from .bridge import CodeModeRuntime, run_guest


def exec_tool(runtime: CodeModeRuntime):
    """构造绑定到 ``runtime`` 的 exec 工具（注册名 ``exec``）。"""

    async def run_model_code(code: str) -> dict[str, Any]:
        """Run ONE Python program in the code-mode sandbox. Orchestrate the whole task
        inside it: call `tools.<name>(**kwargs)` for file/shell work, and print() only
        what the user should see. Do the work in a single program instead of separate
        steps. If the program fails, stderr comes back — fix it and retry at most twice.
        """
        from ..tools.sandbox import DockerExecutor

        if not isinstance(runtime.executor, DockerExecutor):
            return {
                "error": "sandbox_unavailable",
                "reason": "code-mode exec requires the Docker sandbox; refusing to run "
                "model code on the host",
                "sandboxed": False,
            }
        runtime.image = runtime.executor.image
        outcome = await runtime.approver(
            PermissionRequest(
                tool_name="exec",
                arguments={"code": code},
                metadata=None,
                reason="run model-generated Python in the code-mode sandbox",
                tool_call_id=None,
            )
        )
        if outcome is ApprovalOutcome.DENY:
            return {"error": "denied", "reason": "denied by user", "sandboxed": True}
        result = await run_guest(code, runtime=runtime, runner="")
        tj = runtime.trajectory
        if tj is not None:
            try:
                tj.record_code_exec(
                    code,
                    sandboxed=bool(result.get("sandboxed")),
                    bridge_calls=int(result.get("bridge_calls", 0)),
                    exit_code=result.get("exit_code"),
                )
            except Exception:
                pass
        return result

    run_model_code.__name__ = "exec"
    return run_model_code
