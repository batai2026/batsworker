# Browser recording → replay

> Source: `coworker/connectors/browser_recording.py` (recording buffer +
> persistence), `browser_automation.py` (recording interception + tools).

The agent's browser tool calls can be **recorded** into a deterministic replay
script and **saved** as a reusable action sequence. Replay re-executes the
recorded actions through the same Playwright browser session; a failed step
stops the replay with the step number so the agent can fall back to
model-driven repair (self-healing).

## How it works

1. `browser_record_start(name)` — opens a recording buffer.
2. While active, every **state-changing** browser tool call (open_url, click,
   type, select, upload, wait) is appended as `{tool, args}` on success. Read
   tools (read_page, screenshot) are excluded — they don't change state and
   would be no-ops on replay.
3. `browser_record_stop()` — closes the buffer and returns the action list.
4. `browser_record_save(name, title)` — persists to
   `<data-dir>/browser-replays/<name>.json` (approval-gated: installs future
   behavior).
5. `browser_replay(name)` — loads the saved recording and re-executes each
   action through the same browser tool closures. First failure stops the
   replay with `{ok: false, failed_step, hint}` (approval-gated: replays
   navigate and click).

## v1 boundaries

- Records the agent's own browser tool calls — not human interactions (that
  would need CDP injection). The agent figures out the flow once; the user can
  replay it deterministically.
- Deterministic replay with exact selectors — no fuzzy re-location in v1. A
  failed step stops cleanly so the agent can repair via the normal tools.
- Recordings are session-local (the browser session is a singleton per
  server); saved recordings persist to disk and survive restarts.
