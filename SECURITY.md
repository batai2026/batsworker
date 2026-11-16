# Security policy

OpenWorker runs an autonomous agent on your machine with access to your files, your
terminal, and the accounts you connect. It also updates itself. That makes a
security report about this project materially different from one about a library, so
this file explains both **how to reach us** and **what we already consider in scope**.

- Product: **OpenWorker** · package `coworker` · repo `batai2026/batsworker`
- Current release: **1.0.5**
- Status: open beta

## Reporting a vulnerability

**Please use GitHub's private vulnerability reporting:**
<https://github.com/batai2026/batsworker/security/advisories/new>

That channel is private between you and the maintainers, and it gives us a place to
develop and coordinate a fix before anything is public.

Please **do not** open a public issue for a suspected vulnerability, and do not
include live API keys, session tokens, or personal data in a report — redact them.
If a proof of concept needs a credential, use a throwaway.

There is no security email alias on this repository, so a DM or a public issue is
not a reliable way to reach us privately. If the private-report button is not
available to you, open a public issue that says only "I have a security report,
please enable private reporting" — no details — and we will open a private channel.

### What to include

- Affected version (`1.0.5`, or the installer filename you have)
- Platform (macOS arm64 / macOS x64 / Windows) and how the app was installed
- What an attacker gains — code execution, reading files outside the workspace,
  exfiltrating connector tokens, bypassing an approval prompt
- Reproduction steps, and whether it needs a malicious *model*, a malicious
  *connector*, or only local access

The last point matters most here. A large share of "vulnerabilities" in an agent
runtime need the user to have already connected a hostile tool or model; a report
that names the trust boundary it crosses gets triaged much faster.

### What to expect

This is a small project in open beta, and we would rather state honest response
targets than advertise an SLA we cannot staff:

| Stage | Target |
| --- | --- |
| Acknowledgement of your report | 5 business days |
| Initial assessment (severity + reachability) | 10 business days |
| Fix or mitigation for a confirmed high/critical issue | next release, or sooner |

We will tell you if a report is out of scope or if we disagree with the severity, and
why. Credit in the advisory is offered unless you prefer to stay anonymous.

## Supported versions

OpenWorker ships as a desktop app that updates itself, so in general only the latest
release gets fixes — with one exception that matters more than the rest.

| Version | Supported | Notes |
| --- | --- | --- |
| 1.0.5 | ✅ | Current release; `v1.0.5` tag |
| 1.0.5-rc1 | ⚠️ pre-release | Tagged *after* `v1.0.5` (2026-11-12) and carries work not in the release, including `uv.lock`. No fixes promised; use 1.0.5 or later |
| 1.0.4 | ⚠️ upgrade required | Gets no fixes; please update to 1.0.5 |
| 1.0.0 — 1.0.3 | ❌ not supported | **Update immediately — see below** |
| 0.x, upstream `andrewyng/openworker` | ❌ | Different codebase; report upstream |

### Why 1.0.3 and earlier must be updated

This is a real behaviour change, not a formality. Until 1.0.4, the built-in updater
pointed at two third-party feeds and trusted a **third-party signing key**:

- `download.openworker.com/latest.json`
- `github.com/andrewyng/openworker/releases/latest/download/latest.json`

with the upstream project's minisign public key. Whoever controlled those feeds and
that key could have delivered an update that OpenWorker would have verified,
installed, and run. **1.0.4** rotated the updater to this project's own key and its
own release feed (`github.com/batai2026/batsworker/releases/latest/download/latest.json`);
1.0.5 and later carry the same configuration.

If you are running 1.0.3 or earlier, install 1.0.5 from
<https://github.com/batai2026/batsworker/releases> rather than relying on the in-app
updater, since in those versions the updater is the thing you should not trust. This
is documented here so users on old builds can act on it.

### Installer signing

Be aware of what the installers do and do not prove:

- **macOS** — signed with a Developer ID and notarized when the release workflow has
  its Apple secrets, with the hardened runtime enabled. Builds made without those
  secrets (forks, local runs) are unsigned and Gatekeeper will require
  `xattr -cr`. An unsigned build is not by itself evidence of tampering, but it also
  gives you no integrity guarantee.
- **Windows** — Authenticode-signed **only when** the release workflow has
  `WINDOWS_CERTIFICATE` / `WINDOWS_CERTIFICATE_PASSWORD` configured. Do not assume a
  valid signature on every published `.msi` / `.exe`.
- **Update payloads** are always verified against the embedded minisign public key
  before extraction; a payload that fails verification is rejected. This is the
  guarantee that matters for the self-update path, and it is why the key rotation in
  1.0.4 was necessary.

## Scope

**In scope** — anything that breaks one of these boundaries:

- Reading or writing files outside the workspace the user granted
- Running commands, or reaching the network, without the approval the UI promised
- Escaping the code-mode sandbox (fail-closed by design)
- Leaking model API keys, connector tokens, or conversation content to a party the
  user did not choose — including a model provider the user did not configure
- Forging update payloads, or bypassing update signature verification
- Defeating the local sidecar API's token check, or getting a cross-origin page to
  drive the local server
- Privilege or consent escalation: a plugin, persona, or MCP server obtaining
  capabilities broader than what the user approved

**Generally out of scope**

- Anything that requires the user to have installed a malicious build obtained
  elsewhere, or to have already been administrator/root on the machine
- Prompt-injection *content* that produces a bad-but-permitted action when the
  approval gate worked as designed — raise it as a design issue unless it crosses one
  of the boundaries above
- Vulnerabilities in third-party dependencies with no reachable path in this
  codebase (advisories are tracked in `osv-scanner.toml`; a reachability argument is
  welcome and often changes the answer)
- Missing hardening headers or version banners with no demonstrated impact

## How we handle dependency risk

The app runs other people's code in the form of libraries, so here is what is
actually automated today — stated plainly, gaps included:

- `osv-scanner` runs on every pull request (failing only on advisories the PR *adds*)
  and weekly against the full backlog. It reads `uv.lock`,
  `surfaces/gui/package-lock.json`, `stt/Cargo.lock` and
  `surfaces/gui/src-tauri/Cargo.lock` — 1,423 packages in total at 1.0.5.
- `osv-scanner.toml` holds the accepted-risk list. Each entry carries a code-path
  justification and an expiry date, so an accepted advisory must be re-argued instead
  of ageing silently.
- Both jobs are blocking as of 2026-11-15, including the weekly full-backlog scan
  (`fail-on-vuln: true`). That flip was made only after the backlog drained: the exact
  job invocation (`--config=osv-scanner.toml --recursive ./`) returns "No issues
  found" and exit 0 on the current tree. Because every accept-list row carries an
  `effectiveUntil` date, a lapsed acceptance makes its finding reappear and the scan exit
  1 — debt with a due date turns the job red instead of rotting quietly. That key
  needs osv-scanner >= 2.7.1, so the workflow pins osv-scanner-action v2.7.1; under
  2.7.0 it is an unknown key and the config is rejected outright.
- Not covered, three ways: (1) Python is scanned through `uv.lock`, so an environment
  resolved from `pyproject.toml` while ignoring the lockfile is outside the scan; (2)
  `uv.lock` includes dev and optional extras, so it overstates what actually ships;
  (3) CI installs `requirements-dev.lock`, which is `uv export`ed from `uv.lock` by
  hand — a lock that drifts from that export is scanned but not installed from. The
  version floors in `pyproject.toml` (e.g. `pypdf>=6.21.4`) remain the review-time
  control for the last two.

## Safe harbour

We will not pursue or support legal action against researchers who:

- test against their own installation, their own accounts, and their own data
- stop as soon as the issue is demonstrated, without accessing, modifying, or
  retaining other people's data
- report privately through the channel above and allow a reasonable window to fix
  before publishing

If you are unsure whether something is allowed, ask first through the private report
— a question costs nothing.

Thank you for helping keep OpenWorker's users safe.
