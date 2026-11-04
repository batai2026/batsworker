# Personas & skills

> Source: `coworker/personas/` — `manifest.py`, `registry.py`,
> `loading.py` — and `coworker/skills/`. Personas follow "persona manifest"
> conventions; skills follow "SKILLS-SPEC" (quoted in code comments).

## Persona = agent definition without code

A persona is a **markdown file with YAML frontmatter** (`manifest.md`): the
body is the system prompt, the frontmatter declares capabilities. No
executable code ships with a persona (the deliberate security property).

`manifest.py::PersonaManifest` parses strictly — unknown keys or malformed
values raise `ManifestError` rather than being ignored. Key fields:

| Field | Meaning |
|---|---|
| `tools` | Catalog capability names composed via `catalog.py::expand()` |
| `skills`, `mcp` | Skill folders and MCP servers the persona may use |
| `connectors` | `False` / an allowlist tuple / the `all` sentinel — the sentinel is **reserved for builtins and fails closed** when parsed from third-party input (OPE-93: never a bare "uses connectors" bit) |
| `team` | `lead` / `worker` / absent — solo personas are not team-eligible |
| `requires_folder`, `subagents`, `scheduling`, `messaging` | Trait gates matching `agents/base.py` |
| `default_permission_mode`, `recommended_models`, `recommends` | Defaults + connector/MCP recommendations (with reason + tier) shown in the UI |
| `version`, `ships`, `group` | Versioning, release flag (`ops.md` has `ships: false`), grouping (general/security) |

`to_agent()` materializes a runtime `Agent` with the catalog-expanded tool
set; `registry.py::PersonaRegistry` unifies the two persona families and
tracks the installed → enabled → surfaced lifecycle (state file). Default
persona: `cowork`.

## Builtins

Python-built (`cowork`, `code` — wrapping the `agents/cowork.py`,
`agents/code.py` builders) plus markdown personas in
`personas/builtin/`: `security`, `swe-lead`, `swe-worker`, `devops-lead`,
`devsecops-lead`, `triage-lead`, `appsec-worker`, `secrets-worker`,
`cloud-posture`, `posture-worker`, `dep-audit`, `logs-worker`,
`infra-worker`, `test-worker`, `design-worker`, `change-worker`, and `ops.md`
(`ships: false`). The security-family personas are semgrep-centric and carry
skills beside their manifest (e.g. `semgrep-review`).

## Third-party install & consent

`loading.py` loads from a local dir or a shallow git clone. At install time
the user sees a **`consent_summary`**: the concrete capability set — tools,
risk classes, connectors, MCP servers, team capability — never a vague
description. On update, if the new version's `capability_set` **grows**, the
stored consent no longer matches and re-consent is required (OPE-93: an
update that adds a connector must grow the set and re-ask).

## Skills

A skill is a `skills/<name>/SKILL.md` — YAML frontmatter (name, description)
plus procedural instructions the model loads on demand. Behaviors
(SKILLS-SPEC):

- **Per-session effective menu** (§3): the skill list is computed per
  session, and the menu passed to the engine is a **live callable**
  recomputed every turn (`agent.py:419`) — like the roots list and the live
  clock (owner ruling 2026-08-20).
- `load_skill` injects a skill's instructions mid-session; force-run skills
  split result handling into a `_display` sidecar so the user's view and the
  provider-visible transcript differ safely.
- **`save_skill` is the worker-authors door** (§5.2): a session can propose
  installing a finished procedure as a reusable skill — and because that
  installs future behavior, it's a `PERSISTENT_AUTHORITY_TOOLS` member,
  always human-approved
  ([permissions-and-approvals.md](permissions-and-approvals.md)).
- **Persona-carried skills** (OPE-58): a persona bundle's `skills/` dir joins
  the loader, so a persona ships its procedures with it.
