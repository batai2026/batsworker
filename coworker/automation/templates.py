"""Automation template marketplace — curated, backend-served templates.

Templates are plain data (no code) so the catalog can later be extended with
cloud-sourced entries without changing the instantiation path. Each template
carries the metadata the GUI needs to render a marketplace card (title, blurb,
cadence, category, required connectors) plus the fields the quickstart
configure pane uses (repo/channel/deliver/day/time) and a ``instructions``
string template with ``{repo}`` / ``{channel}`` / ``{deliver}`` placeholders.

``instantiate`` renders the instructions, assembles a cron from the day/time,
and routes through the same validation as the GUI's manual "New automation"
form (``SessionManager.create_automation``), so template-created automations
are indistinguishable from hand-written ones.
"""

from __future__ import annotations

from typing import Any, Optional

# day key -> cron day-of-week (matches the GUI quickstart's DAYS map).
_DAY_DOW: dict[str, str] = {
    "mon": "1",
    "tue": "2",
    "wed": "3",
    "thu": "4",
    "fri": "5",
    "sat": "6",
    "sun": "0",
    "weekdays": "1-5",
    "daily": "*",
}

# The built-in catalog. `conns` are connector ids with a one-line "why";
# `consent` marks write recipes whose instantiation should carry a standing
# send_message grant to the chosen channel.
AUTOMATION_TEMPLATES: list[dict[str, Any]] = [
    {
        "key": "github",
        "title": "GitHub digest",
        "blurb": "Merged PRs and commits, posted to your team's Slack.",
        "cadence": "Weekly",
        "category": "Engineering",
        "conns": [
            {"name": "slack", "why": "Where the digest posts"},
            {"name": "github", "why": "What the digest summarizes"},
        ],
        "needs_repo": True,
        "needs_channel": True,
        "consent": True,
        "deliver": False,
        "day": "mon",
        "time": "09:00",
        "instructions": (
            "Summarize activity since the last digest in the GitHub repository "
            "{repo}: merged pull requests, notable commits, and anything needing "
            "attention. Post the digest to the Slack channel {channel} using "
            "send_message."
        ),
    },
    {
        "key": "pipeline",
        "title": "Pipeline digest",
        "blurb": "Deals that moved — and deals going quiet — posted to Slack.",
        "cadence": "Weekly",
        "category": "Sales",
        "conns": [
            {"name": "slack", "why": "Where the digest posts"},
            {"name": "hubspot", "why": "Pipeline and deal activity"},
        ],
        "needs_channel": True,
        "consent": True,
        "deliver": False,
        "day": "mon",
        "time": "09:00",
        "instructions": (
            "Review HubSpot activity since the last digest: deals that changed "
            "stage, deals going quiet, and deals past their close date. Post a "
            "short pipeline digest to the Slack channel {channel} using send_message."
        ),
    },
    {
        "key": "brief",
        "title": "Morning brief",
        "blurb": "Calendar and unread email, summarized before your day starts.",
        "cadence": "Daily",
        "category": "Personal",
        "conns": [
            {"name": "google_calendar", "why": "Today's meetings and gaps"},
            {"name": "gmail", "why": "What arrived overnight"},
        ],
        "needs_channel": False,
        "consent": False,
        "deliver": True,
        "day": "daily",
        "time": "08:00",
        "instructions": (
            "Prepare a short morning brief: today's calendar events and gaps, "
            "plus email that arrived since yesterday evening. "
            "Deliver it to {deliver}."
        ),
    },
    {
        "key": "news",
        "title": "Morning news briefing",
        "blurb": "A 5-bullet tech & world news digest, saved as markdown.",
        "cadence": "Daily",
        "category": "Personal",
        "conns": [],
        "needs_channel": False,
        "consent": False,
        "deliver": False,
        "day": "daily",
        "time": "08:00",
        "instructions": (
            "Search the web for the most important technology and world news from "
            "the last 24 hours and write a concise 5-bullet briefing, saved as a "
            "markdown file."
        ),
    },
    {
        "key": "inboxdigest",
        "title": "Inbox digest",
        "blurb": "One short digest of your unread email.",
        "cadence": "Weekdays",
        "category": "Personal",
        "conns": [{"name": "gmail", "why": "Your unread email"}],
        "needs_channel": False,
        "consent": False,
        "deliver": False,
        "day": "weekdays",
        "time": "09:00",
        "instructions": "Summarize my unread email into one short digest note.",
    },
    {
        "key": "cleanup",
        "title": "Folder cleanup",
        "blurb": "Sort recent Downloads into tidy folders by type.",
        "cadence": "Weekly",
        "category": "Personal",
        "conns": [],
        "needs_channel": False,
        "consent": False,
        "deliver": False,
        "day": "fri",
        "time": "17:30",
        "instructions": "Sort my recent Downloads into tidy folders by file type.",
    },
    {
        "key": "code-digest",
        "title": "Daily codebase digest",
        "blurb": "A daily summary of repo changes and new risks, saved for review.",
        "cadence": "Daily",
        "category": "Engineering",
        "conns": [{"name": "github", "why": "The repository being summarized"}],
        "needs_repo": True,
        "needs_channel": False,
        "consent": False,
        "deliver": False,
        "day": "daily",
        "time": "08:30",
        "instructions": (
            "Review the GitHub repository {repo} for changes since yesterday: new "
            "commits, open pull requests, and any newly introduced risks or "
            "dependencies. Write a daily digest as a markdown file."
        ),
    },
    {
        "key": "pr-risk",
        "title": "PR risk review",
        "blurb": "Review open PRs for risk and draft a fix plan for the reviewer.",
        "cadence": "Weekdays",
        "category": "Engineering",
        "conns": [{"name": "github", "why": "Open pull requests to review"}],
        "needs_repo": True,
        "needs_channel": False,
        "consent": False,
        "deliver": False,
        "day": "weekdays",
        "time": "09:30",
        "instructions": (
            "In the GitHub repository {repo}, review open pull requests for risk: "
            "dependency changes, broad permissions, unreviewed migrations. For each "
            "risky PR, draft a short review note with a proposed fix."
        ),
    },
    {
        "key": "dependency-audit",
        "title": "Dependency audit",
        "blurb": "Scan lockfiles for vulnerable dependencies and propose the smallest upgrade.",
        "cadence": "Weekly",
        "category": "Security",
        "conns": [{"name": "github", "why": "Lockfiles and manifests to scan"}],
        "needs_repo": True,
        "needs_channel": False,
        "consent": False,
        "deliver": False,
        "day": "mon",
        "time": "10:00",
        "instructions": (
            "In the GitHub repository {repo}, scan lockfiles and manifests for "
            "known-vulnerable dependencies. Prioritize what is actually reachable "
            "and propose the smallest upgrade that closes each hole."
        ),
    },
    {
        "key": "bug-triage",
        "title": "Bug triage from Slack",
        "blurb": "Watch a Slack channel for bug reports and file triaged GitHub issues.",
        "cadence": "Weekdays",
        "category": "Engineering",
        "conns": [
            {"name": "slack", "why": "Where bug reports are posted"},
            {"name": "github", "why": "Where triaged issues land"},
        ],
        "needs_channel": True,
        "consent": True,
        "deliver": False,
        "day": "weekdays",
        "time": "09:00",
        "instructions": (
            "Check the Slack channel {channel} for messages that look like bug "
            "reports. For each one, investigate briefly and create a triaged GitHub "
            "issue in {repo} with a reproduction summary. Reply in Slack with the "
            "issue link using send_message."
        ),
    },
]


def template_catalog() -> list[dict[str, Any]]:
    """Public catalog shape: everything the marketplace UI needs to render cards."""
    out: list[dict[str, Any]] = []
    for t in AUTOMATION_TEMPLATES:
        out.append(
            {
                "key": t["key"],
                "title": t["title"],
                "blurb": t["blurb"],
                "cadence": t["cadence"],
                "category": t["category"],
                "conns": t["conns"],
                "needs_repo": bool(t.get("needs_repo")),
                "needs_channel": bool(t.get("needs_channel")),
                "consent": bool(t.get("consent")),
                "deliver": bool(t.get("deliver")),
                "day": t.get("day", "daily"),
                "time": t.get("time", "09:00"),
            }
        )
    return out


def find_template(key: str) -> Optional[dict[str, Any]]:
    key = (key or "").strip()
    for t in AUTOMATION_TEMPLATES:
        if t["key"] == key:
            return t
    return None


def cron_for(day: str, hhmm: str) -> str:
    """Assemble a 5-field cron from a day key + 'HH:MM' (matches the quickstart)."""
    try:
        h, m = (hhmm or "09:00").split(":")
        hour = max(0, min(23, int(h) or 9))
        minute = max(0, min(59, int(m) or 0))
    except ValueError:
        hour, minute = 9, 0
    dow = _DAY_DOW.get((day or "daily").strip(), "*")
    return f"{minute} {hour} * * {dow}"


def render_instructions(template: dict[str, Any], ctx: dict[str, str]) -> str:
    """Fill {repo} / {channel} / {deliver} placeholders in a template body."""
    return template["instructions"].format(
        repo=(ctx.get("repo") or "").strip(),
        channel=(ctx.get("channel") or "").strip(),
        deliver=(ctx.get("deliver") or "app").strip(),
    )