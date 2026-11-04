"""Egress policy tool — ``request_egress``.

When a sandboxed/network-restricted agent hits a blocked site, it calls this tool
to request that the user add the domain to the egress policy. The tool is
approval-gated (``requires_approval=True``): the engine raises a check-in card, and
the user's approval IS the "allow it" step — after approval the domain is persisted
in the policy store and later fetches to it auto-run.
"""

from __future__ import annotations

from typing import Any, Callable

import aisuite as ai

from ..egress import EgressPolicy

_SCHEMA = {
    "type": "function",
    "function": {
        "name": "request_egress",
        "description": (
            "Request that a blocked network destination be added to the egress policy. "
            "Call this when a network action (web fetch, download, API call) is blocked "
            "because its domain is not in the allowed list. The user must approve; once "
            "approved, the domain is added to the policy and future requests to it run "
            "without asking again."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "domain": {
                    "type": "string",
                    "description": "The blocked hostname, e.g. 'api.example.com'.",
                },
                "reason": {
                    "type": "string",
                    "description": "Why this destination is needed (one or two sentences).",
                },
            },
            "required": ["domain", "reason"],
        },
    },
}


def request_egress_tool(policy: EgressPolicy) -> list[Callable[..., Any]]:
    """Build the ``request_egress`` tool bound to the given egress policy store."""

    def request_egress(domain: str, reason: str = "") -> dict[str, Any]:
        d = (domain or "").strip()
        if not d:
            return {"ok": False, "error": "domain required"}
        if policy.is_allowed(d):
            return {
                "ok": True,
                "allowed": True,
                "domain": d,
                "note": "already in the egress policy — no change needed",
            }
        policy.add_domain(d, reason)
        return {
            "ok": True,
            "allowed": True,
            "domain": d,
            "reason": (reason or "").strip(),
            "note": "added to the egress policy — future requests to this domain run without asking",
        }

    request_egress.__name__ = "request_egress"
    request_egress.__doc__ = _SCHEMA["function"]["description"]
    request_egress.__coworker_schema__ = _SCHEMA
    request_egress.__aisuite_tool_metadata__ = ai.ToolMetadata(
        name="request_egress",
        category="policy",
        risk_level="high",
        capabilities=["egress"],
        requires_approval=True,
    )
    return [ai.tool(request_egress, metadata=request_egress.__aisuite_tool_metadata__)]