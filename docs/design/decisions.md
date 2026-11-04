# Decision log (curated)

> Harvested from the dated decision comments in the code (`OPE-###` spec
> references, `owner ruling YYYY-MM-DD`, `*-SPEC §n`). This is an index of
> *what was decided and why*, pointing at the source file where the ruling
> lives. The specs themselves (OPE-*, MEMORY-SPEC, SKILLS-SPEC,
> github-relay-spec) are not in this repo; the comments quote them.

## OPE decisions

| ID | Area | Decision | Source |
|---|---|---|---|
| OPE-27 | Engine | Auto-compaction of long histories: checkpoint between tool turns, boundary summary, defensively-parsed state | `coworker/compaction.py`, `engine.py` |
| OPE-51 | Interaction | Grouped questions — one button row can't answer 2+ questions; cards carry up to 4 grouped entries; old persisted string items stay valid | `coworker/inbox.py:83-90`, `engine.py:1792`, `interactions.py:53` |
| OPE-58 | Personas | Persona bundles carry their own `skills/` folders | `coworker/agent.py:247` |
| OPE-82 | Toolchain | PATH *resolution* and tool *installation* are two problems kept deliberately apart | `coworker/toolchain.py:3-7` |
| OPE-83 | Desktop | The desktop shell hands the login shell's PATH/env to the sidecar at spawn | `coworker/toolchain.py:7`, `src-tauri/src/lib.rs` |
| OPE-85 | Supply chain | `request_tool` installs only from a pinned digest-verified catalog — fetching an executable is a supply-chain decision, never auto-run; a missing scanner must *ask*, not silently drop the check that needed it | `coworker/toolchain.py:18,207`, `agent.py:303` |
| OPE-93 | Personas | Connector capability is a concrete set, never a bare "uses connectors" bit; an update that ADDS a connector must grow the `capability_set` and re-consent; allowlisted personas get ONLY those connectors (least privilege) | `coworker/personas/loading.py:30,57`, `agents/base.py:40`, `agent.py:308` |
| OPE-100 | Teams | The board is an open surface: `ocw` CLI + `ocw board mcp` stdio server for external harnesses | `pyproject.toml` script comment, `teams/cli.py` |
| OPE-101 | Audit | Audit-cost defect class: don't scale retained audit detail by session length | `coworker/audit.py:66` |
| OPE-111 | Risk | `browser_open_url` is EGRESS "by another name"; read-only mode must be reachable in a single step | `coworker/risk.py:33,80` |
| OPE-114 | Permissions | **Provenance floor**: running something the agent DOWNLOADED this session forces a human card — the reviewer never sees those cards, and command allowlists don't either | `coworker/engine.py:215,1094,1234` |
| OPE-117 | Permissions | Deferred-execution writes (things that run later on their own — `.git/hooks`, scheduled tasks) are human-only; even Discuss/Plan modes still show cards where owed | `coworker/permissions.py:74`, `risk.py:41` |
| OPE-122 | Browser | Local file uploads resolve inside a granted root only | `coworker/connectors/browser_automation.py:352` |
| OPE-124 | Browser | Redirect guard: the hop that actually loads is re-checked, not just the initial URL | `coworker/connectors/browser_automation.py:333` |

## Spec-section rulings

| Spec | Ruling | Source |
|---|---|---|
| github-relay-spec §2.3, §4, §8 | Cloud relay for GitHub inbound; relay code never touches the secret store; GitHub as second relay provider after Slack | `coworker/connectors/relay_client.py:69`, `cloud.py:522`, `descriptors.py:615` |
| github-relay-spec §7 | On reconnect, missed messages recovered via channel-context / nudge | `relay_client.py:371` |
| spec §1.5 (2026-08-12) | Permission mode renamed `"auto"` → `"bypass-approvals"` — the name must say "safety off" and not be confusable with `auto-approve` | `coworker/permissions.py:219` |
| spec §2.2 | `send_message` gates as EGRESS: model-chosen free text is the same outbound channel | `coworker/risk.py:32` |
| spec §8.4 / §8.6 | Reviewer streak semantics; one action per approval request | `coworker/engine.py:29,719` |
| MEMORY-SPEC §3 | `session` memory scope is dead — never save to it | `coworker/memory/tools.py:77` |
| MEMORY-SPEC §4.2 | When-to-remember rules injected whenever a store is wired | `coworker/agent.py:69` |
| MEMORY-SPEC §5.1 | `on_saved` hook renders the save notice + Undo | `coworker/agent.py:207` |
| MEMORY-SPEC §6 | User rules outrank learned memories; agent can never write them | `coworker/agent.py:210`, `memory/settings.py` |
| MEMORY-SPEC §7 | Index mode above 8k chars: 10 newest in full + summaries + `memory_read` | `coworker/memory/tools.py:6` |
| MEMORY-SPEC §7.1 | What the agent knows is fixed at session start | `coworker/agent.py:404` |
| SKILLS-SPEC §3 | Per-session effective skill menu | `coworker/agent.py:419` |
| SKILLS-SPEC §4.1 | Live skill menu recomputed every turn; force-run results split via `_display` | `coworker/agent.py:501`, `engine.py:288` |
| SKILLS-SPEC §5.2 | `save_skill` is the worker-authors door (human-approved) | `coworker/agent.py:426` |

## Owner rulings

| Date | Ruling | Source |
|---|---|---|
| 2026-08-20 | Live clock injected **every turn** (stale timestamps caused bad reasoning) | `coworker/agent.py:482` |
| 2026-08-20 | One reader everywhere: single windowed, line-numbered `read_file` shared by the whole stack | `coworker/catalog.py:76` |
| 2026-08-24 | Reviewer denial breaker: 2 → **5 in a row** + cumulative-aware streak semantics (a naive 2-count silently punished legitimate back-and-forth) | `coworker/engine.py:29` |
| 2026-08-24 | `updated_at` means "last worked on", never "last saved" | `coworker/conversations.py:173` |
| 2026-08-24 | The `_display` sidecar survives reload but is never provider-visible | `coworker/engine.py:181` |
| 2026-08-24 | A question's captured answer counts as evidence for exactly that question's scope | `coworker/engine.py:1846` |
| 2026-08-24 | Reviewer: agent's captured question shown WITH the reply | `coworker/reviewer.py:239` |

## Incident-driven decisions

| Date | Incident → decision | Source |
|---|---|---|
| 2026-07-28 | A model **faked a memory save** (asserted "I'll remember" without calling the tool) → MEMORY-SPEC anti-bluffing notices + always-visible save toast | `coworker/agent.py`, `memory/` |
| (undated) | Real telemetry leak during a test run → `autouse` state-dir isolation fixture in every test | `tests/conftest.py` |
| PYSEC-2026-3481/3482/3483 | MCP SDK pinned `>=1.28.1,<2` (floor = security fixes; ceiling = 2.0.0 removed `streamablehttp_client`) | `pyproject.toml` |
