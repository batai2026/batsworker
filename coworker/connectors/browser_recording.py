"""Browser recording → replay: capture the agent's browser tool calls as a
deterministic replay script, save it as a reusable skill.

How it works:
  1. `browser_record_start(name)` opens a recording buffer.
  2. While active, every browser tool call (navigate, click, type, …) is
     appended as `{tool, args}` — the agent's actions become a replay script.
  3. `browser_record_stop()` closes the buffer and returns the action list.
  4. `browser_record_save(name, title)` persists the recording to disk
     (replays/ dir under the state dir) as a skill-compatible JSON.
  5. `browser_replay(name)` re-executes the recorded actions via the same
     browser tools. A failed action stops the replay with the step number —
     the agent can then fall back to model-driven repair (self-healing).

v1 scope: records the agent's own browser tool calls (not human interactions —
that would need CDP injection). Deterministic replay, not fuzzy matching.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

# The browser tools that are recorded (reads like browser_read_page and
# browser_screenshot are skipped — they don't change state and would bloat
# the replay with no-ops).
_RECORDED_TOOLS = {
    "browser_open_url",
    "browser_click",
    "browser_type",
    "browser_select",
    "browser_upload_file",
    "browser_wait",
}


class BrowserRecording:
    """Thread-safe recording buffer for browser tool calls."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._active = False
        self._name: str = ""
        self._actions: List[Dict[str, Any]] = []
        self._started_at: float = 0.0

    def start(self, name: str) -> None:
        with self._lock:
            self._active = True
            self._name = name
            self._actions = []
            self._started_at = time.time()

    def stop(self) -> Optional[List[Dict[str, Any]]]:
        with self._lock:
            if not self._active:
                return None
            self._active = False
            return list(self._actions)

    def record(self, tool: str, args: Dict[str, Any], status: str) -> None:
        with self._lock:
            if not self._active or tool not in _RECORDED_TOOLS or status != "ok":
                return
            self._actions.append(
                {"tool": tool, "args": dict(args), "ts": time.time()}
            )

    @property
    def active(self) -> bool:
        with self._lock:
            return self._active

    @property
    def name(self) -> str:
        with self._lock:
            return self._name

    @property
    def action_count(self) -> int:
        with self._lock:
            return len(self._actions)

    @property
    def actions(self) -> List[Dict[str, Any]]:
        with self._lock:
            return list(self._actions)


# Module-level singleton (the browser session is also a singleton).
_recording = BrowserRecording()


def get_recording() -> BrowserRecording:
    return _recording


# -- persistence -----------------------------------------------------------------


def _replays_dir(state_dir: Path) -> Path:
    d = Path(state_dir) / "browser-replays"
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_recording(state_dir: Path, name: str, actions: List[Dict[str, Any]], title: str = "") -> Path:
    d = _replays_dir(state_dir)
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in name)
    path = d / f"{safe}.json"
    path.write_text(
        json.dumps(
            {"name": name, "title": title or name, "saved_at": time.time(), "actions": actions},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return path


def load_recording(state_dir: Path, name: str) -> Optional[Dict[str, Any]]:
    d = _replays_dir(state_dir)
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in name)
    path = d / f"{safe}.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def list_recordings(state_dir: Path) -> List[Dict[str, Any]]:
    d = _replays_dir(state_dir)
    out = []
    for p in sorted(d.glob("*.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            out.append(
                {
                    "name": data.get("name", p.stem),
                    "title": data.get("title", ""),
                    "action_count": len(data.get("actions", [])),
                    "saved_at": data.get("saved_at", 0),
                }
            )
        except (json.JSONDecodeError, OSError):
            continue
    return out
