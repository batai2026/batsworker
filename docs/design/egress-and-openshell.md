# Egress policy + OpenShell sandbox (P0 — v0.3.1 alignment)

> Status: **implemented (scaffolding + server)**, real OpenShell CLI flags are
> site-specific. Source: `coworker/egress.py`, `coworker/tools/egress.py`,
> `coworker/tools/openshell.py`, `coworker/permissions.py`,
> `coworker/server/app.py`, `scripts/security_drill.py`, tests in
> `tests/test_egress.py` / `tests/test_openshell.py`.

## Egress policy server

**Problem**: in a sandboxed/network-restricted run, the agent hits blocked
domains. The user must be able to approve specific destinations once, after which
the agent can reach them without asking again.

**Solution** — a persistent, user-level allowlist:

- `EgressPolicy` (`coworker/egress.py`) stores `{domain → reason}` in
  `<state-dir>/egress-policy.json`. Matching is exact-host-or-subdomain
  (`api.example.com` matches `example.com`; `example.com.evil.net` never does).
- `request_egress(domain, reason)` (`coworker/tools/egress.py`) is the agent-side
  tool. It is **approval-gated** (`requires_approval=True`): the engine shows a
  check-in card, and the user's approval IS the "allow it" step. On execution the
  domain is persisted.
- The permission engine's `_domain_allowed` consults the policy store
  (`permissions.py::_egress_policy_allows`), so approved domains auto-run without
  a per-session grant (and without bypassing the reviewer in AUTO_APPROVE mode —
  the policy is a user-level setting).
- REST: `GET /v1/egress/policy` · `POST /v1/egress/policy` ·
  `DELETE /v1/egress/policy/{domain}`.
- GUI: Settings ▸ Security ▸ **Egress policy** card (list / add / remove) + the
  OpenShell backend status line.
- Registered as the `egress` catalog capability on the 7 security personas and on
  `CODE_CAPABILITIES` (the coding agent can request blocked downloads too).

## OpenShell sandbox executor

**Problem**: upstream v0.3.1 runs coworkers inside NVIDIA OpenShell. The exact
OpenShell CLI is site-deployed and varies.

**Solution** — a configurable adapter with the same v2 honesty as Docker:

- `OpenShellExecutor` (`coworker/tools/openshell.py`) implements `Executor` and
  owns the real OpenShell sandbox lifecycle (NVIDIA OpenShell ≥ v0.1.x):
  - `sandbox_openshell_prefix` (config.toml, JSON argv list) is everything up to
    the command placeholder. The default is the real exec CLI:
    `["openshell","sandbox","exec","-n","{sandbox}","--workdir","/workspace","--"]`
  - Each token may reference `{workspace}`, `{image}`, `{network}`, `{sandbox}`;
    they expand at construction (literal replacement, so unknown braces like
    `{gpu}` survive). `{sandbox}` is the managed sandbox name
    (`coworker-<pid>-<hash>`).
  - The sandbox is created lazily on the first command:
    `openshell sandbox create --name {sandbox} --from <image> --upload
    <workspace>:/workspace --policy <policy.yaml> --detach`, then readiness is
    probed by re-running `true` through the exec prefix (OpenShell reports
    not-ready errors until the sandbox leaves Provisioning). `close()` issues
    `openshell sandbox delete <name>` (best-effort).
  - Isolation comes from a policy file: `sandbox_openshell_policy` points at a
    site YAML; empty means the embedded baseline (Landlock hides unlisted paths,
    root FS read-only except `/workspace` + `/tmp`, empty `network_policies` =
    egress denied, `run_as_user: sandbox`).
  - Availability is probed (`<prefix[0]> --version`, expanded) and cached 30s.
  - Commands run as `<prefix> <command>` with the non-interactive env + hard
    timeout; results carry `sandboxed: true`.
  - If the CLI is missing or vanishes mid-session, or create fails, it falls back
    to `LocalExecutor` and marks `sandboxed: false` + `fallback_reason` (create
    failure is sticky per session — no retry storm).
- `agent.py::build_engine` selects it when `sandbox_backend == "openshell"`.
- `GET /v1/settings/openshell/status` reports `available` / `prefix` / `backend`
  for the settings UI.

## Desktop host checks (Tauri)

The browser build can only see the sidecar's status, so the desktop shell exposes two
read-only probes that Settings ▸ Security renders in a desktop-only card:

- `openshell_version` (`src-tauri/src/lib.rs`) — runs `openshell --version` with the
  user's real PATH (`sidecar_env()`, so a Finder-launched app still finds Homebrew
  installs) and returns the first non-empty line, or `null`.
- `docker_host_networking` — reads Docker Desktop's settings store
  (`settings-store.json`, falling back to `settings.json`; macOS
  `~/Library/Group Containers/group.com.docker/`, Windows `%APPDATA%\Docker\`,
  Linux `~/.docker/desktop/`) and reports:

  | Field | Meaning |
  |---|---|
  | `docker_cli` | `docker --version` succeeds |
  | `host_networking` | `hostNetworkingEnabled` (also tolerates `EnableHostNetworking` / `enableHostNetworking`); `null` = unknown, never assumed off |
  | `settings_path` | which file was read (`null` when absent) |

Both run in `spawn_blocking` and degrade to `null`/`false` on any failure. The card
(`desktop-checks-card` in `SecuritySection.tsx`) is hidden when `isTauri()` is false, so
the browser GUI is unchanged; host networking is only needed when the sandbox runs with
`sandbox_network = "host"`.

## Container image

- `Dockerfile` (multi-stage: installs the package + `aisuite` in `python:3.12-slim`,
  runs `openworker-server --cwd /workspace`).
- `.github/workflows/docker.yml` builds and pushes
  `ghcr.io/andrewyng/openworker:<tag>` + `latest` on `v*` tags.

## Verification

- `tests/test_egress.py`: policy add/remove/subdomain matching, persistence, tool
  round-trip, catalog capability, API, permission-engine integration.
- `tests/test_openshell.py`: prefix parsing (real-CLI default), placeholder
  expansion (`{sandbox}` + unknown braces), full lifecycle with a fake CLI
  (create argv / readiness probe / exec / delete on close), site policy override,
  create-failure fallback, CLI-missing fallback, status API.
- `scripts/security_drill.py` exercises the egress round-trip as step 5/6.
- Backend regression (security/connectors/subagents/skills/marketplaces): passed.

## Follow-ups (need real environments)

- ~~Fill `sandbox_openshell_prefix` with the site's real CLI flags~~ — done: the
  default prefix is the real NVIDIA OpenShell CLI (`sandbox create/exec/delete`
  lifecycle), isolation via `sandbox_openshell_policy` (embedded baseline when
  unset). Remaining: verify on a host with OpenShell installed.
- Verify `ghcr.io` push on a real tag release.
- Desktop host checks landed (`openshell_version` + `docker_host_networking`, GUI card);
  still to verify on a machine with Docker Desktop and OpenShell installed.