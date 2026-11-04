# Design: sandboxed file tools (sandbox v2, last item)

> Status: **core implemented** (`read_file` / `write_file` / `list_files`);
> `apply_patch` / `replace_in_file` and multi-root mounts are deferred.
>
> Source: `coworker/tools/sandbox.py` (`DockerExecutor` file channel),
> `coworker/tools/sandbox_files.py` (`sandboxed_file_tools`),
> `coworker/catalog.py` (`_files` / `_code_files` swap host → sandboxed tools),
> tests in `tests/test_security_workflow.py`.

## Why

The sandbox currently covers **shell commands only**. File tools
(`read_file`, `write_file`, `apply_patch`, `replace_in_file`, `list_files`, …)
operate directly on the host filesystem, scoped only by the permission engine's
path/root checks. That is a *soft* layer: a compromised model could read or
write any path the engine grants (writable roots), and file writes are not
network-isolated or OS-isolated.

## Goal

When `sandbox_enabled` is on, file tools should run **inside the same container**
as shell commands: the workspace is mounted at `/workspace`, root FS is
read-only, and no network. Reads/writes outside the workspace mount would fail
naturally (container filesystem), which is a *hard* boundary on top of the
permission engine's soft root scoping.

## Approach

Two layers need changing:

1. **Executor boundary** — add a file-ops channel to `DockerExecutor`:
   - `read_file(path) -> str` → `docker exec <cid> cat /workspace/<rel>` (with
     host→container path mapping).
   - `write_file(path, content)` → `docker exec <cid> bash -c 'cat > /workspace/<rel>'`
     with the content piped on stdin (read-only root makes `/workspace` the only
     writable path).
   - `list_files(path)` → `docker exec <cid> find /workspace/<rel> ...`.
   - `apply_patch` / `replace_in_file` → run `patch`/`python` inside the container.
   - Path mapping: only paths under the workspace are mappable; anything outside
     is refused (`path escapes the sandbox`).

2. **Tool factory swap** — `catalog.py`'s `_files` / `_code_files` builders
   currently return host-side tools. Introduce a `SandboxedFiles` variant that
   produces the same tool names/schemas but executes via the `DockerExecutor`
   file channel. Selection rule: if `sandbox_enabled && docker_available` →
   sandboxed files; else → current host tools. This mirrors how
   `build_engine` already selects `DockerExecutor` vs `LocalExecutor`.

## Key decisions / tradeoffs

- **Same tool surface**: tool names, argument schemas, and approval metadata stay
  identical, so the engine, permissions, and GUI need no changes.
- **Workspace-only**: only the mounted workspace is addressable. Scratch/granted
  roots outside the workspace must be mounted as extra `-v` mounts (like
  `extra_mounts`) or the sandboxed-files layer must refuse them with a clear
  error asking the user to add the root as a mount.
- **Symlinks / traversal**: resolve paths inside the container; a symlink pointing
  outside `/workspace` is refused (same guard as `SkillStore._folder_of`).
- **Performance**: each file op is a `docker exec` round-trip; acceptable for
  agent-scale file IO, but large reads should be capped (reuse `max_output_chars`).
- **Fallback**: if Docker disappears mid-session, file tools must fail loudly
  (like the shell fallback) or fall back to host tools with `sandboxed: false` —
  the latter reuses the per-command probe pattern from `DockerExecutor.run`.

## What stays out of scope / deferred

- `apply_patch` / `replace_in_file` still run host-side (deferred; they need a
  `patch`/`python` runner inside the container).
- Multi-root sessions: only the mounted workspace is addressable; scratch/granted
  roots outside the workspace need extra `-v` mounts (deferred).
- Network egress of `web_fetch`/connectors is governed by the permission system,
  not the sandbox.
- The sandbox does not replace the permission engine — it hardens the boundary
  for paths that ARE granted.

## Implementation status

1. ✅ `DockerExecutor` file channel: `read_workspace_file` / `write_workspace_file`
   / `list_workspace_dir` with `_safe_rel` traversal guard and `_docker_exec`
   stdin support.
2. ✅ `sandboxed_file_tools` in `coworker/tools/sandbox_files.py` — host-compatible
   `read_file` (windowed, numbered) / `write_file` / `replace_in_file` /
   `apply_patch` (minimal unified-diff applier in `coworker/tools/unified_diff.py`) /
   `delete_file` / `list_files`.
3. ✅ `catalog.py` `_files` / `_code_files` swap to sandboxed tools when
   `context.executor` is a `DockerExecutor`.
4. ✅ Tests: `test_docker_executor_file_channel` (mocked docker, traversal guard),
   `test_sandboxed_file_tools_roundtrip`, `test_sandboxed_file_tools_host_fallback`,
   plus the existing real-Docker test.
5. ✅ **Host fallback**: when the container is unreachable mid-session, the
   sandboxed file tools fall back to the host workspace and mark results
   `sandboxed: false` + `fallback_reason` (same honesty as the shell probe).
6. ✅ **Multi-root mounts**: session roots are registered as extra container
   mounts (`/roots/N` via `DockerExecutor.add_mount`) and absolute host paths under
   them are mapped into the container (`read_container_file` etc.); paths
   outside every root are refused.
7. ✅ `apply_patch` — a minimal unified-diff applier
   (`coworker/tools/unified_diff.py`) patches the container-read content and writes
   it back; mismatches fail loudly. (`apply_unified_diff` from the aisuite toolkit
   is not mirrored — the sandbox tool covers the same primitive.)