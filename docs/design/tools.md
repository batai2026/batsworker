# Tools

> Source: `coworker/tools/`, `coworker/catalog.py`, `coworker/toolchain.py`,
> plus tool packages `coworker/web/`, `coworker/connectors/`, `coworker/mcp/`.

## Registry

`ToolRegistry` (`tools/registry.py`) holds late-bound tool callables with
OpenAI function schemas generated via `aisuite.utils.tools`. One rule is
stated in the module's opening comment: **the registry never does permission
checks.** It executes; the engine decides — on every call, before execution
(see [permissions-and-approvals.md](permissions-and-approvals.md)). Tools are
wired as live settings getters, not baked snapshots, so config changes apply
to the next call.

## Built-ins (`coworker/tools/`)

| File | Tools | Notes |
|---|---|---|
| `shell.py` | `run_shell`, `shell_task_output`, `kill` (`interrupt_now` via hooks) | Persistent OS-native shell REPL — bash or PowerShell — behind an `Executor` ABC explicitly hedged for a future `ContainerExecutor` sandbox. Timeouts + background task handles. |
| `files.py` | `read_file`, `write_file`, `replace_in_file`, `apply_patch`, `apply_unified_diff` | `read_file` is windowed and line-numbered — *one reader everywhere* (owner ruling 2026-08-20, `catalog.py:76`): the whole stack shares it instead of each tool re-reading. |
| `search.py` | `grep` | ripgrep with a pure-Python fallback. |
| `git.py` | `git_log` + status/diff | Status/diff delegate to aisuite toolkits. |
| `todo.py` | `todo_write` | Session task list. |
| `ask.py` | `ask_user` | Gate tool (engine-handled). Supports grouped questions (OPE-51): up to 4 `{question, header, options, allow_text, multi}` per card, because one button row can't answer 2+ questions. |
| `plan.py` | `propose_plan` | Gate tool; the target of PLAN mode. |
| `directories.py` | `request_directory` | Gate tool: agent asks the user to grant a folder root. |
| `toolreq.py` | `request_tool` | Gate tool: install a missing CLI tool — **only from a pinned, digest-verified catalog** (OPE-85: fetching an executable is a supply-chain decision, never auto-run). |
| `subagent.py` | `explore` | Read-only subagent fan-out for broad searches. |

## Web tools (`coworker/web/`)

- `web_search` — provider-configurable; keyless DuckDuckGo (`ddgs`) is the
  default, Tavily/Brave via `httpx` when configured.
- `web_fetch` — with `guard.py`, the egress guard that keeps fetches inside
  the permission system (risk class EGRESS; see
  [permissions-and-approvals.md](permissions-and-approvals.md)).

## Extension planes

- **Connector tools** (`connectors/tools.py` + per-connector modules):
  outbound `send_message` for Slack/Telegram/email via stateless senders
  (`senders.py::DEFAULT_SENDERS`); plus Playwright browser automation
  (optional `browser` dependency) with root-scoped uploads (OPE-122) and
  redirect re-checking (OPE-124).
- **MCP tools**: every remote tool is bridged as `mcp__<server>__<tool>` —
  see [mcp-client.md](mcp-client.md).
- **Memory tools** (`memory/tools.py`): `remember`, `memory_update`,
  `memory_forget`, `memory_read` — see [memory.md](memory.md).
- **Skills** (`skills/`): `load_skill` / `save_skill` — see
  [personas-and-skills.md](personas-and-skills.md).
- **Automation tools** (`automation/tools.py`): scheduled-task CRUD — a
  `PERSISTENT_AUTHORITY_TOOLS` member, always human-approved.
- **Teams tools** (`teams/tools.py`): board/journal verbs, role-filtered —
  see [teams.md](teams.md).

## Capability catalog

`coworker/catalog.py` holds `CATALOG`, a declarative set of named
capabilities, and `expand()` turns a persona/agent's declared names into the
concrete tool set. Personas never list raw functions; they compose catalog
entries, which keeps "what can this persona touch" auditable in one place.

## Toolchain resolution

`coworker/toolchain.py` keeps two problems deliberately apart (OPE-82):

1. **Finding** binaries — PATH resolution using the login shell's environment
   (the desktop shell hands it over at spawn, OPE-83), so a GUI-launched
   server still sees the user's `~/bin`, homebrew, etc.
2. **Installing** binaries — always behind `request_tool` + human approval +
   digest pinning (OPE-85). The distinction between resolution and
   installation is the supply-chain boundary.

There is **no OS-level sandbox** in 0.2.1. Safety is layered instead:
permission gating on every call, root-scoped paths, timeouts, non-interactive
shell environment, and the hard floors in
[permissions-and-approvals.md](permissions-and-approvals.md). The `Executor`
ABC in `shell.py` is the designated seam for a future `ContainerExecutor`.
