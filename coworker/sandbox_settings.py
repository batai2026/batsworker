"""GUI sandbox settings — the user-global sandbox toggle stored as JSON.

The Docker sandbox is configurable via ``config.toml`` (``sandbox_enabled`` etc.),
but editing TOML by hand is not something a GUI can do safely (no TOML writer in
the stdlib). This small JSON store under the state dir holds the same fields and
``build_engine`` treats it as a user-global override on top of ``config.toml`` —
so a user can flip the toggle from Settings without touching config files.

Shape: ``<state-dir>/sandbox.json`` = ``{"enabled": bool, "backend": str,
"image": str, "network": str}``. Absent file ⇒ defaults (sandbox off).
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Optional

from .secrets import state_dir

_DEFAULTS = {
    "enabled": False,
    "backend": "docker",
    "image": "python:3.12-slim",
    "network": "none",
}

_lock = threading.RLock()


def _path() -> Path:
    return state_dir() / "sandbox.json"


def read_sandbox_settings() -> dict:
    with _lock:
        try:
            data = json.loads(_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return dict(_DEFAULTS)
        if not isinstance(data, dict):
            return dict(_DEFAULTS)
        return {**_DEFAULTS, **{k: data[k] for k in _DEFAULTS if k in data}}


def write_sandbox_settings(
    *,
    enabled: Optional[bool] = None,
    image: Optional[str] = None,
    network: Optional[str] = None,
) -> dict:
    with _lock:
        data = read_sandbox_settings()
        if enabled is not None:
            data["enabled"] = bool(enabled)
        if image:
            data["image"] = (image or "").strip() or _DEFAULTS["image"]
        if network is not None:
            data["network"] = (network or "").strip() or _DEFAULTS["network"]
        _path().parent.mkdir(parents=True, exist_ok=True)
        _path().write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return data