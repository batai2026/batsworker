"""Shared primitives for the connector tool surface.

`_schema` / `_attach` are how a connector tool becomes registry-visible: `_attach` stamps
`__coworker_schema__` and the aisuite metadata (name, category, risk, capabilities,
approval) that the tool registry and the permission layer read. `_profile` /
`_account_profile` / `_acct_result` resolve per-account credentials out of the SecretStore,
and `_request` / `_google_headers` / `_graph_headers` are the HTTP helpers every platform
shares. `_GEN_ACCOUNT_PROP` is the "account" schema fragment 31 registrations point at.

They live here rather than in `integration_tools.py` so per-platform tool modules can import
them without importing (and cycling through) the factory that assembles the catalog.
"""

from __future__ import annotations

from typing import Any, Callable, Optional

import aisuite as ai

from ..secrets import SecretStore
from ..web.guard import get_checked
from .tool_defs import approval_for_tool


def _meta(
    name: str, *, approval: bool = False, capabilities: Optional[list[str]] = None
):
    return ai.ToolMetadata(
        name=name,
        category="connector",
        risk_level="medium" if approval else "low",
        capabilities=capabilities or ["integration"],
        requires_approval=approval,
    )


def _schema(
    name: str, description: str, properties: dict[str, Any], required: list[str]
) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        },
    }


def _attach(
    fn: Callable[..., Any],
    schema: dict[str, Any],
    *,
    approval: bool = True,
    caps: Optional[list[str]] = None,
):
    name = schema["function"]["name"]
    # §36: the tool registry's read/write kind overrides the call-site flag for
    # registered tools — connector READS never gate. The explicit arg only governs
    # tools without a registry entry.
    approval = approval_for_tool(name, default=approval)
    fn.__coworker_schema__ = schema
    fn.__aisuite_tool_metadata__ = _meta(name, approval=approval, capabilities=caps)
    fn.__doc__ = schema["function"]["description"]
    return fn


def _profile(
    secrets: SecretStore, name: str, *keys: str
) -> tuple[Optional[dict[str, Any]], Optional[dict[str, str]]]:
    profile = secrets.get(f"{name}:default") or {}
    if profile.get("managed"):
        # Managed-OAuth profiles renew through the cloud broker just before
        # expiry; manual token profiles are never touched (no-op inside).
        from ..cloud import ensure_fresh_connector_token
        from ..config import load_config

        ensure_fresh_connector_token(secrets, load_config(), name)
        profile = secrets.get(f"{name}:default") or {}
    missing = [k for k in keys if not profile.get(k)]
    if missing:
        return None, {"error": f"{name} is not connected; missing {', '.join(missing)}"}
    return profile, None


def _account_profile(
    secrets: SecretStore, connector: str, account: str = "", *keys: str
) -> tuple[str, Optional[dict[str, Any]], Optional[dict[str, str]]]:
    """(account_id, profile, err) for an account-patterned connector (generic
    accounts.py layer): requested — or default — account, managed tokens
    refreshed in place. The gmail/gcal/hubspot bespoke helpers predate this."""
    from . import accounts as _accounts

    account_id, key, profile = _accounts.resolve(secrets, connector, account)
    if profile is None:
        hint = (
            f"no {connector} account matching {account!r}"
            if account
            else f"{connector} is not connected"
        )
        return "", None, {"error": hint}
    if profile.get("managed"):
        from ..cloud import ensure_fresh_connector_token
        from ..config import load_config

        ensure_fresh_connector_token(secrets, load_config(), connector, profile_key=key)
        profile = secrets.get(key) or profile
    missing = [k for k in keys if not profile.get(k)]
    if missing:
        return (
            account_id,
            None,
            {"error": f"{connector} is not connected; missing {', '.join(missing)}"},
        )
    return account_id, profile, None


def _acct_result(account_id: str, result: dict[str, Any]) -> dict[str, Any]:
    """Stamp which account served a tool call — approvals and transcripts must
    name the account once more than one is connected."""
    if isinstance(result, dict) and account_id:
        return {"account": account_id, **result}
    return result


def _google_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Accept": "application/json"}


def _graph_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def _request(
    method: str,
    url: str,
    *,
    headers=None,
    params=None,
    json=None,
    auth=None,
    check_addresses: bool = False,
) -> dict[str, Any]:
    """HTTP for the connectors.

    `check_addresses` is for URLs the *model* supplies. It turns off
    automatic redirects and walks the chain through the address guard instead, so a public
    URL cannot 302 into loopback or the metadata endpoint. The vendor endpoints everything
    else in this module calls are hardcoded, so they skip the guard and its DNS lookup.
    """
    try:
        import httpx

        with httpx.Client(
            timeout=30.0, follow_redirects=not check_addresses
        ) as client:
            if check_addresses:
                if method.upper() != "GET":
                    return {"error": "address-checked requests must be GET"}
                try:
                    resp = get_checked(client, url)
                except PermissionError as exc:
                    return {"error": str(exc)}
            else:
                resp = client.request(
                    method, url, headers=headers, params=params, json=json, auth=auth
                )
            ctype = resp.headers.get("content-type", "")
            data: Any = resp.json() if "json" in ctype.lower() else resp.text
            if resp.status_code >= 400:
                return {"error": f"HTTP {resp.status_code}", "details": data}
            return {"ok": True, "data": data}
    except Exception as exc:
        return {"error": str(exc)}



_GEN_ACCOUNT_PROP = {
    "type": "string",
    "description": "Which connected account to use (default account when empty)",
}


def _clamp(n: Any, default: int = 10, ceiling: int = 20) -> int:
    return max(1, min(int(n or default), ceiling))


def _bearer_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Accept": "application/json"}
