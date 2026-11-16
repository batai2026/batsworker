"""Cowork-only connector tools for first-party integrations.

These tools are intentionally local-first: credentials are read from the SecretStore at
execution time and never enter prompts. OAuth-managed setup can later replace the manual
access-token fields without changing the tool surface.
"""

from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from typing import Any, Callable, Optional


from ..secrets import SecretStore
from .browser_automation import make_browser_automation_tools
from .email_tools import make_email_tools
from .tool_defs import connector_for_tool
from .toolkit import (
    _bearer_headers,
    _clamp,
    _attach,
    _profile,
    _request,
    _schema,
)
from .tools_code_hosting import add_github_tools, add_gitlab_tools
from .tools_commerce import add_commerce_tools, add_finance_chat_tools
from .tools_insights_design import add_design_tools, add_insights_tools
from .tools_tracking import add_discord_tools, add_tracking_tools
from .tools_crm import add_clickup_tools, add_close_tools, add_hubspot_tools
from .tools_mail_calendar import add_mail_calendar_tools


# HubSpot-defined association type ids: note → object (v4 default associations).


# --- "Never show agents" enforcement (desktop tool layer, silent to agents) ----


class _TextExtractor(HTMLParser):
    _SKIP = {"script", "style", "noscript", "svg", "head"}

    def __init__(self) -> None:
        super().__init__()
        self._skip = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag in self._SKIP:
            self._skip += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP and self._skip:
            self._skip -= 1

    def handle_data(self, data: str) -> None:
        if not self._skip:
            text = data.strip()
            if text:
                self.parts.append(text)


def _html_to_text(html: str) -> str:
    parser = _TextExtractor()
    try:
        parser.feed(html)
    except Exception:
        pass
    return re.sub(r"\n{3,}", "\n\n", "\n".join(parser.parts))


def make_integration_tools(
    secrets: SecretStore,
    *,
    enabled_connectors: Optional[set[str]] = None,
    enabled_tools: Optional[set[str]] = None,
    roots: Optional[list[Any]] = None,
) -> list[Callable[..., Any]]:
    # Browser upload/screenshot touch local files but classify EXTERNAL, so the engine's
    # root scoping never runs for them — they enforce the granted roots themselves.
    tools: list[Callable[..., Any]] = make_browser_automation_tools(roots=roots)
    # Email needs the session roots: attachment downloads land in the primary scratch
    # and outgoing attachments must resolve inside a granted directory.
    tools.extend(make_email_tools(secrets, roots=roots))

    add_github_tools(tools, secrets, roots=roots)
    add_mail_calendar_tools(tools, secrets)

    add_tracking_tools(tools, secrets)
    add_gitlab_tools(tools, secrets)
    add_discord_tools(tools, secrets)

    add_commerce_tools(tools, secrets)
    add_hubspot_tools(tools, secrets)
    def _dropbox_path(path: str) -> str:
        path = (path or "").strip()
        if path and not path.startswith("/"):
            path = "/" + path
        return path

    def dropbox_search(query: str, max_results: int = 10) -> dict[str, Any]:
        profile, err = _profile(secrets, "dropbox", "access_token")
        if err:
            return err
        return _request(
            "POST",
            "https://api.dropboxapi.com/2/files/search_v2",
            headers=_bearer_headers(profile["access_token"]),
            json={"query": query, "options": {"max_results": _clamp(max_results)}},
        )

    dropbox_search.__name__ = "dropbox_search"
    tools.append(
        _attach(
            dropbox_search,
            _schema(
                "dropbox_search",
                "Search Dropbox files and folders by name/content.",
                {"query": {"type": "string"}, "max_results": {"type": "integer"}},
                ["query"],
            ),
            caps=["dropbox", "read"],
        )
    )

    def dropbox_list_folder(path: str = "") -> dict[str, Any]:
        profile, err = _profile(secrets, "dropbox", "access_token")
        if err:
            return err
        return _request(
            "POST",
            "https://api.dropboxapi.com/2/files/list_folder",
            headers=_bearer_headers(profile["access_token"]),
            json={"path": _dropbox_path(path)},
        )

    dropbox_list_folder.__name__ = "dropbox_list_folder"
    tools.append(
        _attach(
            dropbox_list_folder,
            _schema(
                "dropbox_list_folder",
                "List a Dropbox folder. Empty path is the root.",
                {"path": {"type": "string"}},
                [],
            ),
            caps=["dropbox", "read"],
        )
    )

    def dropbox_read_file(path: str, max_chars: int = 20000) -> dict[str, Any]:
        profile, err = _profile(secrets, "dropbox", "access_token")
        if err:
            return err
        out = _request(
            "POST",
            "https://content.dropboxapi.com/2/files/download",
            headers={
                "Authorization": f"Bearer {profile['access_token']}",
                "Dropbox-API-Arg": json.dumps({"path": _dropbox_path(path)}),
            },
        )
        if "error" in out:
            return out
        text = out["data"] if isinstance(out["data"], str) else str(out["data"])
        cap = max(1, min(int(max_chars or 20000), 100000))
        return {"path": path, "text": text[:cap], "truncated": len(text) > cap}

    dropbox_read_file.__name__ = "dropbox_read_file"
    tools.append(
        _attach(
            dropbox_read_file,
            _schema(
                "dropbox_read_file",
                "Read a text file from Dropbox by path.",
                {"path": {"type": "string"}, "max_chars": {"type": "integer"}},
                ["path"],
            ),
            caps=["dropbox", "read"],
        )
    )

    def box_search(query: str, max_results: int = 10) -> dict[str, Any]:
        profile, err = _profile(secrets, "box", "access_token")
        if err:
            return err
        return _request(
            "GET",
            "https://api.box.com/2.0/search",
            headers=_bearer_headers(profile["access_token"]),
            params={"query": query, "limit": _clamp(max_results)},
        )

    box_search.__name__ = "box_search"
    tools.append(
        _attach(
            box_search,
            _schema(
                "box_search",
                "Search Box files and folders.",
                {"query": {"type": "string"}, "max_results": {"type": "integer"}},
                ["query"],
            ),
            caps=["box", "read"],
        )
    )

    def box_list_folder(folder_id: str = "0") -> dict[str, Any]:
        profile, err = _profile(secrets, "box", "access_token")
        if err:
            return err
        return _request(
            "GET",
            f"https://api.box.com/2.0/folders/{folder_id}/items",
            headers=_bearer_headers(profile["access_token"]),
        )

    box_list_folder.__name__ = "box_list_folder"
    tools.append(
        _attach(
            box_list_folder,
            _schema(
                "box_list_folder",
                "List items in a Box folder. Folder '0' is the root.",
                {"folder_id": {"type": "string"}},
                [],
            ),
            caps=["box", "read"],
        )
    )

    def box_read_file(file_id: str, max_chars: int = 20000) -> dict[str, Any]:
        profile, err = _profile(secrets, "box", "access_token")
        if err:
            return err
        out = _request(
            "GET",
            f"https://api.box.com/2.0/files/{file_id}/content",
            headers=_bearer_headers(profile["access_token"]),
        )
        if "error" in out:
            return out
        text = out["data"] if isinstance(out["data"], str) else str(out["data"])
        cap = max(1, min(int(max_chars or 20000), 100000))
        return {"file_id": file_id, "text": text[:cap], "truncated": len(text) > cap}

    box_read_file.__name__ = "box_read_file"
    tools.append(
        _attach(
            box_read_file,
            _schema(
                "box_read_file",
                "Read a text file from Box by file ID.",
                {"file_id": {"type": "string"}, "max_chars": {"type": "integer"}},
                ["file_id"],
            ),
            caps=["box", "read"],
        )
    )

    add_finance_chat_tools(tools, secrets)
    add_insights_tools(tools, secrets)

    add_clickup_tools(tools, secrets)
    add_close_tools(tools, secrets)
    add_design_tools(tools, secrets)

    if enabled_connectors is not None:
        tools = [
            t for t in tools if connector_for_tool(t.__name__) in enabled_connectors
        ]
    if enabled_tools is not None:
        tools = [t for t in tools if t.__name__ in enabled_tools]
    return tools
