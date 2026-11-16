"""Jira, Confluence, Zendesk, Linear and Discord tools.

D1 of the connector tool split: the work-tracking side (issues, pages, tickets) plus the one
chat platform. Moved out of `integration_tools.py` verbatim — same names, schemas, gating and
registration order; `tests/test_integration_tool_catalog.py` freezes that catalog.

Atlassian shares `_atlassian_base` + `_basic_auth` (cloud uses email + API token as HTTP
basic); Linear talks GraphQL through `_linear_gql`; Discord is a bot token plus REST.
"""

from __future__ import annotations

from typing import Any

from ..secrets import SecretStore
from . import toolkit
from .toolkit import _attach, _clamp, _profile, _schema


def _atlassian_base(profile: dict[str, Any]) -> str:
    return str(profile.get("base_url", "")).rstrip("/")

def _basic_auth(email: str, token: str) -> tuple[str, str]:
    return (email, token)

def _linear_gql(api_key: str, query: str, variables: dict[str, Any]) -> dict[str, Any]:
    return toolkit._request(
        "POST",
        "https://api.linear.app/graphql",
        headers={"Authorization": api_key, "Content-Type": "application/json"},
        json={"query": query, "variables": variables},
    )


def add_tracking_tools(tools: list, secrets: SecretStore) -> None:
    """Register the work-tracking tools on `tools` (order preserved)."""
    def jira_search_issues(jql: str, max_results: int = 10) -> dict[str, Any]:
        profile, err = _profile(secrets, "jira", "base_url", "email", "api_token")
        if err:
            return err
        return toolkit._request(
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
        return toolkit._request(
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
        return toolkit._request(
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
        return toolkit._request(
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
        return toolkit._request(
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
        return toolkit._request(
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
        return toolkit._request(
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
        return toolkit._request(
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
        return toolkit._request(
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


def add_discord_tools(tools: list, secrets: SecretStore) -> None:
    """Register the Discord tools on `tools` (order preserved)."""
    def discord_list_channels(guild_id: str) -> dict[str, Any]:
        profile, err = _profile(secrets, "discord", "bot_token")
        if err:
            return err
        return toolkit._request(
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
        return toolkit._request(
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
        return toolkit._request(
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
