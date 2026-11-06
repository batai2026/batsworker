"""Vetted tool catalog — the stable ``id → capability`` layer a persona references.

A *capability* bundles a group of tools (the existing ``tools/`` factories) behind a stable
id, plus what session context it needs (``requires``) and the risk classes it can produce
(``risk``, used by the Phase 2 install-consent screen). ``expand(ids, context)`` turns a
persona's ``tools:`` list into concrete callables, skipping capabilities whose context
prerequisites aren't met (e.g. no shell without an executor) — matching the per-agent
factories that used to assemble tools by hand.

The catalog is **platform-owned and closed**: third parties get breadth from us adding
vetted capabilities here and from MCP, never by adding entries. MCP tools are *not* in the
catalog (see ``PERMISSIONS-AND-INBOX.md``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import aisuite as ai

from .agents.base import AgentContext
from .risk import RiskClass
from .tools.files import file_tools
from .tools.git import git_tools
from .tools.search import search_tools
from .tools.shell import shell_tools
from .tools.todo import todo_tools
from .tools.security import security_tools

# Context prerequisites a capability may require, mapped to a predicate over AgentContext.
_REQUIREMENTS: dict[str, Callable[[AgentContext], bool]] = {
    "workspace": lambda c: c.workspace is not None,
    "executor": lambda c: c.executor is not None,
    "todo": lambda c: c.todo is not None,
}


@dataclass(frozen=True)
class Capability:
    id: str
    name: str  # human label (consent screen)
    description: str
    build: Callable[[AgentContext], list]
    requires: tuple[str, ...] = ()
    risk: tuple[RiskClass, ...] = (RiskClass.READ,)

    def available(self, context: AgentContext) -> bool:
        return all(_REQUIREMENTS[r](context) for r in self.requires)


# -- capability builders --------------------------------------------------------
# These reproduce, exactly, what the Code and Cowork agent factories assembled by hand.


def _code_files(context: AgentContext) -> list:
    """Repo-oriented files: line-numbered/windowed `read_file`. Our `grep` and windowed
    `read_file` replace aisuite's slower `search_files` / `read_file`/`read_file_lines`.
    Multi-root aware (universal scratch): with session roots, writes/reads reach the
    scratch and granted dirs too; the workspace stays the relative-path anchor.
    When the Docker sandbox is active, ALL file ops route through the container.
    """
    ws = str(context.workspace)
    # Sandboxed sessions swap the host file tools for container-backed ones.
    try:
        from .tools.sandbox import DockerExecutor

        if isinstance(context.executor, DockerExecutor):
            from .tools.sandbox_files import sandboxed_file_tools

            return sandboxed_file_tools(context.executor, workspace=ws, roots=context.roots)
    except Exception:  # pragma: no cover - a broken sandbox must not block engine build
        pass
    replaced = {"search_files", "read_file", "read_file_lines"}
    file_kwargs = (
        {"roots": context.roots} if context.roots else {"root": ws, "allow_write": True}
    )
    files = [
        t
        for t in ai.toolkits.files(**file_kwargs)
        if getattr(t, "__name__", "") not in replaced
    ]
    return [*files, *file_tools(ws, roots=context.roots)]


def _files(context: AgentContext) -> list:
    """Knowledge-work files: multi-root aware (reads/writes across the session's roots).
    One reader everywhere (owner ruling 2026-08-20): the windowed, line-numbered
    `read_file` replaces aisuite's `read_file`/`read_file_lines`, and our `grep`
    replaces the slow `search_files` — same set Code uses.
    When the Docker sandbox is active, ALL file ops route through the container."""
    ws = str(context.workspace)
    try:
        from .tools.sandbox import DockerExecutor

        if isinstance(context.executor, DockerExecutor):
            from .tools.sandbox_files import sandboxed_file_tools

            return sandboxed_file_tools(context.executor, workspace=ws, roots=context.roots)
    except Exception:  # pragma: no cover - a broken sandbox must not block engine build
        pass
    file_kwargs = (
        {"roots": context.roots} if context.roots else {"root": ws, "allow_write": True}
    )
    replaced = {"search_files", "read_file", "read_file_lines"}
    files = [
        t
        for t in ai.toolkits.files(**file_kwargs)
        if getattr(t, "__name__", "") not in replaced
    ]
    return [*files, *file_tools(ws, roots=context.roots)]


def _git(context: AgentContext) -> list:
    ws = str(context.workspace)
    return [*ai.toolkits.git(root=ws), *git_tools(ws)]  # git_status, git_diff, git_log


def _search(context: AgentContext) -> list:
    return search_tools(str(context.workspace))  # grep (ripgrep, .gitignore-aware)


def _shell(context: AgentContext) -> list:
    return shell_tools(context.executor)  # run_shell + background task tools


def _code_mode(context: AgentContext) -> list:
    """Code mode: the model writes Python that runs in the sandbox; tools are called
    through the approval-gated stdio bridge. Fails closed without a DockerExecutor.

    Surface collapse: the session registry exposes only `exec`; the file/shell toolset
    is built into the runtime's private bridge whitelist (callable, never model-facing).
    """
    from .codemode import CodeModeRuntime, exec_tool

    if context.codemode is None:
        context.codemode = CodeModeRuntime(
            executor=context.executor,
            hidden_tools=expand(("code_files", "shell"), context),
        )
    return [exec_tool(context.codemode)]


def _todo(context: AgentContext) -> list:
    return todo_tools(context.todo)  # todo_write (drives the Progress panel)


def _security(context: AgentContext) -> list:
    """Security workflow: editable threat model + finding tracking loop."""
    from ..security import SecurityStore

    return security_tools(SecurityStore())


def _egress(context: AgentContext) -> list:
    """Egress policy: request a blocked network destination be allowlisted."""
    from .egress import default_policy
    from .tools.egress import request_egress_tool

    return request_egress_tool(default_policy())


_CAPS: list[Capability] = [
    Capability(
        id="code_files",
        name="Code files",
        description="Read & edit files in a single repo workspace (line-numbered reads).",
        build=_code_files,
        requires=("workspace",),
        risk=(RiskClass.READ, RiskClass.WRITE_LOCAL),
    ),
    Capability(
        id="files",
        name="Files",
        description="Read & edit files across the session's workspace folders.",
        build=_files,
        requires=("workspace",),
        risk=(RiskClass.READ, RiskClass.WRITE_LOCAL),
    ),
    Capability(
        id="git",
        name="Git",
        description="Inspect git state and history (status, diff, log).",
        build=_git,
        requires=("workspace",),
        risk=(RiskClass.READ,),
    ),
    Capability(
        id="search",
        name="Search",
        description="Fast code/content search (grep).",
        build=_search,
        requires=("workspace",),
        risk=(RiskClass.READ,),
    ),
    Capability(
        id="shell",
        name="Shell",
        description="Run shell commands in a persistent session.",
        build=_shell,
        requires=("executor",),
        risk=(RiskClass.EXEC,),
    ),
    Capability(
        id="todo",
        name="Task list",
        description="Maintain a visible task/progress list.",
        build=_todo,
        requires=("todo",),
        risk=(RiskClass.READ,),
    ),
    Capability(
        id="security",
        name="Security workflow",
        description="Editable threat model + security finding tracking (Claude-Security loop).",
        build=_security,
        requires=(),
        risk=(RiskClass.READ, RiskClass.WRITE_LOCAL),
    ),
    Capability(
        id="egress",
        name="Egress policy",
        description="Request blocked network destinations be added to the egress policy.",
        build=_egress,
        requires=(),
        risk=(RiskClass.EGRESS,),
    ),
    Capability(
        id="code_mode",
        name="Code mode",
        description="Model writes Python that orchestrates tools via an approval-gated "
        "bridge inside the sandbox (fails closed without Docker).",
        build=_code_mode,
        requires=("workspace",),
        risk=(RiskClass.EXEC, RiskClass.WRITE_LOCAL),
    ),
]

CATALOG: dict[str, Capability] = {c.id: c for c in _CAPS}


def capability(cap_id: str) -> Capability:
    cap = CATALOG.get(cap_id)
    if cap is None:
        raise KeyError(f"Unknown capability id: {cap_id!r}")
    return cap


def expand(ids: list[str], context: AgentContext) -> list:
    """Expand a persona's ``tools:`` id list into concrete tool callables for this context.
    Capabilities whose context prerequisites aren't met are skipped (no shell without an
    executor, no files without a workspace) — exactly like the old hand-written factories.
    """
    tools: list = []
    for cap_id in ids:
        cap = capability(cap_id)
        if cap.available(context):
            tools.extend(cap.build(context))
    return tools


def risk_summary(ids: list[str]) -> set[RiskClass]:
    """The union of risk classes a tool list can produce — for the install-consent screen."""
    out: set[RiskClass] = set()
    for cap_id in ids:
        out.update(capability(cap_id).risk)
    return out
