# Changelog

All notable changes to OpenWorker are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Two naming notes before anything else:

- The product is **OpenWorker**, the repository is `batai2026/batsworker`, and the
  Python distribution is `coworker`. A release bumps all three in lockstep.
- Entries are grouped by the release that **first contains** a change. The 1.0.x line
  was developed on several parallel branches, so a commit's date is not a reliable
  guide to which installer carries it; the grouping here was checked against the
  release tags, not against commit order.

History before 1.0.0 is not reconstructed here. The project was developed inside the
upstream `aisuite` / `andrewyng/openworker` repositories, and inventing a narrative
from commit subjects would be worse than saying so. The `baseline-20261104` tag marks
where this fork's own release line starts.

## [Unreleased]

### Added

- `CONTRIBUTING.md` — the commands CI actually runs, commit/branch conventions,
  the lockfile rules, how dependency upgrades are sized and verified, and the
  release version-bump checklist.
- Issue forms (bug report, feature request) and a pull request template under
  `.github/`. The bug form routes vulnerabilities to the private channel in
  `SECURITY.md` instead of a public issue, and the PR template asks for the
  per-suite commands behind "tests pass".

### Changed — dependency and supply-chain work

- `osv-scanner.toml` migrated from the deprecated `ignoreUntil` key to
  `effectiveUntil`, which removes the deprecation warning osv-scanner printed on
  every run. This couples the config to scanner >= 2.7.1, so the
  `dependency-audit.yml` workflow pins `osv-scanner-action` at v2.7.1
  (`32d3cd48`) in the same change; under 2.7.0 `effectiveUntil` is an unknown key
  and the whole config would be rejected.
- The accept-list is now one entry per advisory. Two entries were aliases of
  entries already listed (`RUSTSEC-2026-0429` for `GHSA-q7v9-x93c-xqv3`,
  `RUSTSEC-2024-0429` for `GHSA-wrw7-89jp-8q8g`), so they suppressed nothing and
  the "20 entries" overstated the accepted count: it is 18 distinct advisories.
  Verified by removing each entry in turn — the scanner re-reports exactly that
  advisory and nothing else.

## [1.0.5] — 2026-11-09

### Changed — dependency and supply-chain work

- Replaced SheetJS (`xlsx`) with `exceljs` in the GUI, closing both reachable SheetJS
  advisories; the matching `osv-scanner.toml` accept-list entries were retired in the
  same release.
- Bumped `vite` / `vitest` / `postcss` to close 23 dev-toolchain advisories.
- Bumped the Rust TLS stack off yanked-adjacent advisory ranges, plus routine Rust
  patch bumps.
- Floored `pypdf` at 6.21.4 (`<7`) to close the malformed-PDF denial-of-service
  cluster.
- Added a dependency-audit workflow (`dependency-audit.yml`) built on `osv-scanner`
  and an accepted-risk file (`osv-scanner.toml`), so advisories are tracked instead of
  rediscovered by hand each quarter.

### Fixed

- **Windows: ask-user prompts could hang with no response.** Text-mode subprocess
  calls decoded output with the ANSI code page; on a zh-CN system the decode failed
  and the question never reached the UI.
- **Security, Cloud Posture, and Dependency Audit could not run at all.** A relative
  import in `coworker/catalog.py` climbed above the top-level package, so every such
  session failed to build its engine and silently dropped the user message. Fixed to
  `.security`, with a regression test.

## [1.0.4] — 2026-11-08

### Security

- **The updater no longer trusts a third-party release feed.** It reads only
  `github.com/batai2026/batsworker/releases/latest/download/latest.json`.
- **Update signing anchor rotated to this project's own minisign key**, so update
  payloads are verified against a key we control rather than the upstream project's.
- Local Windows builds can read the updater key from `.ocw-updater.env` instead of
  requiring CI-only secrets.

> Anything at or below 1.0.3 trusted the upstream feed and key. See
> [SECURITY.md](SECURITY.md#why-103-and-earlier-must-be-updated) for what to do.

## [1.0.3] — 2026-11-08

### Security

- Hardened the release pipeline and container image. GitHub Actions pinned to commit
  SHAs — those workflows hold the Apple certificate, the Authenticode PFX, and the
  Tauri updater key, so a moved tag was a path to signing-key theft. Added explicit
  least-privilege `GITHUB_TOKEN` permissions per workflow (the release job opts into
  `contents: write` locally). Routed `eval.yml`'s `workflow_dispatch` input through
  `env` instead of interpolating it into the shell, which was a command-injection
  path in a job holding provider API keys. The container image now runs as an
  unprivileged user instead of root, with a wider `.dockerignore`.
- Fixed a Windows Authenticode gate that could never evaluate true — a step-level
  `if` cannot read that step's own `env`, so `env.WINDOWS_CERTIFICATE` was always
  empty and installers were never signed even when the certificate secrets were
  configured. The presence check is now published as a job-level boolean.

## [1.0.2] — 2026-11-08

### Fixed

- Security, Cloud Posture, and Dependency Audit sessions failed to build their engine
  because of the relative import described under 1.0.5, silently dropping the user
  message with no reply and no visible error. Adds a regression test that exercises
  the capability-expansion path directly.

## [1.0.1] — 2026-11-08

### Fixed

- Windows GBK decoding crash that left questions unanswered: the prompt reached the
  backend, but the response was lost when decoding failed.

## [1.0.0] — 2026-11-08

First stable release (tag `v1.0.0`), containing the work built on top of
`baseline-20261104`:

- **Sessions:** fork and replay with a turn ledger; mid-turn steering over the
  websocket, applied at the next model-request boundary.
- **Plugins and consent:** a unified plugin manifest adapter; one install pipeline
  where connectors and MCP servers go through lifecycle consent; consent v2 requires
  explicit confirmation when a change widens permissions; every install, uninstall,
  and permission change is written to an append-only audit log.
- **Code mode (form B):** an `exec` tool behind a fail-closed sandbox with an approval
  bridge.
- **Evals:** a harness registry as the single source for evals and presets; a baseline
  gate (`--check-baseline`) with byte-deviation thresholds and exit code 3 on drift;
  deterministic golden cases wired into CI, with real-model matrix runs triggered
  manually.
- **Desktop sandbox:** capability checks for OpenShell (version probing, Docker
  Desktop host-networking) and prefix expansion of `{workspace}` / `{image}` /
  `{network}` onto real CLI flags.
- **GUI:** offline shell and first-run skeleton; session, active-session, and run
  state migrated to zustand slices behind hooks whose signatures and return shapes
  were held constant, so those migrations are behaviour-preserving.

## After 1.0.5

**Not** in the `v1.0.5` tag. Most of it is in `v1.0.5-rc1` (2026-11-12); the codepage
unification and the real-model eval baselines reached the mainline only after that.
Listed here so someone comparing a build against this file can tell where a fix came
from.

### Added

- Python dependency graph locked (`uv.lock`, `requirements-*.lock`); CI installs from
  the lockfiles instead of resolving fresh on every run.
- Python suite gated on measured coverage; a Rust CI job that compiles, lints, and
  tests the desktop shell and STT sidecar; the Docker image built on every PR with a
  test asserting state-directory isolation.
- The desktop shell serves the bundled UI from the sidecar, removing the dependency on
  a separately running dev server; the sidecar CLI surface is asserted at bundle time
  so packaging fails on drift.
- Real-model eval matrix across three providers, with per-cell baselines and the first
  green run recorded as a baseline.

### Fixed

- **Subprocess text decoding unified.** Every text-mode `subprocess` call now decodes as
  UTF-8 with `errors="replace"` in one place, so a non-UTF-8 code page cannot crash a
  prompt path.
- PDF inspection enforces its body size cap before base64 decoding, not only at the
  callers.
- Persona archives compare zip entry paths as paths instead of string prefixes.
- The egress allowlist refuses public suffixes and bare IPs.
- Docker keeps the state directory out of the agent's workspace root;
  `egress-policy.json` is treated as a protected setting.
- The server confines the SPA fallback to the `dist` root and lets the bundled UI load
  without the sidecar token header.
- `osv-scanner.toml` expiry dates use `ignoreUntil`.

[Unreleased]: https://github.com/batai2026/batsworker/compare/v1.0.5-rc1...HEAD
[1.0.5]: https://github.com/batai2026/batsworker/compare/v1.0.4...v1.0.5
[1.0.4]: https://github.com/batai2026/batsworker/compare/v1.0.3...v1.0.4
[1.0.3]: https://github.com/batai2026/batsworker/compare/v1.0.2...v1.0.3
[1.0.2]: https://github.com/batai2026/batsworker/compare/v1.0.1...v1.0.2
[1.0.1]: https://github.com/batai2026/batsworker/compare/v1.0.0...v1.0.1
[1.0.0]: https://github.com/batai2026/batsworker/compare/baseline-20261104...v1.0.0
