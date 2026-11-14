# Server & auth

> Source: `coworker/server/app.py` (`create_app`), `coworker/server/manager.py`
> (`SessionManager`), `coworker/server/run.py`.

The Python backend is a FastAPI + uvicorn app that binds **127.0.0.1 only**.
It is the single owner of engines, sessions, persistence, the scheduler, the
connector gateway, and the Inbox. `SessionManager` (`manager.py:180`) is the
god-object that wires them; `create_app()` (`app.py`, ~2,800 lines) exposes
them.

## Transport model

The GUI uses REST for state and one WebSocket per session for the live turn:

- **`GET /ws/session/{session_id}`** — the main channel. JSON frames
  `{type, data}`.
- **`GET /ws/events`** — read-only app-wide push stream (toasts, session-list
  changes) independent of any one session.
- **REST** — ~150 endpoints under `/v1/`: sessions CRUD + messages / roots /
  artifacts, inbox, personas install/export, skills, workspaces
  (open/trust/pick), connectors (+ `/oauth/callback`), providers, settings,
  automations, board (`/v1/board`, with per-actor Bearer tokens), browser
  state, audit.

### WebSocket protocol (session channel)

Client → server: `user_message`, `approval`, `question_response`,
`plan_response`, `directory_response`, `tool_response`, `allow_anyway`,
`interrupt`, `retry`, `set_mode`, `set_model`, `steering`.

Server → client: `ready`, engine events broadcast verbatim
(`assistant_delta`, `reasoning_delta`, `tool_proposed`,
`permission_required`, `tool_started`, `tool_finished`, …), `turn_done`,
`mode_notice`, `model_changed`, `question_requested`, `input_rejected`.

All four interactive prompts (approval, question, plan, directory) are parked
as persisted Inbox items (`inbox.wait`) *before* anyone is asked — the WS
frame is just one way to answer. An item can equally be resolved over REST
(`POST /v1/inbox/{id}/resolve`) or from a bound channel (a Slack reply with an
`[ow:<id>]` token), first responder wins. This is why dropped sockets,
restarts, and unattended runs lose nothing: see [automation.md](automation.md).

#### Dispatch is a table

Inbound frames go through `FRAME_HANDLERS` in `coworker/server/routes/ws.py`
(`type` → async handler taking `_SessionSocket`, the per-connection context of
manager / engine / turn runner / session id / workspace / reject / send).
Envelope checks — JSON decode, per-connection rate limit, "must be an object",
"type must be a string" — stay in the receive loop because they judge the frame,
not the frame type. `team_response` and `items_response` deliberately share one
handler: same verdict shape, same Inbox plan item.

Adding a frame type is two edits: one row in `FRAME_HANDLERS`, one case in the
matrix below. Nothing else in the loop moves.

#### Testing frame types: use a probe frame

`tests/test_ws_frames.py` pins every dispatch decision. Writing it turned up the
trap worth remembering: **most accepted frames answer nothing.** The socket only
speaks when a turn runs or a prompt resolves, so a `receive_json()` after an
accepted frame blocks forever — the test hangs instead of failing.

The fix is a probe: after the candidate frame, send `{"type": "__probe__"}`,
which is never a known type and therefore always answered with
`input_rejected`. Whatever arrives *before* that rejection belongs to the
candidate, so "accepted silently" and "never handled" become distinguishable
without sleeps or timeouts. Keep that shape for any new case; the matrix is
deliberately shallow (dispatch decision only — deeper effects live in
`tests/test_server.py`).

## Auth: the launch token

The sidecar authenticates every request (HTTP header and WebSocket
subprotocol) with a per-launch token:

- Checked by middleware `require_sidecar_token` (`app.py:214`) using a
  constant-time compare. Sent as `X-OpenWorker-Token` header, or as the
  `openworker` WebSocket subprotocol (CORS doesn't gate WebSockets, so the
  origin allowlist alone is not enough).
- **Desktop mode**: Tauri generates the token in memory and passes it to both
  children via environment (`run.py::_ensure_api_token`, `run.py:128`); it is
  never written to disk.
- **Standalone mode**: the token is written to a user-only file
  `<state-dir>/sidecar-<port>.token` (deleted at shutdown) so the Vite dev UI
  and curl can authenticate.

Additional hardening (browser-origin attacks against a loopback server):

- Origin allowlist: `tauri://localhost`, `127.0.0.1`, `tauri.localhost`
  (`app.py:34`) — DNS-binding aware.
- WS input is rate-limited and frame-size-capped (`app.py:47-54`).

## SessionManager responsibilities

Beyond routing, the manager owns the processes-of-record:

- engine lifecycle per session (build via `agent.py::build_engine`);
- `_dispatch_inbound` — channel messages from the connector gateway are
  buffered per channel and fanned out to subscribed sessions;
- `_route_mention` (the §31 mention router) — an @-mention spawns a dedicated
  per-thread coworker session with a standing `send_message` grant for that
  thread target and pre-loaded channel context; DMs route to the designated
  DM session or park as unrouted;
- `_run_scheduled_task` — each automation fire is a fresh persisted
  conversation thread (engine built per run, continuable afterwards), with an
  optional completion notification to the task's `notify_target`;
- team wake plumbing — rolling per-hour worker-wake budget, staleness digests,
  and a check-in backstop (see [teams.md](teams.md)).

## Startup and state

`run.py` parses config (`docs/config.example.toml` documents the keys),
ensures the token, and hands `create_app(manager)` to uvicorn. The state files
the manager owns are inventoried in
[architecture.md](architecture.md#state-directory-inventory); every store
parses defensively so a corrupt row degrades instead of killing the server.
