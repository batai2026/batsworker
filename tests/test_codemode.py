"""Code mode（形态 B）单元测试：协议、审批门、上限、fail-closed、工具面坍缩。

全程不依赖 Docker：协议测试用本地 python 子进程扮演 guest（monkeypatch `_docker_cmd`），
exec 的 fail-closed/审批路径直接构造 runtime 验证。
"""

from __future__ import annotations

import sys

import pytest

from coworker.codemode import bridge
from coworker.codemode.bridge import CodeModeRuntime, run_guest
from coworker.codemode.exec_tool import exec_tool
from coworker.codemode.guest import guest_source
from coworker.engine import ApprovalOutcome
from coworker.tools.registry import ToolRegistry


def _registry_with(*funcs) -> ToolRegistry:
    reg = ToolRegistry()
    reg.register_all(list(funcs))
    return reg


def _allow_all():
    calls = []

    async def approve(req):
        calls.append(req)
        return ApprovalOutcome.ONCE

    approve.calls = calls
    return approve


def _deny_all():
    calls = []

    async def deny(req):
        calls.append(req)
        return ApprovalOutcome.DENY

    deny.calls = calls
    return deny


def _local_guest(monkeypatch):
    """让桥接协议跑在本地 python 子进程上（guest runner 是纯 stdlib）。"""

    def fake_cmd(image: str, runner: str) -> list[str]:
        return [sys.executable, "-c", runner]

    monkeypatch.setattr(bridge, "_docker_cmd", fake_cmd)


def add(a: int, b: int) -> int:
    """Add two numbers."""
    return a + b


def fail() -> None:
    """Always raises."""
    raise ValueError("boom")


@pytest.mark.asyncio
async def test_guest_protocol_end_to_end(monkeypatch):
    _local_guest(monkeypatch)
    rt = CodeModeRuntime(registry=_registry_with(add), approver=_allow_all())
    result = await run_guest(
        "total = tools.add(a=1, b=2)\nprint('total =', total)",
        runtime=rt,
        runner=guest_source(),
    )
    assert result["stdout"] == "total = 3\n"
    assert result["bridge_calls"] == 1
    assert result["sandboxed"] is True
    assert result["timed_out"] is False
    assert rt.approver.calls[0].tool_name == "add"
    assert rt.approver.calls[0].arguments == {"a": 1, "b": 2}


@pytest.mark.asyncio
async def test_bridge_denied_call_surfaces_as_guest_error(monkeypatch):
    _local_guest(monkeypatch)
    approver = _deny_all()
    rt = CodeModeRuntime(registry=_registry_with(add), approver=approver)
    result = await run_guest("print(tools.add(a=1, b=2))", runtime=rt, runner=guest_source())
    assert "denied by user" in result["stderr"]
    assert result["bridge_calls"] == 1


@pytest.mark.asyncio
async def test_bridge_unknown_tool_rejected_without_approver(monkeypatch):
    _local_guest(monkeypatch)
    approver = _allow_all()
    rt = CodeModeRuntime(registry=_registry_with(add), approver=approver)
    result = await run_guest("tools.nope(x=1)", runtime=rt, runner=guest_source())
    assert "unknown tool: nope" in result["stderr"]
    assert approver.calls == []  # 白名单在审批门之前


@pytest.mark.asyncio
async def test_bridge_call_limit(monkeypatch):
    _local_guest(monkeypatch)
    rt = CodeModeRuntime(registry=_registry_with(add), approver=_allow_all(), max_calls=2)
    code = (
        "for i in range(4):\n"
        "    try:\n"
        "        tools.add(a=i, b=i)\n"
        "    except Exception:\n"
        "        pass"
    )
    result = await run_guest(code, runtime=rt, runner=guest_source())
    assert result["bridge_calls"] == 4  # 超限的调用也计数（错误回给 guest 自决）
    assert len(rt.approver.calls) == 2  # 超限调用不再过审批


@pytest.mark.asyncio
async def test_bridge_tool_exception_returns_error(monkeypatch):
    _local_guest(monkeypatch)
    rt = CodeModeRuntime(registry=_registry_with(fail), approver=_allow_all())
    result = await run_guest("tools.fail()", runtime=rt, runner=guest_source())
    assert "ValueError: boom" in result["stderr"]


@pytest.mark.asyncio
async def test_exec_fails_closed_without_docker_executor(tmp_path):
    approver = _allow_all()
    rt = CodeModeRuntime(executor=object(), approver=approver)  # 非 DockerExecutor
    tool = exec_tool(rt)
    result = await tool("print('hi')")
    assert result["error"] == "sandbox_unavailable"
    assert result["sandboxed"] is False
    assert approver.calls == []  # fail-closed 发生在审批之前（无代码可批）


@pytest.mark.asyncio
async def test_exec_denied_runs_nothing(tmp_path, monkeypatch):
    from coworker.tools.sandbox import DockerExecutor

    rt = CodeModeRuntime(
        executor=DockerExecutor(workspace=tmp_path), approver=_deny_all()
    )
    called = []

    async def fake_run_guest(*args, **kwargs):
        called.append(True)
        return {}

    monkeypatch.setattr(sys.modules["coworker.codemode.exec_tool"], "run_guest", fake_run_guest)
    tool = exec_tool(rt)
    result = await tool("print('hi')")
    assert result["error"] == "denied"
    assert called == []  # 审批拒绝 → guest 从未启动
    req = rt.approver.calls[0]
    assert req.tool_name == "exec"
    assert req.arguments == {"code": "print('hi')"}  # 代码全文进审批预览


@pytest.mark.asyncio
async def test_exec_records_code_exec_trajectory(tmp_path, monkeypatch):
    from coworker.tools.sandbox import DockerExecutor

    records = []

    class _Tj:
        def record_code_exec(self, code, **fields):
            records.append((code, fields))

    rt = CodeModeRuntime(
        executor=DockerExecutor(workspace=tmp_path),
        approver=_allow_all(),
        trajectory=_Tj(),
    )

    async def fake_run_guest(code, *, runtime, runner):
        return {"sandboxed": True, "bridge_calls": 3, "exit_code": 0}

    monkeypatch.setattr(sys.modules["coworker.codemode.exec_tool"], "run_guest", fake_run_guest)
    tool = exec_tool(rt)
    await tool("print('hi')")
    (code, fields), = records
    assert code == "print('hi')"  # 代码全文进账本
    assert fields == {"sandboxed": True, "bridge_calls": 3, "exit_code": 0}


def test_signatures_rewrite_exec_description():
    def read_file(path: str) -> str:
        """Read a file with line numbers."""
        return path

    session_reg = _registry_with(add, read_file)
    rt = CodeModeRuntime(registry=_registry_with(add, read_file), approver=_allow_all())
    tool = exec_tool(rt)
    session_reg.register(tool)
    rt.bind(registry=session_reg, approver=rt.approver)
    desc = session_reg.get("exec").schema["function"]["description"]
    assert "Available code API:" in desc
    assert "add(a: int, b: int)" in desc
    assert "read_file(path: str)" in desc
    assert "exec(" not in desc.split("Available code API:")[1]  # 排除 exec 自身


def test_hidden_tools_form_private_bridge_whitelist(tmp_path):
    from coworker.agents.base import AgentContext
    from coworker.catalog import expand
    from coworker.tools.shell import LocalExecutor

    ctx = AgentContext(
        workspace=tmp_path, executor=LocalExecutor(cwd=str(tmp_path))
    )
    tools = expand(("code_mode",), ctx)
    assert [getattr(t, "__name__", "") for t in tools] == ["exec"]  # 模型面坍缩
    rt = ctx.codemode
    assert rt is not None
    hidden = {getattr(t, "__name__", "") for t in rt.hidden_tools}
    assert "read_file" in hidden  # 桥接可调但不对模型暴露
    assert "run_shell" in hidden
    session_reg = _registry_with(*tools)
    rt.bind(registry=session_reg, approver=_allow_all())
    assert rt.registry is not session_reg  # 私有白名单 registry
    assert rt.registry.get("read_file") is not None
    assert rt.registry.get("exec") is None  # exec 不可经桥接递归
