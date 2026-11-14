"""Notion, Attio, product analytics, sales enrichment, design and document tools.

D3 of the connector tool split — everything that was left, moved out of
`integration_tools.py` verbatim (same names, schemas, gating and order; the catalog snapshot
in `tests/test_integration_tool_catalog.py` is the guard).

Two registration functions because the ClickUp/Close calls sit between the halves in the
factory:

* `add_insights_tools` — Notion and Attio (docs + CRM notes, per-workspace accounts),
  PostHog / Mixpanel / Amplitude (product analytics queries), Apollo / Hunter (contact
  enrichment, read-only lookups against a paid API).
* `add_design_tools` — Figma (file tree + exports), Google Drive (read-only; deliberately no
  write scope), DocuSign (envelopes and templates), Canva (designs + export polling).
"""

from __future__ import annotations

import json
from typing import Any, Optional
from urllib.parse import quote

from ..secrets import SecretStore
from . import toolkit
from .toolkit import (
    _GEN_ACCOUNT_PROP,
    _account_profile,
    _acct_result,
    _attach,
    _bearer_headers,
    _clamp,
    _google_headers,
    _profile,
    _schema,
)


def add_insights_tools(tools: list, secrets: SecretStore) -> None:
    """Register the Notion / Attio / analytics / sales tools on `tools`."""
    # -- notion (managed OAuth or integration token, multi-workspace) --

    def _notion_headers(profile: dict[str, Any]) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {profile['access_token']}",
            "Notion-Version": "2022-06-28",
        }

    def _notion_blocks_text(blocks: list[dict]) -> str:
        """Flatten block children to readable lines (rich_text plain_text)."""
        lines = []
        for b in blocks:
            content = b.get(b.get("type", ""), {})
            texts = content.get("rich_text") or content.get("title") or []
            line = "".join(
                t.get("plain_text", "") for t in texts if isinstance(t, dict)
            )
            if line:
                lines.append(line)
        return "\n".join(lines)

    def notion_search(
        query: str, max_results: int = 10, account: str = ""
    ) -> dict[str, Any]:
        aid, profile, err = _account_profile(secrets, "notion", account, "access_token")
        if err:
            return err
        result = toolkit._request(
            "POST",
            "https://api.notion.com/v1/search",
            headers=_notion_headers(profile),
            json={"query": query, "page_size": _clamp(max_results, ceiling=100)},
        )
        return _acct_result(aid, result)

    notion_search.__name__ = "notion_search"
    tools.append(
        _attach(
            notion_search,
            _schema(
                "notion_search",
                "Search Notion pages and databases the integration can see.",
                {
                    "query": {"type": "string"},
                    "max_results": {"type": "integer"},
                    "account": _GEN_ACCOUNT_PROP,
                },
                ["query"],
            ),
            caps=["notion", "read"],
        )
    )

    def notion_read_page(page_id: str, account: str = "") -> dict[str, Any]:
        aid, profile, err = _account_profile(secrets, "notion", account, "access_token")
        if err:
            return err
        page = toolkit._request(
            "GET",
            f"https://api.notion.com/v1/pages/{page_id}",
            headers=_notion_headers(profile),
        )
        if "error" in page:
            return _acct_result(aid, page)
        blocks = toolkit._request(
            "GET",
            f"https://api.notion.com/v1/blocks/{page_id}/children",
            headers=_notion_headers(profile),
            params={"page_size": 100},
        )
        text = (
            _notion_blocks_text((blocks.get("data") or {}).get("results") or [])
            if "error" not in blocks
            else ""
        )
        return _acct_result(
            aid,
            {
                "ok": True,
                "properties": (page.get("data") or {}).get("properties"),
                "url": (page.get("data") or {}).get("url"),
                "text": text,
            },
        )

    notion_read_page.__name__ = "notion_read_page"
    tools.append(
        _attach(
            notion_read_page,
            _schema(
                "notion_read_page",
                "Read a Notion page: properties plus its content flattened to text.",
                {"page_id": {"type": "string"}, "account": _GEN_ACCOUNT_PROP},
                ["page_id"],
            ),
            caps=["notion", "read"],
        )
    )

    def notion_query_database(
        database_id: str,
        filter_json: str = "",
        max_results: int = 10,
        account: str = "",
    ) -> dict[str, Any]:
        aid, profile, err = _account_profile(secrets, "notion", account, "access_token")
        if err:
            return err
        body: dict[str, Any] = {"page_size": _clamp(max_results, ceiling=100)}
        if filter_json:
            try:
                body["filter"] = json.loads(filter_json)
            except ValueError:
                return {"error": "filter_json must be a Notion filter object (JSON)"}
        result = toolkit._request(
            "POST",
            f"https://api.notion.com/v1/databases/{database_id}/query",
            headers=_notion_headers(profile),
            json=body,
        )
        return _acct_result(aid, result)

    notion_query_database.__name__ = "notion_query_database"
    tools.append(
        _attach(
            notion_query_database,
            _schema(
                "notion_query_database",
                "Query a Notion database, optionally with a Notion filter object.",
                {
                    "database_id": {"type": "string"},
                    "filter_json": {"type": "string"},
                    "max_results": {"type": "integer"},
                    "account": _GEN_ACCOUNT_PROP,
                },
                ["database_id"],
            ),
            caps=["notion", "read"],
        )
    )

    def notion_create_page(
        parent_page_id: str, title: str, content: str = "", account: str = ""
    ) -> dict[str, Any]:
        aid, profile, err = _account_profile(secrets, "notion", account, "access_token")
        if err:
            return err
        children = [
            {
                "object": "block",
                "type": "paragraph",
                "paragraph": {"rich_text": [{"text": {"content": line}}]},
            }
            for line in content.splitlines()
            if line.strip()
        ]
        result = toolkit._request(
            "POST",
            "https://api.notion.com/v1/pages",
            headers=_notion_headers(profile),
            json={
                "parent": {"page_id": parent_page_id},
                "properties": {"title": {"title": [{"text": {"content": title}}]}},
                "children": children,
            },
        )
        return _acct_result(aid, result)

    notion_create_page.__name__ = "notion_create_page"
    tools.append(
        _attach(
            notion_create_page,
            _schema(
                "notion_create_page",
                "Create a Notion page under a parent page (plain-text paragraphs).",
                {
                    "parent_page_id": {"type": "string"},
                    "title": {"type": "string"},
                    "content": {"type": "string"},
                    "account": _GEN_ACCOUNT_PROP,
                },
                ["parent_page_id", "title"],
            ),
            approval=True,
            caps=["notion", "write"],
        )
    )

    # -- attio (managed OAuth or API key, multi-workspace) --

    def attio_list_objects(account: str = "") -> dict[str, Any]:
        aid, profile, err = _account_profile(secrets, "attio", account, "access_token")
        if err:
            return err
        result = toolkit._request(
            "GET",
            "https://api.attio.com/v2/objects",
            headers=_bearer_headers(profile["access_token"]),
        )
        return _acct_result(aid, result)

    attio_list_objects.__name__ = "attio_list_objects"
    tools.append(
        _attach(
            attio_list_objects,
            _schema(
                "attio_list_objects",
                "List Attio object types (companies, people, deals, custom).",
                {"account": _GEN_ACCOUNT_PROP},
                [],
            ),
            caps=["attio", "read"],
        )
    )

    def attio_query_records(
        object_type: str,
        filter_json: str = "",
        max_results: int = 10,
        account: str = "",
    ) -> dict[str, Any]:
        aid, profile, err = _account_profile(secrets, "attio", account, "access_token")
        if err:
            return err
        body: dict[str, Any] = {"limit": _clamp(max_results, ceiling=100)}
        if filter_json:
            try:
                body["filter"] = json.loads(filter_json)
            except ValueError:
                return {"error": "filter_json must be an Attio filter object (JSON)"}
        result = toolkit._request(
            "POST",
            f"https://api.attio.com/v2/objects/{object_type}/records/query",
            headers=_bearer_headers(profile["access_token"]),
            json=body,
        )
        return _acct_result(aid, result)

    attio_query_records.__name__ = "attio_query_records"
    tools.append(
        _attach(
            attio_query_records,
            _schema(
                "attio_query_records",
                "List/filter records of an Attio object (e.g. companies, people); "
                "filter_json is an Attio filter object.",
                {
                    "object_type": {"type": "string"},
                    "filter_json": {"type": "string"},
                    "max_results": {"type": "integer"},
                    "account": _GEN_ACCOUNT_PROP,
                },
                ["object_type"],
            ),
            caps=["attio", "read"],
        )
    )

    def attio_get_record(
        object_type: str, record_id: str, account: str = ""
    ) -> dict[str, Any]:
        aid, profile, err = _account_profile(secrets, "attio", account, "access_token")
        if err:
            return err
        result = toolkit._request(
            "GET",
            f"https://api.attio.com/v2/objects/{object_type}/records/{record_id}",
            headers=_bearer_headers(profile["access_token"]),
        )
        return _acct_result(aid, result)

    attio_get_record.__name__ = "attio_get_record"
    tools.append(
        _attach(
            attio_get_record,
            _schema(
                "attio_get_record",
                "Read one Attio record by object type and record id.",
                {
                    "object_type": {"type": "string"},
                    "record_id": {"type": "string"},
                    "account": _GEN_ACCOUNT_PROP,
                },
                ["object_type", "record_id"],
            ),
            caps=["attio", "read"],
        )
    )

    def attio_create_note(
        parent_object: str,
        parent_record_id: str,
        title: str,
        content: str,
        account: str = "",
    ) -> dict[str, Any]:
        aid, profile, err = _account_profile(secrets, "attio", account, "access_token")
        if err:
            return err
        result = toolkit._request(
            "POST",
            "https://api.attio.com/v2/notes",
            headers=_bearer_headers(profile["access_token"]),
            json={
                "data": {
                    "parent_object": parent_object,
                    "parent_record_id": parent_record_id,
                    "title": title,
                    "format": "plaintext",
                    "content": content,
                }
            },
        )
        return _acct_result(aid, result)

    attio_create_note.__name__ = "attio_create_note"
    tools.append(
        _attach(
            attio_create_note,
            _schema(
                "attio_create_note",
                "Log a note on an Attio record (e.g. a company or person).",
                {
                    "parent_object": {"type": "string"},
                    "parent_record_id": {"type": "string"},
                    "title": {"type": "string"},
                    "content": {"type": "string"},
                    "account": _GEN_ACCOUNT_PROP,
                },
                ["parent_object", "parent_record_id", "title", "content"],
            ),
            approval=True,
            caps=["attio", "write"],
        )
    )

    # -- product analytics: posthog / mixpanel / amplitude (manual keys, multi-account) --

    def _posthog_base(profile: dict[str, Any]) -> str:
        return str(profile.get("base_url") or "https://us.posthog.com").rstrip("/")

    def posthog_query(hogql: str, account: str = "") -> dict[str, Any]:
        aid, profile, err = _account_profile(
            secrets, "posthog", account, "api_key", "project_id"
        )
        if err:
            return err
        result = toolkit._request(
            "POST",
            f"{_posthog_base(profile)}/api/projects/{profile['project_id']}/query",
            headers=_bearer_headers(profile["api_key"]),
            json={"query": {"kind": "HogQLQuery", "query": hogql}},
        )
        return _acct_result(aid, result)

    posthog_query.__name__ = "posthog_query"
    tools.append(
        _attach(
            posthog_query,
            _schema(
                "posthog_query",
                "Run a HogQL (SQL-like) query against PostHog analytics, e.g. "
                "SELECT event, count() FROM events WHERE timestamp > now() - "
                "INTERVAL 7 DAY GROUP BY event.",
                {"hogql": {"type": "string"}, "account": _GEN_ACCOUNT_PROP},
                ["hogql"],
            ),
            caps=["posthog", "read"],
        )
    )

    def posthog_list_insights(
        query: str = "", max_results: int = 10, account: str = ""
    ) -> dict[str, Any]:
        aid, profile, err = _account_profile(
            secrets, "posthog", account, "api_key", "project_id"
        )
        if err:
            return err
        params: dict[str, Any] = {"limit": _clamp(max_results)}
        if query:
            params["search"] = query
        result = toolkit._request(
            "GET",
            f"{_posthog_base(profile)}/api/projects/{profile['project_id']}/insights",
            headers=_bearer_headers(profile["api_key"]),
            params=params,
        )
        return _acct_result(aid, result)

    posthog_list_insights.__name__ = "posthog_list_insights"
    tools.append(
        _attach(
            posthog_list_insights,
            _schema(
                "posthog_list_insights",
                "List saved PostHog insights (dashboards' building blocks).",
                {
                    "query": {"type": "string"},
                    "max_results": {"type": "integer"},
                    "account": _GEN_ACCOUNT_PROP,
                },
                [],
            ),
            caps=["posthog", "read"],
        )
    )

    def mixpanel_segmentation(
        event: str,
        from_date: str,
        to_date: str,
        unit: str = "day",
        where: str = "",
        account: str = "",
    ) -> dict[str, Any]:
        aid, profile, err = _account_profile(
            secrets, "mixpanel", account, "username", "secret", "project_id"
        )
        if err:
            return err
        params = {
            "project_id": profile["project_id"],
            "event": event,
            "from_date": from_date,
            "to_date": to_date,
            "unit": (
                unit if unit in ("minute", "hour", "day", "week", "month") else "day"
            ),
        }
        if where:
            params["where"] = where
        result = toolkit._request(
            "GET",
            "https://mixpanel.com/api/query/segmentation",
            params=params,
            auth=(profile["username"], profile["secret"]),
        )
        return _acct_result(aid, result)

    mixpanel_segmentation.__name__ = "mixpanel_segmentation"
    tools.append(
        _attach(
            mixpanel_segmentation,
            _schema(
                "mixpanel_segmentation",
                "Mixpanel event counts over a date range (YYYY-MM-DD), optionally "
                'filtered by a `where` expression like properties["plan"]=="pro".',
                {
                    "event": {"type": "string"},
                    "from_date": {"type": "string"},
                    "to_date": {"type": "string"},
                    "unit": {"type": "string"},
                    "where": {"type": "string"},
                    "account": _GEN_ACCOUNT_PROP,
                },
                ["event", "from_date", "to_date"],
            ),
            caps=["mixpanel", "read"],
        )
    )

    def mixpanel_top_events(max_results: int = 10, account: str = "") -> dict[str, Any]:
        aid, profile, err = _account_profile(
            secrets, "mixpanel", account, "username", "secret", "project_id"
        )
        if err:
            return err
        result = toolkit._request(
            "GET",
            "https://mixpanel.com/api/query/events/top",
            params={
                "project_id": profile["project_id"],
                "type": "general",
                "limit": _clamp(max_results, ceiling=100),
            },
            auth=(profile["username"], profile["secret"]),
        )
        return _acct_result(aid, result)

    mixpanel_top_events.__name__ = "mixpanel_top_events"
    tools.append(
        _attach(
            mixpanel_top_events,
            _schema(
                "mixpanel_top_events",
                "Today's top Mixpanel events by volume.",
                {"max_results": {"type": "integer"}, "account": _GEN_ACCOUNT_PROP},
                [],
            ),
            caps=["mixpanel", "read"],
        )
    )

    def amplitude_active_users(
        start: str, end: str, metric: str = "active", account: str = ""
    ) -> dict[str, Any]:
        aid, profile, err = _account_profile(
            secrets, "amplitude", account, "api_key", "secret_key"
        )
        if err:
            return err
        result = toolkit._request(
            "GET",
            "https://amplitude.com/api/2/users",
            params={
                "m": metric if metric in ("active", "new") else "active",
                "start": start.replace("-", ""),
                "end": end.replace("-", ""),
                "i": 1,
            },
            auth=(profile["api_key"], profile["secret_key"]),
        )
        return _acct_result(aid, result)

    amplitude_active_users.__name__ = "amplitude_active_users"
    tools.append(
        _attach(
            amplitude_active_users,
            _schema(
                "amplitude_active_users",
                "Amplitude daily active or new users between two dates (YYYYMMDD "
                "or YYYY-MM-DD).",
                {
                    "start": {"type": "string"},
                    "end": {"type": "string"},
                    "metric": {"type": "string", "description": "active | new"},
                    "account": _GEN_ACCOUNT_PROP,
                },
                ["start", "end"],
            ),
            caps=["amplitude", "read"],
        )
    )

    def amplitude_event_totals(
        event_type: str, start: str, end: str, account: str = ""
    ) -> dict[str, Any]:
        aid, profile, err = _account_profile(
            secrets, "amplitude", account, "api_key", "secret_key"
        )
        if err:
            return err
        result = toolkit._request(
            "GET",
            "https://amplitude.com/api/2/events/segmentation",
            params={
                "e": json.dumps({"event_type": event_type}),
                "start": start.replace("-", ""),
                "end": end.replace("-", ""),
                "m": "totals",
            },
            auth=(profile["api_key"], profile["secret_key"]),
        )
        return _acct_result(aid, result)

    amplitude_event_totals.__name__ = "amplitude_event_totals"
    tools.append(
        _attach(
            amplitude_event_totals,
            _schema(
                "amplitude_event_totals",
                "Daily totals for one Amplitude event between two dates.",
                {
                    "event_type": {"type": "string"},
                    "start": {"type": "string"},
                    "end": {"type": "string"},
                    "account": _GEN_ACCOUNT_PROP,
                },
                ["event_type", "start", "end"],
            ),
            caps=["amplitude", "read"],
        )
    )

    # -- prospecting/enrichment: apollo / hunter (manual keys, multi-account) --

    def _apollo_headers(profile: dict[str, Any]) -> dict[str, str]:
        return {"X-Api-Key": profile["api_key"], "Content-Type": "application/json"}

    def apollo_enrich_person(
        email: str = "", name: str = "", company_domain: str = "", account: str = ""
    ) -> dict[str, Any]:
        if not email and not name:
            return {"error": "provide an email, a name, or both"}
        aid, profile, err = _account_profile(secrets, "apollo", account, "api_key")
        if err:
            return err
        body: dict[str, Any] = {}
        if email:
            body["email"] = email
        if name:
            body["name"] = name
        if company_domain:
            body["domain"] = company_domain
        result = toolkit._request(
            "POST",
            "https://api.apollo.io/api/v1/people/match",
            headers=_apollo_headers(profile),
            json=body,
        )
        return _acct_result(aid, result)

    apollo_enrich_person.__name__ = "apollo_enrich_person"
    tools.append(
        _attach(
            apollo_enrich_person,
            _schema(
                "apollo_enrich_person",
                "Enrich a person from Apollo: title, company, LinkedIn, location "
                "— by email and/or name (+ optional company domain).",
                {
                    "email": {"type": "string"},
                    "name": {"type": "string"},
                    "company_domain": {"type": "string"},
                    "account": _GEN_ACCOUNT_PROP,
                },
                [],
            ),
            caps=["apollo", "read"],
        )
    )

    def apollo_enrich_company(domain: str, account: str = "") -> dict[str, Any]:
        aid, profile, err = _account_profile(secrets, "apollo", account, "api_key")
        if err:
            return err
        result = toolkit._request(
            "GET",
            "https://api.apollo.io/api/v1/organizations/enrich",
            headers=_apollo_headers(profile),
            params={"domain": domain},
        )
        return _acct_result(aid, result)

    apollo_enrich_company.__name__ = "apollo_enrich_company"
    tools.append(
        _attach(
            apollo_enrich_company,
            _schema(
                "apollo_enrich_company",
                "Enrich a company from Apollo by domain: size, industry, funding, "
                "tech stack.",
                {"domain": {"type": "string"}, "account": _GEN_ACCOUNT_PROP},
                ["domain"],
            ),
            caps=["apollo", "read"],
        )
    )

    def apollo_search_people(
        query: str, max_results: int = 10, account: str = ""
    ) -> dict[str, Any]:
        aid, profile, err = _account_profile(secrets, "apollo", account, "api_key")
        if err:
            return err
        result = toolkit._request(
            "POST",
            "https://api.apollo.io/api/v1/mixed_people/search",
            headers=_apollo_headers(profile),
            json={"q_keywords": query, "page": 1, "per_page": _clamp(max_results)},
        )
        return _acct_result(aid, result)

    apollo_search_people.__name__ = "apollo_search_people"
    tools.append(
        _attach(
            apollo_search_people,
            _schema(
                "apollo_search_people",
                "Keyword-search people in Apollo's B2B database (e.g. 'VP "
                "engineering fintech Berlin').",
                {
                    "query": {"type": "string"},
                    "max_results": {"type": "integer"},
                    "account": _GEN_ACCOUNT_PROP,
                },
                ["query"],
            ),
            caps=["apollo", "read"],
        )
    )

    def _hunter_get(
        profile: dict[str, Any], path: str, params: dict[str, Any]
    ) -> dict[str, Any]:
        return toolkit._request(
            "GET",
            f"https://api.hunter.io/v2/{path}",
            params={**params, "api_key": profile["api_key"]},
        )

    def hunter_domain_search(
        domain: str, max_results: int = 10, account: str = ""
    ) -> dict[str, Any]:
        aid, profile, err = _account_profile(secrets, "hunter", account, "api_key")
        if err:
            return err
        result = _hunter_get(
            profile, "domain-search", {"domain": domain, "limit": _clamp(max_results)}
        )
        return _acct_result(aid, result)

    hunter_domain_search.__name__ = "hunter_domain_search"
    tools.append(
        _attach(
            hunter_domain_search,
            _schema(
                "hunter_domain_search",
                "Find published email addresses for a company domain (Hunter).",
                {
                    "domain": {"type": "string"},
                    "max_results": {"type": "integer"},
                    "account": _GEN_ACCOUNT_PROP,
                },
                ["domain"],
            ),
            caps=["hunter", "read"],
        )
    )

    def hunter_find_email(
        domain: str, first_name: str, last_name: str, account: str = ""
    ) -> dict[str, Any]:
        aid, profile, err = _account_profile(secrets, "hunter", account, "api_key")
        if err:
            return err
        result = _hunter_get(
            profile,
            "email-finder",
            {"domain": domain, "first_name": first_name, "last_name": last_name},
        )
        return _acct_result(aid, result)

    hunter_find_email.__name__ = "hunter_find_email"
    tools.append(
        _attach(
            hunter_find_email,
            _schema(
                "hunter_find_email",
                "Find a person's most likely email address from their name and "
                "company domain (Hunter).",
                {
                    "domain": {"type": "string"},
                    "first_name": {"type": "string"},
                    "last_name": {"type": "string"},
                    "account": _GEN_ACCOUNT_PROP,
                },
                ["domain", "first_name", "last_name"],
            ),
            caps=["hunter", "read"],
        )
    )

    def hunter_verify_email(email: str, account: str = "") -> dict[str, Any]:
        aid, profile, err = _account_profile(secrets, "hunter", account, "api_key")
        if err:
            return err
        return _acct_result(
            aid, _hunter_get(profile, "email-verifier", {"email": email})
        )

    hunter_verify_email.__name__ = "hunter_verify_email"
    tools.append(
        _attach(
            hunter_verify_email,
            _schema(
                "hunter_verify_email",
                "Check whether an email address is deliverable (Hunter).",
                {"email": {"type": "string"}, "account": _GEN_ACCOUNT_PROP},
                ["email"],
            ),
            caps=["hunter", "read"],
        )
    )


def add_design_tools(tools: list, secrets: SecretStore) -> None:
    """Register the Figma / Drive / DocuSign / Canva tools on `tools`."""
    # --- Figma --------------------------------------------------------------

    _FIGMA = "https://api.figma.com/v1"

    def _figma_headers(profile: dict[str, Any]) -> dict[str, str]:
        return {"X-Figma-Token": str(profile.get("access_token", ""))}

    def _figma_summarize(node: dict[str, Any], depth: int) -> dict[str, Any]:
        out = {
            "id": node.get("id"),
            "name": node.get("name"),
            "type": node.get("type"),
        }
        children = node.get("children") or []
        if depth > 0 and children:
            out["children"] = [_figma_summarize(c, depth - 1) for c in children]
        elif children:
            out["child_count"] = len(children)
        return out

    def figma_get_file(file_key: str) -> dict[str, Any]:
        profile, err = _profile(secrets, "figma", "access_token")
        if err:
            return err
        result = toolkit._request(
            "GET",
            f"{_FIGMA}/files/{quote(file_key)}",
            headers=_figma_headers(profile),
            params={"depth": 2},
        )
        if not result.get("ok"):
            return result
        data = result.get("data") or {}
        # The raw file tree is enormous — return pages + top-level frames only.
        doc = data.get("document") or {}
        return {
            "ok": True,
            "name": data.get("name"),
            "last_modified": data.get("lastModified"),
            "pages": [_figma_summarize(p, 1) for p in (doc.get("children") or [])],
        }

    figma_get_file.__name__ = "figma_get_file"
    tools.append(
        _attach(
            figma_get_file,
            _schema(
                "figma_get_file",
                "Read a Figma file's pages and top-level frames (file key is in the URL).",
                {"file_key": {"type": "string"}},
                ["file_key"],
            ),
            caps=["figma", "read"],
        )
    )

    def figma_get_comments(file_key: str) -> dict[str, Any]:
        profile, err = _profile(secrets, "figma", "access_token")
        if err:
            return err
        return toolkit._request(
            "GET",
            f"{_FIGMA}/files/{quote(file_key)}/comments",
            headers=_figma_headers(profile),
        )

    figma_get_comments.__name__ = "figma_get_comments"
    tools.append(
        _attach(
            figma_get_comments,
            _schema(
                "figma_get_comments",
                "List comments on a Figma file.",
                {"file_key": {"type": "string"}},
                ["file_key"],
            ),
            caps=["figma", "read"],
        )
    )

    def figma_post_comment(
        file_key: str, message: str, reply_to: str = ""
    ) -> dict[str, Any]:
        profile, err = _profile(secrets, "figma", "access_token")
        if err:
            return err
        body: dict[str, Any] = {"message": message}
        if reply_to:
            body["comment_id"] = reply_to
        return toolkit._request(
            "POST",
            f"{_FIGMA}/files/{quote(file_key)}/comments",
            headers=_figma_headers(profile),
            json=body,
        )

    figma_post_comment.__name__ = "figma_post_comment"
    tools.append(
        _attach(
            figma_post_comment,
            _schema(
                "figma_post_comment",
                "Comment on a Figma file (optionally replying to a comment). Requires user approval.",
                {
                    "file_key": {"type": "string"},
                    "message": {"type": "string"},
                    "reply_to": {"type": "string"},
                },
                ["file_key", "message"],
            ),
            approval=True,
            caps=["figma", "write"],
        )
    )

    def figma_export_images(
        file_key: str, node_ids: str, format: str = "png", scale: int = 2
    ) -> dict[str, Any]:
        profile, err = _profile(secrets, "figma", "access_token")
        if err:
            return err
        return toolkit._request(
            "GET",
            f"{_FIGMA}/images/{quote(file_key)}",
            headers=_figma_headers(profile),
            params={"ids": node_ids, "format": format, "scale": scale},
        )

    figma_export_images.__name__ = "figma_export_images"
    tools.append(
        _attach(
            figma_export_images,
            _schema(
                "figma_export_images",
                "Render Figma nodes to image URLs (node ids comma-separated; png/svg/pdf).",
                {
                    "file_key": {"type": "string"},
                    "node_ids": {"type": "string"},
                    "format": {"type": "string"},
                    "scale": {"type": "integer"},
                },
                ["file_key", "node_ids"],
            ),
            caps=["figma", "read"],
        )
    )

    # --- Google Drive (read-only; deliberately no write scope) ---------------

    _DRIVE = "https://www.googleapis.com/drive/v3"
    _DRIVE_FIELDS = "files(id,name,mimeType,modifiedTime,size,webViewLink)"
    # Google-native types export to text; everything else downloads as-is.
    _DRIVE_EXPORTS = {
        "application/vnd.google-apps.document": "text/plain",
        "application/vnd.google-apps.spreadsheet": "text/csv",
        "application/vnd.google-apps.presentation": "text/plain",
    }

    def _drive_quote(term: str) -> str:
        return term.replace("\\", "\\\\").replace("'", "\\'")

    def drive_search_files(
        query: str, max_results: int = 10, account: str = ""
    ) -> dict[str, Any]:
        aid, profile, err = _account_profile(
            secrets, "google_drive", account, "access_token"
        )
        if err:
            return err
        q = _drive_quote(query)
        return _acct_result(
            aid,
            toolkit._request(
                "GET",
                f"{_DRIVE}/files",
                headers=_google_headers(profile["access_token"]),
                params={
                    "q": f"(name contains '{q}' or fullText contains '{q}') and trashed=false",
                    "pageSize": _clamp(max_results),
                    "fields": _DRIVE_FIELDS,
                },
            ),
        )

    drive_search_files.__name__ = "drive_search_files"
    tools.append(
        _attach(
            drive_search_files,
            _schema(
                "drive_search_files",
                "Search Google Drive files by name or content.",
                {
                    "query": {"type": "string"},
                    "max_results": {"type": "integer"},
                    "account": _GEN_ACCOUNT_PROP,
                },
                ["query"],
            ),
            caps=["google_drive", "read"],
        )
    )

    def drive_list_folder(
        folder_id: str = "root", max_results: int = 20, account: str = ""
    ) -> dict[str, Any]:
        aid, profile, err = _account_profile(
            secrets, "google_drive", account, "access_token"
        )
        if err:
            return err
        return _acct_result(
            aid,
            toolkit._request(
                "GET",
                f"{_DRIVE}/files",
                headers=_google_headers(profile["access_token"]),
                params={
                    "q": f"'{_drive_quote(folder_id)}' in parents and trashed=false",
                    "pageSize": _clamp(max_results, default=20, ceiling=50),
                    "fields": _DRIVE_FIELDS,
                },
            ),
        )

    drive_list_folder.__name__ = "drive_list_folder"
    tools.append(
        _attach(
            drive_list_folder,
            _schema(
                "drive_list_folder",
                "List a Google Drive folder's contents ('root' for My Drive).",
                {
                    "folder_id": {"type": "string"},
                    "max_results": {"type": "integer"},
                    "account": _GEN_ACCOUNT_PROP,
                },
                [],
            ),
            caps=["google_drive", "read"],
        )
    )

    def drive_read_file(
        file_id: str, max_chars: int = 20000, account: str = ""
    ) -> dict[str, Any]:
        aid, profile, err = _account_profile(
            secrets, "google_drive", account, "access_token"
        )
        if err:
            return err
        headers = _google_headers(profile["access_token"])
        meta = toolkit._request(
            "GET",
            f"{_DRIVE}/files/{quote(file_id)}",
            headers=headers,
            params={"fields": "id,name,mimeType,size"},
        )
        if not meta.get("ok"):
            return _acct_result(aid, meta)
        info = meta.get("data") or {}
        mime = str(info.get("mimeType", ""))
        export_mime = _DRIVE_EXPORTS.get(mime)
        if export_mime:
            body = toolkit._request(
                "GET",
                f"{_DRIVE}/files/{quote(file_id)}/export",
                headers=headers,
                params={"mimeType": export_mime},
            )
        elif mime.startswith("application/vnd.google-apps"):
            return _acct_result(
                aid, {"error": f"cannot read {mime} as text", "file": info}
            )
        else:
            body = toolkit._request(
                "GET",
                f"{_DRIVE}/files/{quote(file_id)}",
                headers=headers,
                params={"alt": "media"},
            )
        if not body.get("ok"):
            return _acct_result(aid, body)
        text = body.get("data")
        if not isinstance(text, str):
            text = json.dumps(text)
        return _acct_result(
            aid,
            {
                "ok": True,
                "file": info,
                "content": text[: max(1, int(max_chars))],
                "truncated": len(text) > max_chars,
            },
        )

    drive_read_file.__name__ = "drive_read_file"
    tools.append(
        _attach(
            drive_read_file,
            _schema(
                "drive_read_file",
                "Read a Drive file as text (Docs/Sheets/Slides export; other text files download).",
                {
                    "file_id": {"type": "string"},
                    "max_chars": {"type": "integer"},
                    "account": _GEN_ACCOUNT_PROP,
                },
                ["file_id"],
            ),
            caps=["google_drive", "read"],
        )
    )

    # --- Docusign -----------------------------------------------------------

    def _docusign_ctx(
        profile: dict[str, Any],
    ) -> tuple[Optional[dict[str, Any]], Optional[dict[str, str]]]:
        """Return {token, base} — discovering and caching account_id + base_uri
        from the OAuth userinfo endpoint on first use."""
        token = str(profile.get("access_token", ""))
        account_id = profile.get("account_id")
        base_uri = profile.get("base_uri")
        if not (account_id and base_uri):
            info = toolkit._request(
                "GET",
                "https://account.docusign.com/oauth/userinfo",
                headers=_bearer_headers(token),
            )
            if not info.get("ok"):
                return None, {
                    "error": "docusign account discovery failed",
                    "details": str(info.get("details") or info.get("error")),
                }
            accounts = (info.get("data") or {}).get("accounts") or []
            chosen = next(
                (a for a in accounts if a.get("is_default")),
                accounts[0] if accounts else None,
            )
            if not chosen:
                return None, {"error": "docusign token has no accounts"}
            account_id = chosen.get("account_id")
            base_uri = chosen.get("base_uri")
            secrets.put(
                "docusign:default",
                {**profile, "account_id": account_id, "base_uri": base_uri},
            )
        return {
            "token": token,
            "base": f"{str(base_uri).rstrip('/')}/restapi/v2.1/accounts/{account_id}",
        }, None

    def docusign_list_envelopes(
        status: str = "", since_days: int = 30
    ) -> dict[str, Any]:
        profile, err = _profile(secrets, "docusign", "access_token")
        if err:
            return err
        ctx, err = _docusign_ctx(profile)
        if err:
            return err
        from datetime import datetime, timedelta, timezone

        params: dict[str, Any] = {
            "from_date": (
                datetime.now(timezone.utc) - timedelta(days=max(1, int(since_days)))
            ).strftime("%Y-%m-%dT%H:%M:%SZ")
        }
        if status:
            params["status"] = status
        return toolkit._request(
            "GET",
            f"{ctx['base']}/envelopes",
            headers=_bearer_headers(ctx["token"]),
            params=params,
        )

    docusign_list_envelopes.__name__ = "docusign_list_envelopes"
    tools.append(
        _attach(
            docusign_list_envelopes,
            _schema(
                "docusign_list_envelopes",
                "List recent Docusign envelopes, optionally by status (sent/delivered/completed/declined/voided).",
                {"status": {"type": "string"}, "since_days": {"type": "integer"}},
                [],
            ),
            caps=["docusign", "read"],
        )
    )

    def docusign_get_envelope(envelope_id: str) -> dict[str, Any]:
        profile, err = _profile(secrets, "docusign", "access_token")
        if err:
            return err
        ctx, err = _docusign_ctx(profile)
        if err:
            return err
        return toolkit._request(
            "GET",
            f"{ctx['base']}/envelopes/{quote(envelope_id)}",
            headers=_bearer_headers(ctx["token"]),
            params={"include": "recipients"},
        )

    docusign_get_envelope.__name__ = "docusign_get_envelope"
    tools.append(
        _attach(
            docusign_get_envelope,
            _schema(
                "docusign_get_envelope",
                "Read a Docusign envelope's status and per-signer progress.",
                {"envelope_id": {"type": "string"}},
                ["envelope_id"],
            ),
            caps=["docusign", "read"],
        )
    )

    def docusign_list_templates(max_results: int = 10) -> dict[str, Any]:
        profile, err = _profile(secrets, "docusign", "access_token")
        if err:
            return err
        ctx, err = _docusign_ctx(profile)
        if err:
            return err
        return toolkit._request(
            "GET",
            f"{ctx['base']}/templates",
            headers=_bearer_headers(ctx["token"]),
            params={"count": _clamp(max_results)},
        )

    docusign_list_templates.__name__ = "docusign_list_templates"
    tools.append(
        _attach(
            docusign_list_templates,
            _schema(
                "docusign_list_templates",
                "List Docusign templates (template ids are needed to send).",
                {"max_results": {"type": "integer"}},
                [],
            ),
            caps=["docusign", "read"],
        )
    )

    def docusign_send_from_template(
        template_id: str,
        recipient_email: str,
        recipient_name: str,
        role_name: str = "Signer",
        subject: str = "",
    ) -> dict[str, Any]:
        profile, err = _profile(secrets, "docusign", "access_token")
        if err:
            return err
        ctx, err = _docusign_ctx(profile)
        if err:
            return err
        body: dict[str, Any] = {
            "templateId": template_id,
            "templateRoles": [
                {
                    "email": recipient_email,
                    "name": recipient_name,
                    "roleName": role_name,
                }
            ],
            "status": "sent",
        }
        if subject:
            body["emailSubject"] = subject
        return toolkit._request(
            "POST",
            f"{ctx['base']}/envelopes",
            headers=_bearer_headers(ctx["token"]),
            json=body,
        )

    docusign_send_from_template.__name__ = "docusign_send_from_template"
    tools.append(
        _attach(
            docusign_send_from_template,
            _schema(
                "docusign_send_from_template",
                "Send a Docusign template to one signer for signature. Requires user approval.",
                {
                    "template_id": {"type": "string"},
                    "recipient_email": {"type": "string"},
                    "recipient_name": {"type": "string"},
                    "role_name": {"type": "string"},
                    "subject": {"type": "string"},
                },
                ["template_id", "recipient_email", "recipient_name"],
            ),
            approval=True,
            caps=["docusign", "write"],
        )
    )

    # --- Canva --------------------------------------------------------------

    _CANVA = "https://api.canva.com/rest/v1"

    def canva_list_designs(query: str = "", max_results: int = 10) -> dict[str, Any]:
        profile, err = _profile(secrets, "canva", "access_token")
        if err:
            return err
        params: dict[str, Any] = {"limit": _clamp(max_results)}
        if query:
            params["query"] = query
        return toolkit._request(
            "GET",
            f"{_CANVA}/designs",
            headers=_bearer_headers(profile["access_token"]),
            params=params,
        )

    canva_list_designs.__name__ = "canva_list_designs"
    tools.append(
        _attach(
            canva_list_designs,
            _schema(
                "canva_list_designs",
                "List (or text-search) Canva designs.",
                {"query": {"type": "string"}, "max_results": {"type": "integer"}},
                [],
            ),
            caps=["canva", "read"],
        )
    )

    def canva_get_design(design_id: str) -> dict[str, Any]:
        profile, err = _profile(secrets, "canva", "access_token")
        if err:
            return err
        return toolkit._request(
            "GET",
            f"{_CANVA}/designs/{quote(design_id)}",
            headers=_bearer_headers(profile["access_token"]),
        )

    canva_get_design.__name__ = "canva_get_design"
    tools.append(
        _attach(
            canva_get_design,
            _schema(
                "canva_get_design",
                "Read a Canva design's metadata (title, pages, urls).",
                {"design_id": {"type": "string"}},
                ["design_id"],
            ),
            caps=["canva", "read"],
        )
    )

    def canva_export_design(design_id: str, format: str = "pdf") -> dict[str, Any]:
        profile, err = _profile(secrets, "canva", "access_token")
        if err:
            return err
        return toolkit._request(
            "POST",
            f"{_CANVA}/exports",
            headers=_bearer_headers(profile["access_token"]),
            json={"design_id": design_id, "format": {"type": format}},
        )

    canva_export_design.__name__ = "canva_export_design"
    tools.append(
        _attach(
            canva_export_design,
            _schema(
                "canva_export_design",
                "Start rendering a Canva design to pdf/png/jpg; returns an export job to poll.",
                {"design_id": {"type": "string"}, "format": {"type": "string"}},
                ["design_id"],
            ),
            caps=["canva", "read"],
        )
    )

    def canva_get_export(export_id: str) -> dict[str, Any]:
        profile, err = _profile(secrets, "canva", "access_token")
        if err:
            return err
        return toolkit._request(
            "GET",
            f"{_CANVA}/exports/{quote(export_id)}",
            headers=_bearer_headers(profile["access_token"]),
        )

    canva_get_export.__name__ = "canva_get_export"
    tools.append(
        _attach(
            canva_get_export,
            _schema(
                "canva_get_export",
                "Check a Canva export job; returns download URLs when finished.",
                {"export_id": {"type": "string"}},
                ["export_id"],
            ),
            caps=["canva", "read"],
        )
    )
