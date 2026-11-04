# Connectors

> Source: `coworker/connectors/` — `descriptors.py` (catalog),
> `base.py` (adapter ABC), `gateway.py`, `adapters.py`, `relay_client.py`,
> `github_relay.py`, `accounts.py`, `setup.py`, `tools.py`, `senders.py`.

A connector is a third-party platform OpenWorker can act on (Slack, GitHub,
Gmail, …). ~40 are declared in the single `DESCRIPTORS` registry
(`descriptors.py`); `register_descriptor()` adds extras.

**Shipped / connectable**: telegram, slack, email (IMAP), gmail,
google_calendar, browser, github, outlook, jira, monday, confluence, zendesk,
linear, gitlab, discord, stripe, asana, hubspot, dropbox, box, whatsapp,
quickbooks, docusign, clickup, google_drive, canva, figma, close, notion,
attio, posthog, mixpanel, amplitude, apollo, hunter.
**Placeholders** (`available=False`): datadog, salesforce, descript, clay,
pagerduty. `experimental/` is a hook for risk-acknowledged connectors
(currently empty; excluded from release builds).

## The abstraction

`base.py::BasePlatformAdapter` — `connect` / `disconnect` / `send`, an inbound
`handle_message` hook, and optional `send_interactive` / `handle_interaction`
(for Slack Approve/Deny buttons). Supporting value types: `SessionSource`
(the `platform:chat_id[:thread]` target token), `MessageEvent` (with the
`mentions_me` flag and `tagged_text()` framing), `MessageSource` (display-only
sidecar), `InteractionEvent`, `SendResult`.

## Gateway

`gateway.py::Gateway` owns all adapters and is the single inbound door:

- **Inbound allowlist** (`config.py::is_authorized`): empty allowlist admits
  *nobody* — fail closed. Multi-workspace relay uses per-team `TeamAuth`.
- Unauthorized messages are parked (`parked.py::ParkedStore`, a JSON-capped
  queue) for allow-and-deliver, not silently dropped.
- `reply_resolver` maps channel replies (Slack messages carrying `[ow:<id>]`
  tokens, button interactions) back to parked Inbox items before ever
  starting new turns.

### Inbound → session

`server/manager.py::_dispatch_inbound` buffers channel messages and fans them
out to subscribed sessions. The **§31 mention router** (`mentions.py`)
handles @-mentions: a mention spawns a dedicated per-thread coworker session
with a standing `send_message` grant scoped to that thread and pre-loaded
channel context. DMs go to the designated DM session or park as unrouted
(`unrouted.py`). Email inbound is polled (`email_tools.py`).

### Outbound

Outbound is the stateless `send_message` tool (`tools.py`) via
`senders.py::DEFAULT_SENDERS` (httpx-based) — no long-lived adapter needed to
push a message. The permission system classifies it EGRESS because the body
is model-chosen free text (see
[permissions-and-approvals.md](permissions-and-approvals.md)).

## Three auth paths

Per-descriptor flags choose how a connector is credentialed:

1. **Manual** — paste token fields; validated by one-shot whoami calls
   (e.g. `_validate_slack`); stored under the `SecretStore` profile
   `<name>:default` (`setup.py::connect_connector`).
2. **Managed OAuth** (`managed=True`) — one-click broker handshake through
   OpenWorker Cloud (`cloud.py`; the *only* cloud piece, per the privacy
   model). Google connectors are `managed_paused=True` pending CASA review.
3. **`mcp_url`** — vendor-hosted MCP server with local OAuth/DCR
   (jira → mcp.atlassian.com, monday → mcp.monday.com); the tool surface is
   pinned to `mcp__<name>__<tool>` defs in `tool_defs.py`.

Multi-account support (`accounts.py`): profiles at `<name>:account:<id>`
(gmail, gcal, hubspot portals, github installs).

## Cloud relay

Direct inbound listeners (Socket Mode for Slack in `adapters.py`, Telegram,
polled email) require the server to hold long-lived credentials. The relay
(`relay_client.py::SlackRelayAdapter`, `github_relay.py::GitHubRelayAdapter`,
`cloud.py`) is the alternative: the desktop holds an outbound WebSocket to
OpenWorker Cloud, inbound events fan out through `RelayHub` with per-team /
per-install allowlists (github-relay-spec §2.3, §4, §8). Relay code never
touches the secret store. Missed messages are recovered on reconnect via
channel-context replay (spec §7).
