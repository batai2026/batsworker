"""Connector/plugin marketplace — the open ecosystem door.

Two kinds of extensions are offered:

- **Curated MCP servers** — the true open ecosystem: installing one adds an MCP
  server to the local registry (existing ``/v1/mcp`` API), and its tools become
  available to agents immediately. No cloud, no plugin SDK.
- **Community plugins** — JSON-serializable connector descriptors (fields,
  instructions, auth) that install as new connector cards in the local
  registry. They manage credentials like any manual connector; tool surfaces can
  be attached later via MCP or a future plugin SDK.

Installed plugins persist as ``<state_dir>/connector-plugins/<name>.json`` and
are re-registered automatically at import time (see ``load_installed_plugins``),
so a plugin survives server restarts without any database.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from ..secrets import state_dir
from .descriptors import ConnectorDescriptor, Field, get_descriptor, register_descriptor

# -- curated MCP servers -----------------------------------------------------------
# name/label/blurb/config mirror the GUI's MCP_PRESETS shape; "Add" calls the
# standard MCP add endpoint so the server lands in the user's Custom · MCP group.
CURATED_MCP_SERVERS: list[dict[str, Any]] = [
    {
        "name": "granola",
        "label": "Granola",
        "blurb": "Meeting notes & transcripts — sign in with your Granola account.",
        "config": {"type": "http", "url": "https://mcp.granola.ai/mcp", "auth": "oauth"},
    },
    {
        "name": "linear",
        "label": "Linear",
        "blurb": "Search, read, and create Linear issues through Linear's MCP server.",
        "config": {"type": "http", "url": "https://mcp.linear.app/mcp", "auth": "oauth"},
    },
]

# -- community plugins --------------------------------------------------------------
# Plain data only: name/title/icon/blurb/brand_color/logo/aliases/category + the
# same field/instruction shape as a ConnectorDescriptor. Plugins choose names
# that do NOT collide with shipped connectors (collisions are rejected).
COMMUNITY_PLUGINS: list[dict[str, Any]] = [
    {
        "name": "intercom",
        "title": "Intercom",
        "icon": "☎",
        "blurb": "Search conversations and contacts in Intercom support.",
        "brand_color": "#1f8ded",
        "logo": "intercom",
        "aliases": ("support", "helpdesk", "customer support"),
        "category": "Support",
        "auth": "token",
        "fields": [
            {
                "key": "access_token",
                "label": "Access token",
                "secret": True,
                "required": True,
                "help": "Personal access token from Intercom settings.",
                "placeholder": "dG9rL…",
            }
        ],
        "instructions": [
            "In Intercom, go to Settings → Developer hub → Your apps → Authentication.",
            "Create an access token and paste it below.",
        ],
        "risk_notice": "",
    },
    {
        "name": "webflow",
        "title": "Webflow",
        "icon": "❖",
        "blurb": "Read and update Webflow sites, collections, and CMS items.",
        "brand_color": "#4353ff",
        "logo": "webflow",
        "aliases": ("cms", "website", "sites"),
        "category": "Marketing",
        "auth": "api_token",
        "fields": [
            {
                "key": "api_token",
                "label": "API token",
                "secret": True,
                "required": True,
                "help": "Site-level API token from Webflow Dashboard → Integrations.",
                "placeholder": "wf_…",
            }
        ],
        "instructions": [
            "In Webflow, open Dashboard → your site → Integrations → API access.",
            "Generate an API token and paste it below.",
        ],
        "risk_notice": "",
    },
]


def _plugins_dir() -> Path:
    d = state_dir() / "connector-plugins"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _plugin_data(name: str) -> Optional[dict[str, Any]]:
    name = (name or "").strip()
    for p in COMMUNITY_PLUGINS:
        if p["name"] == name:
            return p
    return None


def _public_plugin(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": data["name"],
        "title": data["title"],
        "icon": data.get("icon", ""),
        "blurb": data["blurb"],
        "brand_color": data.get("brand_color", "#6b7280"),
        "logo": data.get("logo", ""),
        "aliases": list(data.get("aliases", ())),
        "category": data.get("category", "Other"),
        "auth": data.get("auth", "token"),
        "fields": data.get("fields", []),
        "instructions": data.get("instructions", []),
        "risk_notice": data.get("risk_notice", ""),
    }


def marketplace_catalog() -> dict[str, Any]:
    """The marketplace payload: curated MCP servers + plugin cards with install state."""
    installed = {p["name"] for p in installed_plugins()}
    return {
        "mcp": list(CURATED_MCP_SERVERS),
        "plugins": [
            {**_public_plugin(p), "installed": p["name"] in installed}
            for p in COMMUNITY_PLUGINS
        ],
    }


# -- persistence -------------------------------------------------------------------


def installed_plugins() -> list[dict[str, Any]]:
    d = _plugins_dir()
    out: list[dict[str, Any]] = []
    for path in sorted(d.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if isinstance(data, dict) and data.get("name"):
            out.append(data)
    return out


def save_plugin(data: dict[str, Any]) -> None:
    d = _plugins_dir()
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in data["name"])
    (d / f"{safe}.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def remove_plugin_file(name: str) -> None:
    d = _plugins_dir()
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in name)
    path = d / f"{safe}.json"
    if path.is_file():
        path.unlink()


# -- install / uninstall -----------------------------------------------------------


def _plugin_to_descriptor(data: dict[str, Any]) -> ConnectorDescriptor:
    return ConnectorDescriptor(
        name=data["name"],
        title=data["title"],
        icon=data.get("icon", ""),
        blurb=data["blurb"],
        auth=data.get("auth", "token"),
        two_way=False,
        brand_color=data.get("brand_color", "#6b7280"),
        logo=data.get("logo", ""),
        aliases=tuple(data.get("aliases", ())),
        fields=[Field(**f) for f in data.get("fields", [])],
        instructions=list(data.get("instructions", [])),
        risk_notice=data.get("risk_notice", ""),
        available=True,
    )


def install_plugin(name: str) -> dict[str, Any]:
    data = _plugin_data(name)
    if data is None:
        return {"ok": False, "error": f"unknown plugin: {name}"}
    if get_descriptor(name) is not None:
        return {
            "ok": False,
            "error": f"a connector named '{name}' already exists in the registry",
        }
    register_descriptor(_plugin_to_descriptor(data))
    save_plugin(data)
    return {"ok": True, "plugin": _public_plugin(data)}


def uninstall_plugin(name: str) -> dict[str, Any]:
    name = (name or "").strip()
    # Remove from the in-memory registry (both the ordered list and the index).
    from .descriptors import DESCRIPTORS, _BY_NAME

    removed = False
    for i, d in enumerate(DESCRIPTORS):
        if d.name == name:
            del DESCRIPTORS[i]
            removed = True
            break
    if removed or name in _BY_NAME:
        _BY_NAME.pop(name, None)
    remove_plugin_file(name)
    return {"ok": True, "name": name}


def load_installed_plugins() -> None:
    """Re-register persisted plugins at startup (called from descriptors import)."""
    for data in installed_plugins():
        if get_descriptor(data.get("name", "")) is None:
            try:
                register_descriptor(_plugin_to_descriptor(data))
            except Exception:
                # A corrupt persisted plugin must not prevent the server from booting.
                continue