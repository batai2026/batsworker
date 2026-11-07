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

- `OpenShellExecutor` (`coworker/tools/openshell.py`) implements `Executor`.
  - `sandbox_openshell_prefix` (config.toml, JSON argv list; default
    `["openshell", "exec", "--"]`) is everything up to the command placeholder.
  - Each token may reference `{workspace}`, `{image}`, `{network}`; they expand
    to the resolved workspace path and `sandbox_image` / `sandbox_network` at
    construction (literal replacement, so unknown braces like `{gpu}` survive).
    Example:
    `["openshell","exec","--mount","{workspace}:/workspace","--image","{image}","--network","{network}","--"]`
  - Availability is probed (`<prefix[0]> --version`, expanded) and cached 30s.
  - Commands run as `<prefix> <command>` with the non-interactive env + hard
    timeout; results carry `sandboxed: true`.
  - If the CLI is missing or vanishes mid-session, it falls back to
    `LocalExecutor` and marks `sandboxed: false` + `fallback_reason`.
- `agent.py::build_engine` selects it when `sandbox_backend == "openshell"`.
- `GET /v1/settings/openshell/status` reports `available` / `prefix` / `backend`
  for the settings UI.

## Container image

- `Dockerfile` (multi-stage: installs the package + `aisuite` in `python:3.12-slim`,
  runs `openworker-server --cwd /workspace`).
- `.github/workflows/docker.yml` builds and pushes
  `ghcr.io/andrewyng/openworker:<tag>` + `latest` on `v*` tags.

## Verification

- `tests/test_egress.py`: policy add/remove/subdomain matching, persistence, tool
  round-trip, catalog capability, API, permission-engine integration.
- `tests/test_openshell.py`: prefix parsing, placeholder expansion (pure +
  argv capture), CLI-missing fallback, fake-CLI success, per-command fallback,
  status API.
- `scripts/security_drill.py` exercises the egress round-trip as step 5/6.
- Backend regression (security/connectors/subagents/skills/marketplaces): passed.

## Follow-ups (need real environments)

- Fill `sandbox_openshell_prefix` with the site's real CLI flags (the adapter
  already expands `{workspace}` / `{image}` / `{network}`).
- Desktop Tauri settings item: OpenShell version + Docker Desktop host-networking
  check (Rust commands + GUI card).
- Verify `ghcr.io` push on a real tag release.