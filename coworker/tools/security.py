"""Security workflow tools — threat model + finding tracker for security coworkers.

These tools give the Security Coworker (and any persona that opts into the
``security`` capability) the Claude-Security-style loop:

- ``threat_model_read`` / ``threat_model_update`` — read/edit the team's threat
  model before triaging; the model decides what is worth flagging.
- ``security_finding_log`` / ``security_finding_list`` / ``security_finding_update``
  — record findings, link their fix PRs, and carry them open → fixing → fixed →
  closed, so remediation is tracked to closure, not just reported.
"""

from __future__ import annotations

from typing import Any, Callable

import aisuite as ai

from ..security import SecurityStore

_SEV_HELP = "critical|high|medium|low|noise — default medium."
_STATUS_HELP = "open|fixing|fixed|closed"


def _meta(name: str, *, approval: bool = False, category: str = "security"):
    return ai.ToolMetadata(
        name=name,
        category=category,
        risk_level="medium" if approval else "low",
        capabilities=["security"],
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


def security_tools(store: SecurityStore) -> list[Callable[..., Any]]:
    """Build the security workflow tools bound to the given store."""

    def threat_model_read() -> dict[str, Any]:
        """Read the team's editable threat model. Call this BEFORE triaging any
        security findings: it defines what this codebase treats as worth flagging
        versus noise, plus reachability and mitigation preferences."""
        return {
            "ok": True,
            "content": store.read_threat_model(),
            "path": str(store.threat_model_path),
        }

    def threat_model_update(content: str) -> dict[str, Any]:
        """Replace the team's threat model with the given markdown. Use after the
        user asks to add/remove a rule; the user reviews this before it saves."""
        store.write_threat_model(content or "")
        return {"ok": True, "content": store.read_threat_model()}

    def security_finding_log(
        title: str,
        severity: str = "medium",
        file: str = "",
        description: str = "",
        pr_url: str = "",
    ) -> dict[str, Any]:
        """Record a triaged security finding so it is tracked to closure. Call this
        after triage for every real (non-noise) finding. `severity` is
        critical|high|medium|low; `file` is the affected path; `pr_url` can be
        added now or linked later via security_finding_update."""
        try:
            finding = store.add_finding(
                title=title,
                severity=severity,
                file=file,
                description=description,
                pr_url=pr_url,
            )
        except ValueError as exc:
            return {"error": str(exc)}
        return {"ok": True, "finding": finding}

    def security_finding_list(status: str = "") -> dict[str, Any]:
        """List tracked security findings, newest first. `status` optionally
        filters to open|fixing|fixed|closed."""
        return {"ok": True, "findings": store.list_findings(status)}

    def security_finding_update(
        finding_id: str, status: str = "", pr_url: str = ""
    ) -> dict[str, Any]:
        """Update a tracked finding's status (open|fixing|fixed|closed) and/or its
        fix PR url. Use 'fixed' when the fix PR merged, then 'closed' to finish."""
        if not status and not pr_url:
            return {"error": "provide a status and/or pr_url"}
        finding = store.update_finding(
            finding_id, status=status or None, pr_url=pr_url if pr_url else None
        )
        if finding is None:
            return {"error": f"unknown finding: {finding_id}"}
        return {"ok": True, "finding": finding}

    threat_model_read.__name__ = "threat_model_read"
    threat_model_read.__coworker_schema__ = _schema(
        "threat_model_read",
        "Read the team's editable threat model (what to flag vs noise, reachability rules).",
        {},
        [],
    )
    threat_model_read.__aisuite_tool_metadata__ = _meta("threat_model_read")

    threat_model_update.__name__ = "threat_model_update"
    threat_model_update.__coworker_schema__ = _schema(
        "threat_model_update",
        "Replace the team's threat model markdown. The user reviews this before it saves.",
        {"content": {"type": "string", "description": "The full threat model markdown."}},
        ["content"],
    )
    threat_model_update.__aisuite_tool_metadata__ = _meta(
        "threat_model_update", approval=True
    )

    security_finding_log.__name__ = "security_finding_log"
    security_finding_log.__coworker_schema__ = _schema(
        "security_finding_log",
        "Record a triaged security finding so it is tracked to closure.",
        {
            "title": {"type": "string", "description": "Short finding title."},
            "severity": {"type": "string", "description": _SEV_HELP},
            "file": {"type": "string", "description": "Affected file path (optional)."},
            "description": {"type": "string", "description": "Reachability/impact, one or two sentences."},
            "pr_url": {"type": "string", "description": "Fix PR url (optional; link later via update)."},
        },
        ["title"],
    )
    security_finding_log.__aisuite_tool_metadata__ = _meta("security_finding_log")

    security_finding_list.__name__ = "security_finding_list"
    security_finding_list.__coworker_schema__ = _schema(
        "security_finding_list",
        "List tracked security findings, newest first (optionally filtered by status).",
        {"status": {"type": "string", "description": _STATUS_HELP}},
        [],
    )
    security_finding_list.__aisuite_tool_metadata__ = _meta("security_finding_list")

    security_finding_update.__name__ = "security_finding_update"
    security_finding_update.__coworker_schema__ = _schema(
        "security_finding_update",
        "Update a finding's status (open|fixing|fixed|closed) and/or its fix PR url.",
        {
            "finding_id": {"type": "string", "description": "The finding id, e.g. F-abc123."},
            "status": {"type": "string", "description": _STATUS_HELP},
            "pr_url": {"type": "string", "description": "Fix PR url."},
        },
        ["finding_id"],
    )
    security_finding_update.__aisuite_tool_metadata__ = _meta("security_finding_update")

    return [
        ai.tool(threat_model_read, metadata=threat_model_read.__aisuite_tool_metadata__),
        ai.tool(threat_model_update, metadata=threat_model_update.__aisuite_tool_metadata__),
        ai.tool(security_finding_log, metadata=security_finding_log.__aisuite_tool_metadata__),
        ai.tool(security_finding_list, metadata=security_finding_list.__aisuite_tool_metadata__),
        ai.tool(security_finding_update, metadata=security_finding_update.__aisuite_tool_metadata__),
    ]