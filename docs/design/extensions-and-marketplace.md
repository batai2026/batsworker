# Extensions, marketplaces & workflow features (2026-10)

> Source: this doc archives a batch of features built on top of the 0.2.1
> snapshot. Each section lists its source files; where the code and this doc
> disagree, the code wins.
>
> - `coworker/skills/demo.py`, `coworker/automation/templates.py`
> - `coworker/connectors/marketplace.py`, `coworker/memory/markdown.py`
> - `coworker/security/__init__.py`, `coworker/tools/security.py`
> - `coworker/tools/subagent.py` (delegate_parallel), `coworker/catalog.py`
> - `coworker/server/app.py`, `coworker/server/manager.py`
> - `surfaces/gui/src/components/{SkillsTab,TemplateMarketplace,MarketplaceModal,ScheduledView,MemorySection,SecuritySection,HireModal,RightRail,SettingsView,PersonasTab}.tsx`
> - `surfaces/gui/public/manifest.webmanifest`, `surfaces/gui/index.html`, `surfaces/gui/src/styles.css`

Ten features are archived here, grouped into three themes:

1. **Login & cloud optionality** (analysis) — the signed-in vs signed-out surface.
2. **Marketplace doors** — automation templates, connector/plugin ecosystem,
   role-based "hire" landing pages.
3. **Workflow upgrades** — demo→skill, built-in browser panel, plain-text
   editable memory, PWA/multi-device, parallel subagents, editable threat model
   + finding closure loop.

---

## 1. Demo → skill (`coworker/skills/demo.py`)

Turn a recorded/pasted demonstration into a real `SKILL.md` skill.

- **Pasted steps** (`steps_from_text`) — one step per line, or a light markdown
  list (`1.`, `-`, `*`), cleaned into ordered steps.
- **Browser recording** (`steps_from_recording`) — a saved browser replay
  (`<data-dir>/browser-replays/*.json`) is converted into readable ordered
  steps ("Open the page …", "Click …", "Type …").
- `build_skill_markdown()` emits YAML frontmatter (`name` / `description` /
  `source: demo`) + body (Steps + Guidelines).
- Install goes through the **same staged path as an uploaded archive**
  (`SkillStore.stage_upload` / `confirm_upload`): the generated SKILL.md is
  written to the staging dir and the user reviews it before confirming. The
  Settings list shows the `demo` provenance badge.

API: `POST /v1/skills/demo` → `manager.stage_demo_skill`.
GUI: Settings ▸ Skills ▸ "Record a demo" door (paste steps or pick a saved
browser recording via `GET /v1/browser/recordings`).

Tests: `tests/test_skills_api.py` (`test_demo_skill_*`).

## 2. Built-in browser panel (`surfaces/gui/src/components/RightRail.tsx`)

The Playwright browser session behind the `browser_*` tools was backend-only; the
GUI now has a live view.

- New **Browser** rail section: live screenshot (data URL), current URL/title,
  status, last action/error, with **Screenshot / Open in system browser / Close
  browser** actions.
- Polls every 2 s while the section is open; `refreshKey` (bumped by App on every
  `browser_*` tool call) refreshes immediately.
- Auto-reveals once when a browser session is first live ("see the work as it
  happens").

Backend already exposed `GET /v1/browser/state`, `POST /v1/browser/screenshot`,
`POST /v1/browser/close`; this batch adds `GET /v1/browser/recordings`.

## 3. Automation template marketplace (`coworker/automation/templates.py`)

Backend-served, searchable template catalog (Cursor-Automations style).

- 10 built-in templates (the 6 original quickstart templates + `code-digest`,
  `pr-risk`, `dependency-audit`, `bug-triage`), each with `key/title/blurb/
  cadence/category/conns/needs_repo/needs_channel/consent/deliver/day/time`.
- `cron_for(day, time)` assembles a 5-field cron; `render_instructions()` fills
  `{repo}/{channel}/{deliver}`.
- One-click instantiation routes through the same validation as the manual form
  and adds a standing `send_message → channel` grant for write templates.

API: `GET /v1/automations/templates`,
`POST /v1/automations/templates/{key}/instantiate`.
GUI: `TemplateMarketplace.tsx` in the Automations page — search + category
chips + card grid + configure pane.

Tests: `tests/test_marketplaces.py` (`test_template_*`).

## 4. Connector/plugin marketplace (`coworker/connectors/marketplace.py`)

The open-ecosystem door (Claude-Marketplace style), two kinds of extensions:

- **Curated MCP servers** — installing adds an MCP server to the user's Custom ·
  MCP group (`POST /v1/mcp`), tools immediately available. Ships Granola + Linear.
- **Community plugins** — JSON-serializable connector descriptors (fields,
  instructions, auth). Installing registers a new connector card via
  `register_descriptor()` and persists to `<data-dir>/connector-plugins/<name>.json`;
  `load_installed_plugins()` (called from `descriptors.py` import time)
  re-registers persisted plugins on every startup.
- Plugins choose names that do **not** collide with shipped connectors; catalog
  copy (About/Access) is provided in `catalog_copy.py` for shipped plugins.

API: `GET /v1/connectors/marketplace`,
`POST /v1/connectors/marketplace/plugins/{name}/install|uninstall`.
GUI: `MarketplaceModal.tsx` (⋮ Marketplace button on the Connectors page).

Tests: `tests/test_marketplaces.py` (`test_plugin_*`, `test_marketplace_catalog_*`).

## 5. Plain-text editable memory (`coworker/memory/markdown.py`)

Lindy-style: memory also lives as an editable Markdown document.

- `memory_to_markdown()` groups entries by scope (`## Global`,
  `## Workspace: <path>`, `## Session: <id>`), one bullet per memory.
- `parse_memory_markdown()` reads an edited file back into rows.
- Re-import is **replace per section** — the file becomes the source of truth for
  each scope it names.

API: `POST /v1/memory/export`, `POST /v1/memory/import`,
`POST /v1/memory/reveal`.
GUI: Settings ▸ Memory ▸ "Plain-text memory" card (Export / Show folder /
Re-import edited file).

Tests: `tests/test_memory_markdown.py`.

## 6. PWA / multi-device (`surfaces/gui/public`, `index.html`, `styles.css`)

The web build becomes installable and phone-friendly.

- `manifest.webmanifest` + SVG/PNG icons; `index.html` gains manifest link,
  `theme-color`, `mobile-web-app-capable`, `apple-mobile-web-app-capable`,
  `apple-touch-icon`, `viewport-fit=cover`.
- Mobile CSS: right rail becomes a full-height overlay ≤900 px (chat keeps the
  width), `100dvh` for the URL bar, narrower sidebar + larger composer text on
  ≤640 px (avoids iOS focus zoom).
- Service worker (`public/sw.js`, production browser builds only): precaches the
  shell and content-hashed assets for offline reloads; `/v1/*` API calls and
  WebSockets stay network-only so the sidecar launch token is never cached.

## 7. Parallel subagents & task chunking (`coworker/tools/subagent.py`)

`delegate_parallel` — split a big job into independent read-only research
subtasks and run them in parallel.

- Each subtask gets a fresh read-only explorer engine (`build_explorer_engine`);
  the tool runs them in a `ThreadPoolExecutor` (true parallel fan-out) and
  returns `{ok, results: {name: {report, status}}, count}`.
- Registered for `subagents=True` agents alongside `explore` (`coworker/agent.py`).
- The Progress panel shows "Running subagents in parallel…" while these tools run.

Tests: `tests/test_subagent.py` (`test_delegate_parallel_*`).

## 8. Editable threat model + finding closure (`coworker/security`, `coworker/tools/security.py`)

Claude-Security-style loop for the security coworkers.

- **Threat model**: `<data-dir>/security/threat-model.md`, a team-editable
  markdown file (default template with In scope / Out of scope / Reachability /
  Mitigations). Agent tools `threat_model_read` / `threat_model_update` (update
  is approval-gated).
- **Finding tracker**: `<data-dir>/security/findings.json` carries each finding
  `open → fixing → fixed → closed` with its fix PR url. Agent tools
  `security_finding_log` / `security_finding_list` / `security_finding_update`.
- Registered as the `security` catalog capability (`coworker/catalog.py`) and
  added to the 7 security-family persona manifests.

API: `GET/PUT /v1/security/threat-model`,
`POST /v1/security/threat-model/reveal`,
`GET/POST /v1/security/findings`, `PATCH/DELETE /v1/security/findings/{id}`.
GUI: Settings ▸ Security tab (`SecuritySection.tsx`) — threat-model editor +
findings tracker.

Tests: `tests/test_security_workflow.py`.

## 9. Role-based "hire" landing pages (`surfaces/gui/src/components/HireModal.tsx`)

Lindy-style role directory inside the app.

- Settings ▸ Coworkers ▸ **"Hire by role"** opens a modal with 5 role cards
  (Security Reviewer, Cloud Posture, Dependency Audit, Executive Assistant,
  Software Engineer).
- Each card expands to a landing page: **What it does / Checks in / Never /
  Works with**, plus **"Meet the coworker →"** which opens that persona's detail
  page (configure / start a session).

## 10. Signed-in vs signed-out (analysis)

An earlier analysis documented where cloud sign-in changes behavior; it is
summarized in the worklog below rather than as code.

---

## On-disk state added

| Path | Purpose |
|---|---|
| `<data-dir>/skills-staged/<token>/SKILL.md` | Staged demo-generated skills (pre-confirm) |
| `<data-dir>/browser-replays/*.json` | Saved browser recordings (existing; now listed via HTTP) |
| `<data-dir>/connector-plugins/*.json` | Installed marketplace plugins (re-registered on boot) |
| `<data-dir>/memory/MEMORY.md` | Exported plain-text memory (global default location) |
| `<data-dir>/security/threat-model.md` | Editable threat model |
| `<data-dir>/security/findings.json` | Finding tracker |

## Verification

- Backend: `pytest tests/test_marketplaces.py tests/test_memory_markdown.py
  tests/test_subagent.py tests/test_security_workflow.py tests/test_skills_api.py
  tests/test_connectors.py` → **105 passed** in the final regression run.
- GUI: `tsc --noEmit` clean; `scripts/check_i18n.py` reports all `t()` keys
  translated with no stale entries.
- Known environment-only failures (unrelated to these features): `time.tzset()`
  missing on Windows in `test_automation.py`; GBK decoding of UTF-8 persona files
  in two `test_security_bundles.py` tests; vitest cannot spawn esbuild under the
  DSH sandbox (EPERM).