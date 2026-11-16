"""Stripe, Asana, Dropbox, Box, QuickBooks and WhatsApp tools (D2 of the split).

Two registration functions, because the HubSpot call sits between the two halves in the
factory; keeping it there is what preserves the catalog order.

Two families live here. Commerce and files: Stripe (read-only customers/charges/invoices, no
write scope), Asana tasks, Dropbox / Box list + read with a character cap so a huge file cannot
flood a turn. Books and messaging: QuickBooks (`_qbo_base` picks the sandbox host from the
stored realm profile) and WhatsApp (text + pre-approved templates only).
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from ..secrets import SecretStore
from . import toolkit
from .toolkit import _attach, _bearer_headers, _clamp, _profile, _schema


def _qbo_base(profile: dict[str, Any]) -> str:
    env = str(profile.get("environment", "")).lower()
    host = (
        "sandbox-quickbooks.api.intuit.com"
        if env.startswith("sand")
        else "quickbooks.api.intuit.com"
    )
    return f"https://{host}/v3/company/{profile['realm_id']}"


def add_commerce_tools(tools: list, secrets: SecretStore) -> None:
    """Register the Stripe / Asana / Dropbox / Box tools on `tools`."""
    def stripe_search_customers(query: str, max_results: int = 10) -> dict[str, Any]:
        profile, err = _profile(secrets, "stripe", "api_key")
        if err:
            return err
        return toolkit._request(
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
        return toolkit._request(
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
        return toolkit._request(
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
        return toolkit._request(
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
        return toolkit._request(
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
        return toolkit._request(
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
        return toolkit._request(
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


def add_finance_chat_tools(tools: list, secrets: SecretStore) -> None:
    """Register the QuickBooks / WhatsApp tools on `tools`."""
    def quickbooks_query(query: str, max_results: int = 10) -> dict[str, Any]:
        profile, err = _profile(secrets, "quickbooks", "access_token", "realm_id")
        if err:
            return err
        q = query.strip()
        if "maxresults" not in q.lower():
            q = f"{q} MAXRESULTS {_clamp(max_results, ceiling=100)}"
        return toolkit._request(
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
        return toolkit._request(
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
        return toolkit._request(
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
        return toolkit._request(
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
        return toolkit._request(
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
        return toolkit._request(
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
