"""HubSpot, ClickUp and Close tools (batch C of the connector tool split).

Moved out of `integration_tools.py` verbatim — same names, schemas, gating and registration
order; `tests/test_integration_tool_catalog.py` freezes that catalog.

These three are "CRM-shaped": a portal/workspace selector in every call (`portal` /
`team_id` / none), read tools that gate at the connector rather than per call, and write
tools that require approval. `_hubspot_result` normalises HubSpot's error envelope into the
`{"ok": ..., "error": ...}` shape the rest of the runtime speaks.
"""

from __future__ import annotations

import json
from typing import Any, Optional
from urllib.parse import quote

from ..secrets import SecretStore
from . import toolkit
from .toolkit import _attach, _bearer_headers, _clamp, _profile, _schema


def _hubspot_profile(
    secrets: SecretStore, portal: str = ""
) -> tuple[str, str, Optional[dict[str, str]]]:
    """(portal name, bearer token, err) for the requested — or default — portal,
    with a managed token refreshed in place. Multi-portal: `hubspot:portal:<id>`."""
    from . import hubspot_portals

    hub_id, key, profile = hubspot_portals.resolve(secrets, portal)
    if profile is None:
        hint = (
            f"no hubspot portal matching {portal!r}"
            if portal
            else "hubspot is not connected"
        )
        return "", "", {"error": hint}
    if profile.get("managed"):
        from ..cloud import ensure_fresh_connector_token
        from ..config import load_config

        ensure_fresh_connector_token(secrets, load_config(), "hubspot", profile_key=key)
        profile = secrets.get(key) or profile
    # Manual private-app profiles carry `token`; managed OAuth carries
    # `access_token` (which is what the broker refresh rotates).
    token = profile.get("token") or profile.get("access_token") or ""
    if not token:
        return "", "", {"error": f"hubspot portal {hub_id} has no usable token"}
    name = str(profile.get("account") or f"portal {hub_id}")
    return name, token, None

def _hubspot_result(secrets: SecretStore, portal_name: str, result: dict) -> dict:
    """Post-process a CRM read: strip denylisted fields (model-facing policy)
    and name the portal so transcripts/approvals say where data came from.
    Stripped-value counts ride `_display` → audit; agents see nothing."""
    from . import hubspot_portals

    if not result.get("ok"):
        return result
    hidden = hubspot_portals.get_hidden_fields(secrets)
    data, removed = hubspot_portals.strip_hidden(result.get("data"), hidden)
    out = {**result, "data": data, "portal": portal_name}
    if removed:
        out["_display"] = {"hidden_fields": removed, "connector": "hubspot"}
    return out


def add_hubspot_tools(tools: list, secrets: SecretStore) -> None:
    """Register the HubSpot tools on `tools` (order preserved)."""
    _PORTAL_PROP = {
        "type": "string",
        "description": "Portal (hub id or name) to use; omit for the default portal.",
    }
    _HS_KINDS = ("contacts", "companies", "deals", "tickets")

    def hubspot_search(
        query: str = "",
        object_type: str = "contacts",
        max_results: int = 10,
        properties: str = "",
        filters: str = "",
        portal: str = "",
    ) -> dict[str, Any]:
        name, token, err = _hubspot_profile(secrets, portal)
        if err:
            return err
        kind = object_type if object_type in _HS_KINDS else "contacts"
        # The search API only returns HubSpot's default properties unless asked,
        # and free-text `query` never matches custom properties — so property
        # filters are the only way to select on them (e.g. an "org_type" field).
        body: dict[str, Any] = {"limit": _clamp(max_results, ceiling=100)}
        if query:
            body["query"] = query
        if properties:
            body["properties"] = [p.strip() for p in properties.split(",") if p.strip()]
        if filters:
            try:
                parsed = json.loads(filters)
            except ValueError:
                return {"error": "filters must be a JSON array of filter objects"}
            if not isinstance(parsed, list) or not all(
                isinstance(f, dict) and f.get("property") and f.get("operator")
                for f in parsed
            ):
                return {"error": "each filter needs at least 'property' and 'operator'"}
            body["filterGroups"] = [{"filters": parsed}]
        if not query and not filters:
            return {"error": "provide a query, filters, or both"}
        result = toolkit._request(
            "POST",
            f"https://api.hubapi.com/crm/v3/objects/{kind}/search",
            headers=_bearer_headers(token),
            json=body,
        )
        return _hubspot_result(secrets, name, result)

    hubspot_search.__name__ = "hubspot_search"
    tools.append(
        _attach(
            hubspot_search,
            _schema(
                "hubspot_search",
                "Search HubSpot CRM contacts, companies, deals, or tickets (object_type). "
                "Custom properties are only returned if named in `properties`, and only "
                "matchable via `filters` (free-text query searches default fields only).",
                {
                    "query": {"type": "string", "description": "Free-text search"},
                    "object_type": {"type": "string"},
                    "max_results": {"type": "integer"},
                    "properties": {
                        "type": "string",
                        "description": "Comma-separated property names to return "
                        "(include custom properties here)",
                    },
                    "filters": {
                        "type": "string",
                        "description": 'JSON array of {"property", "operator", "value"} '
                        "objects, ANDed together. Operators: EQ, NEQ, LT, LTE, GT, GTE, "
                        "CONTAINS_TOKEN, HAS_PROPERTY, NOT_HAS_PROPERTY, IN",
                    },
                    "portal": _PORTAL_PROP,
                },
                [],
            ),
            caps=["hubspot", "read"],
        )
    )

    def hubspot_get_object(
        object_type: str,
        object_id: str,
        properties: str = "",
        associations: str = "",
        portal: str = "",
    ) -> dict[str, Any]:
        name, token, err = _hubspot_profile(secrets, portal)
        if err:
            return err
        kind = object_type if object_type in _HS_KINDS else "contacts"
        params: dict[str, Any] = {}
        if properties:
            params["properties"] = properties  # API takes the comma string as-is
        if associations:
            params["associations"] = associations
        result = toolkit._request(
            "GET",
            f"https://api.hubapi.com/crm/v3/objects/{kind}/{object_id}",
            headers=_bearer_headers(token),
            params=params or None,
        )
        return _hubspot_result(secrets, name, result)

    hubspot_get_object.__name__ = "hubspot_get_object"
    tools.append(
        _attach(
            hubspot_get_object,
            _schema(
                "hubspot_get_object",
                "Read a HubSpot CRM record by ID. Custom properties are only "
                "returned if named in `properties`; pass `associations` to also get "
                "linked record ids.",
                {
                    "object_type": {"type": "string"},
                    "object_id": {"type": "string"},
                    "properties": {
                        "type": "string",
                        "description": "Comma-separated property names to return",
                    },
                    "associations": {
                        "type": "string",
                        "description": "Comma-separated object types to return "
                        "associated ids for (e.g. companies,contacts)",
                    },
                    "portal": _PORTAL_PROP,
                },
                ["object_type", "object_id"],
            ),
            caps=["hubspot", "read"],
        )
    )

    def hubspot_create_contact(
        email: str, first_name: str = "", last_name: str = "", portal: str = ""
    ) -> dict[str, Any]:
        name, token, err = _hubspot_profile(secrets, portal)
        if err:
            return err
        props = {"email": email}
        if first_name:
            props["firstname"] = first_name
        if last_name:
            props["lastname"] = last_name
        result = toolkit._request(
            "POST",
            "https://api.hubapi.com/crm/v3/objects/contacts",
            headers=_bearer_headers(token),
            json={"properties": props},
        )
        return _hubspot_result(secrets, name, result)

    hubspot_create_contact.__name__ = "hubspot_create_contact"
    tools.append(
        _attach(
            hubspot_create_contact,
            _schema(
                "hubspot_create_contact",
                "Create a HubSpot contact. Requires user approval; the `portal` "
                "argument names the portal on the approval card.",
                {
                    "email": {"type": "string"},
                    "first_name": {"type": "string"},
                    "last_name": {"type": "string"},
                    "portal": _PORTAL_PROP,
                },
                ["email"],
            ),
            approval=True,
            caps=["hubspot", "write"],
        )
    )

    def hubspot_update_object(
        object_type: str, object_id: str, properties: dict, portal: str = ""
    ) -> dict[str, Any]:
        name, token, err = _hubspot_profile(secrets, portal)
        if err:
            return err
        kind = object_type if object_type in _HS_KINDS else "contacts"
        if not isinstance(properties, dict) or not properties:
            return {"error": "properties must be a non-empty object"}
        result = toolkit._request(
            "PATCH",
            f"https://api.hubapi.com/crm/v3/objects/{kind}/{object_id}",
            headers=_bearer_headers(token),
            json={"properties": properties},
        )
        return _hubspot_result(secrets, name, result)

    hubspot_update_object.__name__ = "hubspot_update_object"
    tools.append(
        _attach(
            hubspot_update_object,
            _schema(
                "hubspot_update_object",
                "Update properties on a HubSpot CRM record (no deletes exist). "
                "Requires user approval.",
                {
                    "object_type": {"type": "string"},
                    "object_id": {"type": "string"},
                    "properties": {"type": "object"},
                    "portal": _PORTAL_PROP,
                },
                ["object_type", "object_id", "properties"],
            ),
            approval=True,
            caps=["hubspot", "write"],
        )
    )

    def hubspot_log_note(
        object_type: str, object_id: str, note: str, portal: str = ""
    ) -> dict[str, Any]:
        name, token, err = _hubspot_profile(secrets, portal)
        if err:
            return err
        kind = object_type if object_type in _HS_KINDS else "contacts"
        # Note engagement associated to the record (association type ids are
        # HubSpot-defined per object; v4 default associations handle the rest).
        result = toolkit._request(
            "POST",
            "https://api.hubapi.com/crm/v3/objects/notes",
            headers=_bearer_headers(token),
            json={
                "properties": {
                    "hs_note_body": note,
                    "hs_timestamp": _now_ms(),
                },
                "associations": [
                    {
                        "to": {"id": object_id},
                        "types": [
                            {
                                "associationCategory": "HUBSPOT_DEFINED",
                                "associationTypeId": _HS_NOTE_ASSOC[kind],
                            }
                        ],
                    }
                ],
            },
        )
        return _hubspot_result(secrets, name, result)

    hubspot_log_note.__name__ = "hubspot_log_note"
    tools.append(
        _attach(
            hubspot_log_note,
            _schema(
                "hubspot_log_note",
                "Log a note on a HubSpot record's timeline. Requires user approval.",
                {
                    "object_type": {"type": "string"},
                    "object_id": {"type": "string"},
                    "note": {"type": "string"},
                    "portal": _PORTAL_PROP,
                },
                ["object_type", "object_id", "note"],
            ),
            approval=True,
            caps=["hubspot", "write"],
        )
    )

    def hubspot_create_task(
        title: str, due: str = "", notes: str = "", portal: str = ""
    ) -> dict[str, Any]:
        name, token, err = _hubspot_profile(secrets, portal)
        if err:
            return err
        props: dict[str, Any] = {
            "hs_task_subject": title,
            "hs_task_status": "NOT_STARTED",
            "hs_timestamp": due or _now_ms(),
        }
        if notes:
            props["hs_task_body"] = notes
        result = toolkit._request(
            "POST",
            "https://api.hubapi.com/crm/v3/objects/tasks",
            headers=_bearer_headers(token),
            json={"properties": props},
        )
        return _hubspot_result(secrets, name, result)

    hubspot_create_task.__name__ = "hubspot_create_task"
    tools.append(
        _attach(
            hubspot_create_task,
            _schema(
                "hubspot_create_task",
                "Create a HubSpot task (due = epoch ms or ISO date). Requires user approval.",
                {
                    "title": {"type": "string"},
                    "due": {"type": "string"},
                    "notes": {"type": "string"},
                    "portal": _PORTAL_PROP,
                },
                ["title"],
            ),
            approval=True,
            caps=["hubspot", "write"],
        )
    )

def add_clickup_tools(tools: list, secrets: SecretStore) -> None:
    """Register the ClickUp tools on `tools` (order preserved)."""
    def clickup_list_teams() -> dict[str, Any]:
        profile, err = _profile(secrets, "clickup", "api_token")
        if err:
            return err
        return toolkit._request(
            "GET", f"{_CLICKUP}/team", headers={"Authorization": profile["api_token"]}
        )

    clickup_list_teams.__name__ = "clickup_list_teams"
    tools.append(
        _attach(
            clickup_list_teams,
            _schema(
                "clickup_list_teams",
                "List ClickUp workspaces (team ids are needed to browse spaces).",
                {},
                [],
            ),
            caps=["clickup", "read"],
        )
    )

    def clickup_list_spaces(team_id: str) -> dict[str, Any]:
        profile, err = _profile(secrets, "clickup", "api_token")
        if err:
            return err
        return toolkit._request(
            "GET",
            f"{_CLICKUP}/team/{quote(team_id)}/space",
            headers={"Authorization": profile["api_token"]},
        )

    clickup_list_spaces.__name__ = "clickup_list_spaces"
    tools.append(
        _attach(
            clickup_list_spaces,
            _schema(
                "clickup_list_spaces",
                "List spaces in a ClickUp workspace.",
                {"team_id": {"type": "string"}},
                ["team_id"],
            ),
            caps=["clickup", "read"],
        )
    )

    def clickup_list_lists(space_id: str) -> dict[str, Any]:
        profile, err = _profile(secrets, "clickup", "api_token")
        if err:
            return err
        return toolkit._request(
            "GET",
            f"{_CLICKUP}/space/{quote(space_id)}/list",
            headers={"Authorization": profile["api_token"]},
        )

    clickup_list_lists.__name__ = "clickup_list_lists"
    tools.append(
        _attach(
            clickup_list_lists,
            _schema(
                "clickup_list_lists",
                "List folderless lists in a ClickUp space (list ids hold the tasks).",
                {"space_id": {"type": "string"}},
                ["space_id"],
            ),
            caps=["clickup", "read"],
        )
    )

    def clickup_list_tasks(
        list_id: str, include_closed: bool = False, max_results: int = 10
    ) -> dict[str, Any]:
        profile, err = _profile(secrets, "clickup", "api_token")
        if err:
            return err
        return toolkit._request(
            "GET",
            f"{_CLICKUP}/list/{quote(list_id)}/task",
            headers={"Authorization": profile["api_token"]},
            params={
                "include_closed": str(bool(include_closed)).lower(),
                "page": 0,
            },
        )

    clickup_list_tasks.__name__ = "clickup_list_tasks"
    tools.append(
        _attach(
            clickup_list_tasks,
            _schema(
                "clickup_list_tasks",
                "List tasks in a ClickUp list.",
                {
                    "list_id": {"type": "string"},
                    "include_closed": {"type": "boolean"},
                    "max_results": {"type": "integer"},
                },
                ["list_id"],
            ),
            caps=["clickup", "read"],
        )
    )

    def clickup_get_task(task_id: str) -> dict[str, Any]:
        profile, err = _profile(secrets, "clickup", "api_token")
        if err:
            return err
        return toolkit._request(
            "GET",
            f"{_CLICKUP}/task/{quote(task_id)}",
            headers={"Authorization": profile["api_token"]},
            params={"include_subtasks": "true"},
        )

    clickup_get_task.__name__ = "clickup_get_task"
    tools.append(
        _attach(
            clickup_get_task,
            _schema(
                "clickup_get_task",
                "Read a ClickUp task (with subtasks) by id.",
                {"task_id": {"type": "string"}},
                ["task_id"],
            ),
            caps=["clickup", "read"],
        )
    )

    def clickup_create_task(
        list_id: str, name: str, description: str = ""
    ) -> dict[str, Any]:
        profile, err = _profile(secrets, "clickup", "api_token")
        if err:
            return err
        return toolkit._request(
            "POST",
            f"{_CLICKUP}/list/{quote(list_id)}/task",
            headers={"Authorization": profile["api_token"]},
            json={"name": name, "description": description},
        )

    clickup_create_task.__name__ = "clickup_create_task"
    tools.append(
        _attach(
            clickup_create_task,
            _schema(
                "clickup_create_task",
                "Create a ClickUp task in a list. Requires user approval.",
                {
                    "list_id": {"type": "string"},
                    "name": {"type": "string"},
                    "description": {"type": "string"},
                },
                ["list_id", "name"],
            ),
            approval=True,
            caps=["clickup", "write"],
        )
    )

    def clickup_update_task(
        task_id: str, name: str = "", description: str = "", status: str = ""
    ) -> dict[str, Any]:
        profile, err = _profile(secrets, "clickup", "api_token")
        if err:
            return err
        body: dict[str, Any] = {}
        if name:
            body["name"] = name
        if description:
            body["description"] = description
        if status:
            body["status"] = status
        if not body:
            return {"error": "nothing to update: pass name, description, or status"}
        return toolkit._request(
            "PUT",
            f"{_CLICKUP}/task/{quote(task_id)}",
            headers={"Authorization": profile["api_token"]},
            json=body,
        )

    clickup_update_task.__name__ = "clickup_update_task"
    tools.append(
        _attach(
            clickup_update_task,
            _schema(
                "clickup_update_task",
                "Update a ClickUp task's name, description, or status. Requires user approval.",
                {
                    "task_id": {"type": "string"},
                    "name": {"type": "string"},
                    "description": {"type": "string"},
                    "status": {"type": "string"},
                },
                ["task_id"],
            ),
            approval=True,
            caps=["clickup", "write"],
        )
    )

    def clickup_add_comment(task_id: str, text: str) -> dict[str, Any]:
        profile, err = _profile(secrets, "clickup", "api_token")
        if err:
            return err
        return toolkit._request(
            "POST",
            f"{_CLICKUP}/task/{quote(task_id)}/comment",
            headers={"Authorization": profile["api_token"]},
            json={"comment_text": text},
        )

    clickup_add_comment.__name__ = "clickup_add_comment"
    tools.append(
        _attach(
            clickup_add_comment,
            _schema(
                "clickup_add_comment",
                "Comment on a ClickUp task. Requires user approval.",
                {"task_id": {"type": "string"}, "text": {"type": "string"}},
                ["task_id", "text"],
            ),
            approval=True,
            caps=["clickup", "write"],
        )
    )


# --- Close --------------------------------------------------------------

_CLOSE = "https://api.close.com/api/v1"

def _close_auth(profile: dict[str, Any]) -> tuple[str, str]:
    # HTTP basic: API key as username, blank password.
    return (str(profile.get("api_key", "")), "")

def add_close_tools(tools: list, secrets: SecretStore) -> None:
    """Register the Close tools on `tools` (order preserved)."""
    def close_search_leads(query: str, max_results: int = 10) -> dict[str, Any]:
        profile, err = _profile(secrets, "close", "api_key")
        if err:
            return err
        return toolkit._request(
            "GET",
            f"{_CLOSE}/lead/",
            auth=_close_auth(profile),
            params={"query": query, "_limit": _clamp(max_results)},
        )

    close_search_leads.__name__ = "close_search_leads"
    tools.append(
        _attach(
            close_search_leads,
            _schema(
                "close_search_leads",
                'Search Close leads (supports Close\'s search syntax, e.g. "status:potential acme").',
                {"query": {"type": "string"}, "max_results": {"type": "integer"}},
                ["query"],
            ),
            caps=["close", "read"],
        )
    )

    def close_get_lead(lead_id: str) -> dict[str, Any]:
        profile, err = _profile(secrets, "close", "api_key")
        if err:
            return err
        return toolkit._request(
            "GET", f"{_CLOSE}/lead/{quote(lead_id)}/", auth=_close_auth(profile)
        )

    close_get_lead.__name__ = "close_get_lead"
    tools.append(
        _attach(
            close_get_lead,
            _schema(
                "close_get_lead",
                "Read a Close lead (contacts, opportunities, addresses) by id.",
                {"lead_id": {"type": "string"}},
                ["lead_id"],
            ),
            caps=["close", "read"],
        )
    )

    def close_list_opportunities(
        lead_id: str = "", max_results: int = 10
    ) -> dict[str, Any]:
        profile, err = _profile(secrets, "close", "api_key")
        if err:
            return err
        params: dict[str, Any] = {"_limit": _clamp(max_results)}
        if lead_id:
            params["lead_id"] = lead_id
        return toolkit._request(
            "GET", f"{_CLOSE}/opportunity/", auth=_close_auth(profile), params=params
        )

    close_list_opportunities.__name__ = "close_list_opportunities"
    tools.append(
        _attach(
            close_list_opportunities,
            _schema(
                "close_list_opportunities",
                "List Close opportunities, optionally for one lead.",
                {"lead_id": {"type": "string"}, "max_results": {"type": "integer"}},
                [],
            ),
            caps=["close", "read"],
        )
    )

    def close_create_lead(
        name: str, contact_name: str = "", contact_email: str = ""
    ) -> dict[str, Any]:
        profile, err = _profile(secrets, "close", "api_key")
        if err:
            return err
        body: dict[str, Any] = {"name": name}
        if contact_name or contact_email:
            contact: dict[str, Any] = {"name": contact_name}
            if contact_email:
                contact["emails"] = [{"email": contact_email}]
            body["contacts"] = [contact]
        return toolkit._request("POST", f"{_CLOSE}/lead/", auth=_close_auth(profile), json=body)

    close_create_lead.__name__ = "close_create_lead"
    tools.append(
        _attach(
            close_create_lead,
            _schema(
                "close_create_lead",
                "Create a Close lead (company), optionally with one contact. Requires user approval.",
                {
                    "name": {"type": "string"},
                    "contact_name": {"type": "string"},
                    "contact_email": {"type": "string"},
                },
                ["name"],
            ),
            approval=True,
            caps=["close", "write"],
        )
    )

    def close_update_opportunity(
        opportunity_id: str, status_id: str = "", note: str = ""
    ) -> dict[str, Any]:
        profile, err = _profile(secrets, "close", "api_key")
        if err:
            return err
        body: dict[str, Any] = {}
        if status_id:
            body["status_id"] = status_id
        if note:
            body["note"] = note
        if not body:
            return {"error": "nothing to update: pass status_id or note"}
        return toolkit._request(
            "PUT",
            f"{_CLOSE}/opportunity/{quote(opportunity_id)}/",
            auth=_close_auth(profile),
            json=body,
        )

    close_update_opportunity.__name__ = "close_update_opportunity"
    tools.append(
        _attach(
            close_update_opportunity,
            _schema(
                "close_update_opportunity",
                "Update a Close opportunity's status or note. Requires user approval.",
                {
                    "opportunity_id": {"type": "string"},
                    "status_id": {"type": "string"},
                    "note": {"type": "string"},
                },
                ["opportunity_id"],
            ),
            approval=True,
            caps=["close", "write"],
        )
    )

    def close_log_note(lead_id: str, note: str) -> dict[str, Any]:
        profile, err = _profile(secrets, "close", "api_key")
        if err:
            return err
        return toolkit._request(
            "POST",
            f"{_CLOSE}/activity/note/",
            auth=_close_auth(profile),
            json={"lead_id": lead_id, "note": note},
        )

    close_log_note.__name__ = "close_log_note"
    tools.append(
        _attach(
            close_log_note,
            _schema(
                "close_log_note",
                "Log a note on a Close lead's timeline. Requires user approval.",
                {"lead_id": {"type": "string"}, "note": {"type": "string"}},
                ["lead_id", "note"],
            ),
            approval=True,
            caps=["close", "write"],
        )
    )


def _now_ms() -> int:
    from time import time

    return int(time() * 1000)


_HS_NOTE_ASSOC = {"contacts": 202, "companies": 190, "deals": 214, "tickets": 228}



_CLICKUP = "https://api.clickup.com/api/v2"
