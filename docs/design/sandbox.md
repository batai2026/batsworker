# Sandbox executor (Docker-backed ContainerExecutor)

> Source: `coworker/tools/sandbox.py` (DockerExecutor + docker_available),
> wired in `coworker/agent.py::build_engine` (executor selection),
> `coworker/config.py` (sandbox fields).

The `Executor` ABC in `tools/shell.py` was always the hedge for sandboxing —
`DockerExecutor` realizes it. When enabled, shell commands run inside a Docker
container with **no network**, a **read-only root filesystem**, and the
workspace as the only writable mount. Since v2, the core file tools
(read/write/list) also run inside the container (see
[file-sandbox.md](file-sandbox.md)); the permission engine's root scoping remains
a complementary soft layer on top.

## Isolation model

| Control | Flag | Effect |
|---|---|---|
| Network | `--network none` | Sandboxed commands cannot reach the network |
| Root FS | `--read-only` | Container runtime is read-only; workspace is the only writable path |
| Memory | `--memory 512m` | Memory cap |
| CPU | `--cpus 2` | CPU cap |
| Cleanup | `--rm` | Container is removed after each command |
| Env | `GIT_TERMINAL_PROMPT=0` etc. | Non-interactive enforcement (same as LocalExecutor) |

## Per-command model

Each `run()` invocation is one `docker run --rm …` subprocess — no persistent
container to manage. The tradeoff: `cd` and `export` don't persist across
commands. Commands can be chained with `&&` if state is needed. Background
tasks (v2) run inside a long-lived container via `docker exec`.

## Configuration

| Field | Default | Meaning |
|---|---|---|
| `sandbox_enabled` | `false` | Master switch (user-global — a repo config can't enable sandboxing) |
| `sandbox_backend` | `"docker"` | Backend selector (v1: only `"docker"`) |
| `sandbox_image` | `"python:3.12-slim"` | Container image |
| `sandbox_network` | `"none"` | Docker network mode |

## Fallback

If `docker` CLI is not on PATH or the daemon is unreachable (checked via
`docker info`), the manager falls back to `LocalExecutor` and logs a warning.
The shell tool's result dict carries a `sandboxed: true` flag so the UI can
surface whether a command was sandboxed.

Since **v2** the fallback is also **per-command**: if Docker disappears after
engine build (CLI removed, daemon stopped mid-session), `DockerExecutor.run`
detects it (`FileNotFoundError`, or a `docker run` exit with
"Cannot connect to the Docker daemon" in stderr) and runs that command locally,
returning `sandboxed: false` plus a `fallback_reason` the UI can surface.

## Boundaries

- **File tools (v2)**: with the sandbox enabled, `files`/`code_files`
  capabilities swap to container-backed read/write/list/patch/delete
  (`sandbox_files.py`), including `apply_patch`/`replace_in_file` and multi-root
  mounts (see [file-sandbox.md](file-sandbox.md)).
- **Background tasks (v2)**: supported via a long-lived container + `docker exec`;
  output captured to a workspace file.
- No persistent container for foreground commands (each gets a clean environment).
- `docker_available()` is checked at engine build time, not per-command; the
  per-command probe in `DockerExecutor.run` catches post-build outages and
  falls back locally (v2).
