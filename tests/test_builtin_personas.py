"""Phase 1 gate — built-in personas resolve to the same toolsets as the legacy agents.

The equivalence net: routing Code/Cowork through the persona registry must yield the exact
same tools the agent builders produce, and Ops (a markdown persona) must compose the knowledge
toolset. Ties back to the Phase 0 catalog equivalence."""

from __future__ import annotations

from coworker.agents.base import AgentContext
from coworker.agents.code import code_agent
from coworker.agents.cowork import cowork_agent
from coworker.personas.registry import PersonaRegistry
from coworker.tools.todo import TodoList


def _ctx(tmp_path) -> AgentContext:
    return AgentContext(workspace=tmp_path, executor=object(), todo=TodoList())


def _names(agent, ctx) -> set:
    return {getattr(t, "__name__", "") for t in agent.build_tools(ctx)}


def test_code_persona_matches_builder(tmp_path):
    reg = PersonaRegistry()
    ctx = _ctx(tmp_path)
    assert _names(reg.agent("code"), ctx) == _names(code_agent(), ctx)
    assert reg.agent("code").requires_folder and reg.agent("code").subagents


def test_cowork_persona_matches_builder(tmp_path):
    reg = PersonaRegistry()
    ctx = _ctx(tmp_path)
    assert _names(reg.agent("cowork"), ctx) == _names(cowork_agent(), ctx)
    a = reg.agent("cowork")
    assert a.messaging and a.connectors


def test_ops_persona_composes_knowledge_toolset(tmp_path):
    reg = PersonaRegistry()
    ctx = _ctx(tmp_path)
    # Ops uses the same capability list as Cowork (files/search/shell/todo).
    assert _names(reg.agent("ops"), ctx) == _names(cowork_agent(), ctx)
    a = reg.agent("ops")
    assert not a.requires_folder and a.scheduling and a.messaging and a.connectors
    assert "read_file" in _names(a, ctx)  # windowed reader, multi-root aware


def test_code_keeps_single_root_file_tools(tmp_path):
    reg = PersonaRegistry()
    names = _names(reg.agent("code"), _ctx(tmp_path))
    assert "read_file" in names and "read_file_lines" not in names
    assert "git_log" in names  # code has git; cowork/ops do not


def test_minimal_preset_is_files_and_shell_only(tmp_path):
    """P1-4：minimal 是模型基准用的最小 harness — 不含 git/search/todo。"""
    reg = PersonaRegistry()
    a = reg.agent("minimal")
    names = _names(a, _ctx(tmp_path))
    assert "read_file" in names and "run_shell" in names
    assert "git_log" not in names and "grep" not in names
    assert a.requires_folder and not a.subagents and not a.scheduling
    assert not a.messaging and not a.connectors


def test_creator_preset_has_todo_and_search(tmp_path):
    """P1-4：creator 需要检索能力槽与待办来创作/调试插件。"""
    reg = PersonaRegistry()
    a = reg.agent("creator")
    names = _names(a, _ctx(tmp_path))
    assert {"read_file", "grep", "run_shell"} <= names
    assert a.requires_folder and a.subagents and not a.scheduling


def test_code_mode_preset_collapses_to_exec(tmp_path):
    """形态 B：code-mode 模型面只有 exec；文件/shell 坍缩进桥接白名单。"""
    reg = PersonaRegistry()
    a = reg.agent("code-mode")
    ctx = _ctx(tmp_path)
    assert _names(a, ctx) == {"exec"}
    assert a.requires_folder and not a.subagents and not a.scheduling
    assert not a.messaging and not a.connectors
    hidden = {getattr(t, "__name__", "") for t in ctx.codemode.hidden_tools}
    assert {"read_file", "write_file", "run_shell"} <= hidden

