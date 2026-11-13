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
from urllib.parse import quote


from ..secrets import SecretStore
from .browser_automation import make_browser_automation_tools
from .email_tools import make_email_tools
from .tool_defs import connector_for_tool
from .toolkit import (
    _bearer_headers,
    _GEN_ACCOUNT_PROP,
    _clamp,
    _account_profile,
    _acct_result,
    _attach,
    _google_headers,
    _profile,
    _request,
    _schema,
)
from .tools_code_hosting import add_github_tools, add_gitlab_tools
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


def _basic_auth(email: str, token: str) -> tuple[str, str]:
    return (email, token)


def _atlassian_base(profile: dict[str, Any]) -> str:
    return str(profile.get("base_url", "")).rstrip("/")




def _linear_gql(api_key: str, query: str, variables: dict[str, Any]) -> dict[str, Any]:
    return _request(
        "POST",
        "https://api.linear.app/graphql",
        headers={"Authorization": api_key, "Content-Type": "application/json"},
        json={"query": query, "variables": variables},
    )


def _qbo_base(profile: dict[str, Any]) -> str:
    env = str(profile.get("environment", "")).lower()
    host = (
        "sandbox-quickbooks.api.intuit.com"
        if env.startswith("sand")
        else "quickbooks.api.intuit.com"
    )
    return f"https://{host}/v3/company/{profile['realm_id']}"


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

    def jira_search_issues(jql: str, max_results: int = 10) -> dict[str, Any]:
        profile, err = _profile(secrets, "jira", "base_url", "email", "api_token")
        if err:
            return err
        return _request(
            "GET",
            f"{_atlassian_base(profile)}/rest/api/3/search",
            auth=_basic_auth(profile["email"], profile["api_token"]),
            params={"jql": jql, "maxResults": max(1, min(int(max_results or 10), 20))},
        )

    jira_search_issues.__name__ = "jira_search_issues"
    tools.append(
        _attach(
            jira_search_issues,
            _schema(
                "jira_search_issues",
                "Search Jira issues using JQL.",
                {"jql": {"type": "string"}, "max_results": {"type": "integer"}},
                ["jql"],
            ),
            caps=["jira", "read"],
        )
    )

    def jira_get_issue(issue_key: str) -> dict[str, Any]:
        profile, err = _profile(secrets, "jira", "base_url", "email", "api_token")
        if err:
            return err
        return _request(
            "GET",
            f"{_atlassian_base(profile)}/rest/api/3/issue/{issue_key}",
            auth=_basic_auth(profile["email"], profile["api_token"]),
        )

    jira_get_issue.__name__ = "jira_get_issue"
    tools.append(
        _attach(
            jira_get_issue,
            _schema(
                "jira_get_issue",
                "Read a Jira issue.",
                {"issue_key": {"type": "string"}},
                ["issue_key"],
            ),
            caps=["jira", "read"],
        )
    )

    def jira_create_issue(
        project_key: str, issue_type: str, summary: str, description: str = ""
    ) -> dict[str, Any]:
        profile, err = _profile(secrets, "jira", "base_url", "email", "api_token")
        if err:
            return err
        payload = {
            "fields": {
                "project": {"key": project_key},
                "issuetype": {"name": issue_type},
                "summary": summary,
                "description": {
                    "type": "doc",
                    "version": 1,
                    "content": [
                        {
                            "type": "paragraph",
                            "content": [
                                {"type": "text", "text": description or summary}
                            ],
                        }
                    ],
                },
            }
        }
        return _request(
            "POST",
            f"{_atlassian_base(profile)}/rest/api/3/issue",
            auth=_basic_auth(profile["email"], profile["api_token"]),
            json=payload,
        )

    jira_create_issue.__name__ = "jira_create_issue"
    tools.append(
        _attach(
            jira_create_issue,
            _schema(
                "jira_create_issue",
                "Create a Jira issue. Requires user approval.",
                {
                    "project_key": {"type": "string"},
                    "issue_type": {"type": "string"},
                    "summary": {"type": "string"},
                    "description": {"type": "string"},
                },
                ["project_key", "issue_type", "summary"],
            ),
            approval=True,
            caps=["jira", "write"],
        )
    )

    def confluence_search(query: str, max_results: int = 10) -> dict[str, Any]:
        profile, err = _profile(secrets, "confluence", "base_url", "email", "api_token")
        if err:
            return err
        return _request(
            "GET",
            f"{_atlassian_base(profile)}/wiki/rest/api/search",
            auth=_basic_auth(profile["email"], profile["api_token"]),
            params={
                "cql": f'text ~ "{query}"',
                "limit": max(1, min(int(max_results or 10), 20)),
            },
        )

    confluence_search.__name__ = "confluence_search"
    tools.append(
        _attach(
            confluence_search,
            _schema(
                "confluence_search",
                "Search Confluence pages.",
                {"query": {"type": "string"}, "max_results": {"type": "integer"}},
                ["query"],
            ),
            caps=["confluence", "read"],
        )
    )

    def confluence_get_page(page_id: str) -> dict[str, Any]:
        profile, err = _profile(secrets, "confluence", "base_url", "email", "api_token")
        if err:
            return err
        return _request(
            "GET",
            f"{_atlassian_base(profile)}/wiki/rest/api/content/{page_id}",
            auth=_basic_auth(profile["email"], profile["api_token"]),
            params={"expand": "body.storage,version,space"},
        )

    confluence_get_page.__name__ = "confluence_get_page"
    tools.append(
        _attach(
            confluence_get_page,
            _schema(
                "confluence_get_page",
                "Read a Confluence page.",
                {"page_id": {"type": "string"}},
                ["page_id"],
            ),
            caps=["confluence", "read"],
        )
    )

    def confluence_create_page(
        space_key: str, title: str, body: str, parent_id: str = ""
    ) -> dict[str, Any]:
        profile, err = _profile(secrets, "confluence", "base_url", "email", "api_token")
        if err:
            return err
        payload: dict[str, Any] = {
            "type": "page",
            "title": title,
            "space": {"key": space_key},
            "body": {"storage": {"value": body, "representation": "storage"}},
        }
        if parent_id:
            payload["ancestors"] = [{"id": parent_id}]
        return _request(
            "POST",
            f"{_atlassian_base(profile)}/wiki/rest/api/content",
            auth=_basic_auth(profile["email"], profile["api_token"]),
            json=payload,
        )

    confluence_create_page.__name__ = "confluence_create_page"
    tools.append(
        _attach(
            confluence_create_page,
            _schema(
                "confluence_create_page",
                "Create a Confluence page. Body should be Confluence storage-format HTML. Requires user approval.",
                {
                    "space_key": {"type": "string"},
                    "title": {"type": "string"},
                    "body": {"type": "string"},
                    "parent_id": {"type": "string"},
                },
                ["space_key", "title", "body"],
            ),
            approval=True,
            caps=["confluence", "write"],
        )
    )

    def zendesk_search(query: str) -> dict[str, Any]:
        profile, err = _profile(secrets, "zendesk", "subdomain", "email", "api_token")
        if err:
            return err
        return _request(
            "GET",
            f"https://{profile['subdomain']}.zendesk.com/api/v2/search.json",
            auth=_basic_auth(f"{profile['email']}/token", profile["api_token"]),
            params={"query": query},
        )

    zendesk_search.__name__ = "zendesk_search"
    tools.append(
        _attach(
            zendesk_search,
            _schema(
                "zendesk_search",
                "Search Zendesk tickets/users/articles.",
                {"query": {"type": "string"}},
                ["query"],
            ),
            caps=["zendesk", "read"],
        )
    )

    def zendesk_get_ticket(ticket_id: int) -> dict[str, Any]:
        profile, err = _profile(secrets, "zendesk", "subdomain", "email", "api_token")
        if err:
            return err
        return _request(
            "GET",
            f"https://{profile['subdomain']}.zendesk.com/api/v2/tickets/{ticket_id}.json",
            auth=_basic_auth(f"{profile['email']}/token", profile["api_token"]),
        )

    zendesk_get_ticket.__name__ = "zendesk_get_ticket"
    tools.append(
        _attach(
            zendesk_get_ticket,
            _schema(
                "zendesk_get_ticket",
                "Read a Zendesk ticket.",
                {"ticket_id": {"type": "integer"}},
                ["ticket_id"],
            ),
            caps=["zendesk", "read"],
        )
    )

    def zendesk_create_ticket(
        subject: str, body: str, requester_email: str = ""
    ) -> dict[str, Any]:
        profile, err = _profile(secrets, "zendesk", "subdomain", "email", "api_token")
        if err:
            return err
        ticket: dict[str, Any] = {"subject": subject, "comment": {"body": body}}
        if requester_email:
            ticket["requester"] = {"email": requester_email}
        return _request(
            "POST",
            f"https://{profile['subdomain']}.zendesk.com/api/v2/tickets.json",
            auth=_basic_auth(f"{profile['email']}/token", profile["api_token"]),
            json={"ticket": ticket},
        )

    zendesk_create_ticket.__name__ = "zendesk_create_ticket"
    tools.append(
        _attach(
            zendesk_create_ticket,
            _schema(
                "zendesk_create_ticket",
                "Create a Zendesk ticket. Requires user approval.",
                {
                    "subject": {"type": "string"},
                    "body": {"type": "string"},
                    "requester_email": {"type": "string"},
                },
                ["subject", "body"],
            ),
            approval=True,
            caps=["zendesk", "write"],
        )
    )

    def linear_search_issues(query: str, max_results: int = 10) -> dict[str, Any]:
        profile, err = _profile(secrets, "linear", "api_key")
        if err:
            return err
        gql = (
            "query($term: String!, $first: Int!) {"
            " searchIssues(term: $term, first: $first) {"
            " nodes { identifier title url state { name } assignee { name } } } }"
        )
        return _linear_gql(
            profile["api_key"], gql, {"term": query, "first": _clamp(max_results)}
        )

    linear_search_issues.__name__ = "linear_search_issues"
    tools.append(
        _attach(
            linear_search_issues,
            _schema(
                "linear_search_issues",
                "Search Linear issues by text.",
                {"query": {"type": "string"}, "max_results": {"type": "integer"}},
                ["query"],
            ),
            caps=["linear", "read"],
        )
    )

    def linear_get_issue(issue_id: str) -> dict[str, Any]:
        profile, err = _profile(secrets, "linear", "api_key")
        if err:
            return err
        gql = (
            "query($id: String!) { issue(id: $id) {"
            " identifier title description url state { name } assignee { name }"
            " comments { nodes { body user { name } } } } }"
        )
        return _linear_gql(profile["api_key"], gql, {"id": issue_id})

    linear_get_issue.__name__ = "linear_get_issue"
    tools.append(
        _attach(
            linear_get_issue,
            _schema(
                "linear_get_issue",
                "Read a Linear issue (with comments) by ID or key like ENG-123.",
                {"issue_id": {"type": "string"}},
                ["issue_id"],
            ),
            caps=["linear", "read"],
        )
    )

    def linear_list_teams() -> dict[str, Any]:
        profile, err = _profile(secrets, "linear", "api_key")
        if err:
            return err
        return _linear_gql(
            profile["api_key"], "{ teams { nodes { id key name } } }", {}
        )

    linear_list_teams.__name__ = "linear_list_teams"
    tools.append(
        _attach(
            linear_list_teams,
            _schema(
                "linear_list_teams",
                "List Linear teams (IDs are needed to create issues).",
                {},
                [],
            ),
            caps=["linear", "read"],
        )
    )

    def linear_create_issue(
        team_id: str, title: str, description: str = ""
    ) -> dict[str, Any]:
        profile, err = _profile(secrets, "linear", "api_key")
        if err:
            return err
        gql = (
            "mutation($input: IssueCreateInput!) { issueCreate(input: $input) {"
            " success issue { identifier url } } }"
        )
        return _linear_gql(
            profile["api_key"],
            gql,
            {"input": {"teamId": team_id, "title": title, "description": description}},
        )

    linear_create_issue.__name__ = "linear_create_issue"
    tools.append(
        _attach(
            linear_create_issue,
            _schema(
                "linear_create_issue",
                "Create a Linear issue. Get team_id from linear_list_teams. Requires user approval.",
                {
                    "team_id": {"type": "string"},
                    "title": {"type": "string"},
                    "description": {"type": "string"},
                },
                ["team_id", "title"],
            ),
            approval=True,
            caps=["linear", "write"],
        )
    )

    add_gitlab_tools(tools, secrets)
    def discord_list_channels(guild_id: str) -> dict[str, Any]:
        profile, err = _profile(secrets, "discord", "bot_token")
        if err:
            return err
        return _request(
            "GET",
            f"https://discord.com/api/v10/guilds/{guild_id}/channels",
            headers={"Authorization": f"Bot {profile['bot_token']}"},
        )

    discord_list_channels.__name__ = "discord_list_channels"
    tools.append(
        _attach(
            discord_list_channels,
            _schema(
                "discord_list_channels",
                "List channels in a Discord server (guild).",
                {"guild_id": {"type": "string"}},
                ["guild_id"],
            ),
            caps=["discord", "read"],
        )
    )

    def discord_read_messages(channel_id: str, max_results: int = 10) -> dict[str, Any]:
        profile, err = _profile(secrets, "discord", "bot_token")
        if err:
            return err
        return _request(
            "GET",
            f"https://discord.com/api/v10/channels/{channel_id}/messages",
            headers={"Authorization": f"Bot {profile['bot_token']}"},
            params={"limit": _clamp(max_results, ceiling=50)},
        )

    discord_read_messages.__name__ = "discord_read_messages"
    tools.append(
        _attach(
            discord_read_messages,
            _schema(
                "discord_read_messages",
                "Read recent messages from a Discord channel.",
                {"channel_id": {"type": "string"}, "max_results": {"type": "integer"}},
                ["channel_id"],
            ),
            caps=["discord", "read"],
        )
    )

    def discord_send_message(channel_id: str, content: str) -> dict[str, Any]:
        profile, err = _profile(secrets, "discord", "bot_token")
        if err:
            return err
        return _request(
            "POST",
            f"https://discord.com/api/v10/channels/{channel_id}/messages",
            headers={"Authorization": f"Bot {profile['bot_token']}"},
            json={"content": content[:2000]},
        )

    discord_send_message.__name__ = "discord_send_message"
    tools.append(
        _attach(
            discord_send_message,
            _schema(
                "discord_send_message",
                "Send a message to a Discord channel. Requires user approval.",
                {"channel_id": {"type": "string"}, "content": {"type": "string"}},
                ["channel_id", "content"],
            ),
            approval=True,
            caps=["discord", "write"],
        )
    )

    def stripe_search_customers(query: str, max_results: int = 10) -> dict[str, Any]:
        profile, err = _profile(secrets, "stripe", "api_key")
        if err:
            return err
        return _request(
            "GET",
            "https://api.stripe.com/v1/customers/search",
            headers=_bearer_headers(profile["api_key"]),
            params={"query": query, "limit": _clamp(max_results)},
        )

    stripe_search_customers.__name__ = "stripe_search_customers"
    tools.append(
        _attach(
            stripe_search_customers,
            _schema(
                "stripe_search_customers",
                "Search Stripe customers. Query uses Stripe search syntax, e.g. email:'jane@example.com' or name~'Jane'.",
                {"query": {"type": "string"}, "max_results": {"type": "integer"}},
                ["query"],
            ),
            caps=["stripe", "read"],
        )
    )

    def stripe_list_charges(
        customer_id: str = "", max_results: int = 10
    ) -> dict[str, Any]:
        profile, err = _profile(secrets, "stripe", "api_key")
        if err:
            return err
        params: dict[str, Any] = {"limit": _clamp(max_results)}
        if customer_id:
            params["customer"] = customer_id
        return _request(
            "GET",
            "https://api.stripe.com/v1/charges",
            headers=_bearer_headers(profile["api_key"]),
            params=params,
        )

    stripe_list_charges.__name__ = "stripe_list_charges"
    tools.append(
        _attach(
            stripe_list_charges,
            _schema(
                "stripe_list_charges",
                "List Stripe charges, optionally for one customer.",
                {"customer_id": {"type": "string"}, "max_results": {"type": "integer"}},
                [],
            ),
            caps=["stripe", "read"],
        )
    )

    def stripe_list_invoices(
        customer_id: str = "", max_results: int = 10
    ) -> dict[str, Any]:
        profile, err = _profile(secrets, "stripe", "api_key")
        if err:
            return err
        params: dict[str, Any] = {"limit": _clamp(max_results)}
        if customer_id:
            params["customer"] = customer_id
        return _request(
            "GET",
            "https://api.stripe.com/v1/invoices",
            headers=_bearer_headers(profile["api_key"]),
            params=params,
        )

    stripe_list_invoices.__name__ = "stripe_list_invoices"
    tools.append(
        _attach(
            stripe_list_invoices,
            _schema(
                "stripe_list_invoices",
                "List Stripe invoices, optionally for one customer.",
                {"customer_id": {"type": "string"}, "max_results": {"type": "integer"}},
                [],
            ),
            caps=["stripe", "read"],
        )
    )

    def asana_list_workspaces() -> dict[str, Any]:
        profile, err = _profile(secrets, "asana", "token")
        if err:
            return err
        return _request(
            "GET",
            "https://app.asana.com/api/1.0/workspaces",
            headers=_bearer_headers(profile["token"]),
        )

    asana_list_workspaces.__name__ = "asana_list_workspaces"
    tools.append(
        _attach(
            asana_list_workspaces,
            _schema(
                "asana_list_workspaces",
                "List Asana workspaces (GIDs are needed to search tasks).",
                {},
                [],
            ),
            caps=["asana", "read"],
        )
    )

    def asana_search_tasks(
        workspace_gid: str, query: str, max_results: int = 10
    ) -> dict[str, Any]:
        profile, err = _profile(secrets, "asana", "token")
        if err:
            return err
        return _request(
            "GET",
            f"https://app.asana.com/api/1.0/workspaces/{workspace_gid}/typeahead",
            headers=_bearer_headers(profile["token"]),
            params={
                "resource_type": "task",
                "query": query,
                "count": _clamp(max_results),
            },
        )

    asana_search_tasks.__name__ = "asana_search_tasks"
    tools.append(
        _attach(
            asana_search_tasks,
            _schema(
                "asana_search_tasks",
                "Search Asana tasks by name in a workspace. Get workspace_gid from asana_list_workspaces.",
                {
                    "workspace_gid": {"type": "string"},
                    "query": {"type": "string"},
                    "max_results": {"type": "integer"},
                },
                ["workspace_gid", "query"],
            ),
            caps=["asana", "read"],
        )
    )

    def asana_get_task(task_gid: str) -> dict[str, Any]:
        profile, err = _profile(secrets, "asana", "token")
        if err:
            return err
        return _request(
            "GET",
            f"https://app.asana.com/api/1.0/tasks/{task_gid}",
            headers=_bearer_headers(profile["token"]),
        )

    asana_get_task.__name__ = "asana_get_task"
    tools.append(
        _attach(
            asana_get_task,
            _schema(
                "asana_get_task",
                "Read an Asana task.",
                {"task_gid": {"type": "string"}},
                ["task_gid"],
            ),
            caps=["asana", "read"],
        )
    )

    def asana_create_task(
        project_gid: str, name: str, notes: str = ""
    ) -> dict[str, Any]:
        profile, err = _profile(secrets, "asana", "token")
        if err:
            return err
        return _request(
            "POST",
            "https://app.asana.com/api/1.0/tasks",
            headers=_bearer_headers(profile["token"]),
            json={"data": {"name": name, "notes": notes, "projects": [project_gid]}},
        )

    asana_create_task.__name__ = "asana_create_task"
    tools.append(
        _attach(
            asana_create_task,
            _schema(
                "asana_create_task",
                "Create an Asana task in a project. Requires user approval.",
                {
                    "project_gid": {"type": "string"},
                    "name": {"type": "string"},
                    "notes": {"type": "string"},
                },
                ["project_gid", "name"],
            ),
            approval=True,
            caps=["asana", "write"],
        )
    )

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

    def quickbooks_query(query: str, max_results: int = 10) -> dict[str, Any]:
        profile, err = _profile(secrets, "quickbooks", "access_token", "realm_id")
        if err:
            return err
        q = query.strip()
        if "maxresults" not in q.lower():
            q = f"{q} MAXRESULTS {_clamp(max_results, ceiling=100)}"
        return _request(
            "GET",
            f"{_qbo_base(profile)}/query",
            headers=_bearer_headers(profile["access_token"]),
            params={"query": q},
        )

    quickbooks_query.__name__ = "quickbooks_query"
    tools.append(
        _attach(
            quickbooks_query,
            _schema(
                "quickbooks_query",
                "Run a QuickBooks Online query, e.g. \"SELECT * FROM Invoice WHERE TotalAmt > '100'\". "
                "Entities include Customer, Invoice, Bill, Payment, Account, Vendor.",
                {"query": {"type": "string"}, "max_results": {"type": "integer"}},
                ["query"],
            ),
            caps=["quickbooks", "read"],
        )
    )

    def quickbooks_list_customers(max_results: int = 10) -> dict[str, Any]:
        profile, err = _profile(secrets, "quickbooks", "access_token", "realm_id")
        if err:
            return err
        return _request(
            "GET",
            f"{_qbo_base(profile)}/query",
            headers=_bearer_headers(profile["access_token"]),
            params={
                "query": f"SELECT * FROM Customer MAXRESULTS {_clamp(max_results)}"
            },
        )

    quickbooks_list_customers.__name__ = "quickbooks_list_customers"
    tools.append(
        _attach(
            quickbooks_list_customers,
            _schema(
                "quickbooks_list_customers",
                "List QuickBooks customers.",
                {"max_results": {"type": "integer"}},
                [],
            ),
            caps=["quickbooks", "read"],
        )
    )

    def quickbooks_list_invoices(max_results: int = 10) -> dict[str, Any]:
        profile, err = _profile(secrets, "quickbooks", "access_token", "realm_id")
        if err:
            return err
        return _request(
            "GET",
            f"{_qbo_base(profile)}/query",
            headers=_bearer_headers(profile["access_token"]),
            params={
                "query": "SELECT * FROM Invoice ORDERBY TxnDate DESC "
                f"MAXRESULTS {_clamp(max_results)}"
            },
        )

    quickbooks_list_invoices.__name__ = "quickbooks_list_invoices"
    tools.append(
        _attach(
            quickbooks_list_invoices,
            _schema(
                "quickbooks_list_invoices",
                "List recent QuickBooks invoices.",
                {"max_results": {"type": "integer"}},
                [],
            ),
            caps=["quickbooks", "read"],
        )
    )

    def quickbooks_get_report(
        report: str, start_date: str = "", end_date: str = ""
    ) -> dict[str, Any]:
        profile, err = _profile(secrets, "quickbooks", "access_token", "realm_id")
        if err:
            return err
        params: dict[str, Any] = {}
        if start_date:
            params["start_date"] = start_date
        if end_date:
            params["end_date"] = end_date
        return _request(
            "GET",
            f"{_qbo_base(profile)}/reports/{quote(report, safe='')}",
            headers=_bearer_headers(profile["access_token"]),
            params=params or None,
        )

    quickbooks_get_report.__name__ = "quickbooks_get_report"
    tools.append(
        _attach(
            quickbooks_get_report,
            _schema(
                "quickbooks_get_report",
                "Run a QuickBooks report such as ProfitAndLoss, BalanceSheet, CashFlow, "
                "AgedReceivables. Dates are YYYY-MM-DD.",
                {
                    "report": {"type": "string"},
                    "start_date": {"type": "string"},
                    "end_date": {"type": "string"},
                },
                ["report"],
            ),
            caps=["quickbooks", "read"],
        )
    )

    def whatsapp_send_message(to: str, text: str) -> dict[str, Any]:
        profile, err = _profile(secrets, "whatsapp", "access_token", "phone_number_id")
        if err:
            return err
        return _request(
            "POST",
            f"https://graph.facebook.com/v21.0/{profile['phone_number_id']}/messages",
            headers=_bearer_headers(profile["access_token"]),
            json={
                "messaging_product": "whatsapp",
                "to": to,
                "type": "text",
                "text": {"body": text[:4096]},
            },
        )

    whatsapp_send_message.__name__ = "whatsapp_send_message"
    tools.append(
        _attach(
            whatsapp_send_message,
            _schema(
                "whatsapp_send_message",
                "Send a WhatsApp text message. Only delivered if the recipient messaged "
                "this number within the last 24 hours; otherwise use "
                "whatsapp_send_template. Requires user approval.",
                {"to": {"type": "string"}, "text": {"type": "string"}},
                ["to", "text"],
            ),
            approval=True,
            caps=["whatsapp", "write"],
        )
    )

    def whatsapp_send_template(
        to: str, template_name: str, language_code: str = "en_US"
    ) -> dict[str, Any]:
        profile, err = _profile(secrets, "whatsapp", "access_token", "phone_number_id")
        if err:
            return err
        return _request(
            "POST",
            f"https://graph.facebook.com/v21.0/{profile['phone_number_id']}/messages",
            headers=_bearer_headers(profile["access_token"]),
            json={
                "messaging_product": "whatsapp",
                "to": to,
                "type": "template",
                "template": {
                    "name": template_name,
                    "language": {"code": language_code},
                },
            },
        )

    whatsapp_send_template.__name__ = "whatsapp_send_template"
    tools.append(
        _attach(
            whatsapp_send_template,
            _schema(
                "whatsapp_send_template",
                "Send a pre-approved WhatsApp template message (works outside the "
                "24-hour service window). Requires user approval.",
                {
                    "to": {"type": "string"},
                    "template_name": {"type": "string"},
                    "language_code": {"type": "string"},
                },
                ["to", "template_name"],
            ),
            approval=True,
            caps=["whatsapp", "write"],
        )
    )

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
        result = _request(
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
        page = _request(
            "GET",
            f"https://api.notion.com/v1/pages/{page_id}",
            headers=_notion_headers(profile),
        )
        if "error" in page:
            return _acct_result(aid, page)
        blocks = _request(
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
        result = _request(
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
        result = _request(
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
        result = _request(
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
        result = _request(
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
        result = _request(
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
        result = _request(
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
        result = _request(
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
        result = _request(
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
        result = _request(
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
        result = _request(
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
        result = _request(
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
        result = _request(
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
        result = _request(
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
        result = _request(
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
        result = _request(
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
        return _request(
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



    add_clickup_tools(tools, secrets)
    add_close_tools(tools, secrets)
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
        result = _request(
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
        return _request(
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
        return _request(
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
        return _request(
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
            _request(
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
            _request(
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
        meta = _request(
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
            body = _request(
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
            body = _request(
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
            info = _request(
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
        return _request(
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
        return _request(
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
        return _request(
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
        return _request(
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
        return _request(
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
        return _request(
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
        return _request(
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
        return _request(
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

    if enabled_connectors is not None:
        tools = [
            t for t in tools if connector_for_tool(t.__name__) in enabled_connectors
        ]
    if enabled_tools is not None:
        tools = [t for t in tools if t.__name__ in enabled_tools]
    return tools
