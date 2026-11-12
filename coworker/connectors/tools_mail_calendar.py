"""Gmail, Google Calendar and Outlook tools (batch A of the platform split).

Moved out of `integration_tools.py` verbatim: same tool names, schemas, gating and
registration order — `tests/test_integration_tool_catalog.py` freezes that catalog and is
the guard for the move.

Everything here resolves an *account* at call time: `_gmail_profile` / `_gcal_profile` load a
mailbox profile out of the SecretStore and return `(email, profile, error)`, so a user signed
into three mailboxes gets three valid tokens and the model only ever names one.
"""

from __future__ import annotations

import base64
import datetime as _dt
from email.message import EmailMessage
from typing import Any, Optional
from urllib.parse import quote

from ..secrets import SecretStore
from . import toolkit
from .toolkit import (
    _GEN_ACCOUNT_PROP,
    _account_profile,
    _acct_result,
    _attach,
    _google_headers,
    _graph_headers,
    _schema,
)


def _gmail_profile(
    secrets: SecretStore, account: str = ""
) -> tuple[str, Optional[dict[str, Any]], Optional[dict[str, str]]]:
    """(email, profile, err) for the requested — or default — mailbox, with the
    managed token refreshed in place. Multi-account: `gmail:account:<email>`."""
    from . import gmail_accounts

    email, key, profile = gmail_accounts.resolve(secrets, account)
    if profile is None:
        hint = (
            f"no gmail account matching {account!r}"
            if account
            else "gmail is not connected"
        )
        return "", None, {"error": hint}
    if profile.get("managed"):
        from ..cloud import ensure_fresh_connector_token
        from ..config import load_config

        ensure_fresh_connector_token(secrets, load_config(), "gmail", profile_key=key)
        profile = secrets.get(key) or profile
    if not profile.get("access_token"):
        return "", None, {"error": f"gmail account {email} has no usable token"}
    return email, profile, None


def _gcal_profile(
    secrets: SecretStore, account: str = ""
) -> tuple[str, Optional[dict[str, Any]], Optional[dict[str, str]]]:
    """(email, profile, err) for the requested — or default — Google account,
    with the managed token refreshed in place. Multi-account:
    `google_calendar:account:<email>`."""
    from . import gcal_accounts

    email, key, profile = gcal_accounts.resolve(secrets, account)
    if profile is None:
        hint = (
            f"no google calendar account matching {account!r}"
            if account
            else "google calendar is not connected"
        )
        return "", None, {"error": hint}
    if profile.get("managed"):
        from ..cloud import ensure_fresh_connector_token
        from ..config import load_config

        ensure_fresh_connector_token(
            secrets, load_config(), "google_calendar", profile_key=key
        )
        profile = secrets.get(key) or profile
    if not profile.get("access_token"):
        return (
            "",
            None,
            {"error": f"google calendar account {email} has no usable token"},
        )
    return email, profile, None


def _gmail_filters(secrets: SecretStore) -> Optional[dict[str, list[str]]]:
    from . import gmail_accounts

    f = gmail_accounts.get_filters(secrets)
    return f if (f["senders"] or f["labels"]) else None


def _gmail_label_map(token: str) -> dict[str, str]:
    """Label id → name for the mailbox (names are what the user filters on)."""
    resp = toolkit._request(
        "GET",
        "https://gmail.googleapis.com/gmail/v1/users/me/labels",
        headers=_google_headers(token),
    )
    if not resp.get("ok"):
        return {}
    labels = (resp.get("data") or {}).get("labels") or []
    return {str(label.get("id") or ""): str(label.get("name") or "") for label in labels}


def _gmail_is_hidden(
    message: dict[str, Any],
    filters: dict[str, list[str]],
    label_map: dict[str, str],
) -> bool:
    from .gmail_accounts import sender_matches

    if filters["senders"] and sender_matches(
        _gmail_from_address(message), filters["senders"]
    ):
        return True
    if filters["labels"]:
        wanted = {name.lower() for name in filters["labels"]}
        for lid in message.get("labelIds") or []:
            if (
                label_map.get(str(lid), "").lower() in wanted
                or str(lid).lower() in wanted
            ):
                return True
    return False


def _gmail_from_address(message: dict[str, Any]) -> str:
    from email.utils import parseaddr

    for h in (message.get("payload") or {}).get("headers") or []:
        if str(h.get("name", "")).lower() == "from":
            return parseaddr(str(h.get("value") or ""))[1]
    return ""



def add_mail_calendar_tools(tools: list, secrets: SecretStore) -> None:
    """Register the mail/calendar tools on `tools` (order preserved)."""
    _ACCOUNT_PROP = {
        "type": "string",
        "description": "Mailbox email to use; omit for the default account.",
    }

    def gmail_search_messages(
        query: str, max_results: int = 10, account: str = ""
    ) -> dict[str, Any]:
        email, profile, err = _gmail_profile(secrets, account)
        if err:
            return err
        token = profile["access_token"]
        result = toolkit._request(
            "GET",
            "https://gmail.googleapis.com/gmail/v1/users/me/messages",
            headers=_google_headers(token),
            params={"q": query, "maxResults": max(1, min(int(max_results or 10), 20))},
        )
        filters = _gmail_filters(secrets)
        if result.get("ok") and filters:
            # Enforce "Never show agents" HERE, silently: matching hits are
            # omitted (no tombstone); the count rides the `_display` sidecar for
            # the user's tool card + audit — never the agent-visible content.
            data = dict(result.get("data") or {})
            label_map = _gmail_label_map(token) if filters["labels"] else {}
            kept, hidden = [], 0
            for m in data.get("messages") or []:
                meta = toolkit._request(
                    "GET",
                    f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{m.get('id')}",
                    headers=_google_headers(token),
                    params={"format": "metadata", "metadataHeaders": "From"},
                )
                detail = meta.get("data") if meta.get("ok") else None
                # Fail-open on a metadata miss: ids alone reveal nothing, and
                # gmail_get_message re-enforces before any content flows.
                if isinstance(detail, dict) and _gmail_is_hidden(
                    detail, filters, label_map
                ):
                    hidden += 1
                else:
                    kept.append(m)
            if hidden:
                data["messages"] = kept
                if isinstance(data.get("resultSizeEstimate"), int):
                    data["resultSizeEstimate"] = max(
                        0, data["resultSizeEstimate"] - hidden
                    )
                result = {
                    "ok": True,
                    "data": data,
                    "_display": {"hidden_by_filters": hidden, "connector": "gmail"},
                }
        if result.get("ok"):
            result["account"] = email
        return result

    gmail_search_messages.__name__ = "gmail_search_messages"
    tools.append(
        _attach(
            gmail_search_messages,
            _schema(
                "gmail_search_messages",
                "Search Gmail messages using Gmail query syntax.",
                {
                    "query": {"type": "string"},
                    "max_results": {"type": "integer"},
                    "account": _ACCOUNT_PROP,
                },
                ["query"],
            ),
            caps=["gmail", "read"],
        )
    )

    def gmail_get_message(message_id: str, account: str = "") -> dict[str, Any]:
        email, profile, err = _gmail_profile(secrets, account)
        if err:
            return err
        token = profile["access_token"]
        result = toolkit._request(
            "GET",
            f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{message_id}",
            headers=_google_headers(token),
            params={"format": "full"},
        )
        filters = _gmail_filters(secrets)
        if result.get("ok") and filters:
            data = result.get("data") or {}
            label_map = _gmail_label_map(token) if filters["labels"] else {}
            if isinstance(data, dict) and _gmail_is_hidden(data, filters, label_map):
                # Indistinguishable from a real miss — the agent must not be able
                # to tell "filtered" from "gone" (a tombstone invites probing).
                return {
                    "error": "HTTP 404",
                    "details": {"error": {"code": 404, "message": "Not Found"}},
                    "_display": {"hidden_by_filters": 1, "connector": "gmail"},
                }
        if result.get("ok"):
            result["account"] = email
        return result

    gmail_get_message.__name__ = "gmail_get_message"
    tools.append(
        _attach(
            gmail_get_message,
            _schema(
                "gmail_get_message",
                "Read a Gmail message by ID.",
                {"message_id": {"type": "string"}, "account": _ACCOUNT_PROP},
                ["message_id"],
            ),
            caps=["gmail", "read"],
        )
    )

    def gmail_send_email(
        to: str, subject: str, body: str, cc: str = "", account: str = ""
    ) -> dict[str, Any]:
        email, profile, err = _gmail_profile(secrets, account)
        if err:
            return err
        msg = EmailMessage()
        msg["To"], msg["Subject"] = to, subject
        if cc:
            msg["Cc"] = cc
        msg.set_content(body)
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode().rstrip("=")
        result = toolkit._request(
            "POST",
            "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
            headers=_google_headers(profile["access_token"]),
            json={"raw": raw},
        )
        if result.get("ok"):
            result["account"] = email
        return result

    gmail_send_email.__name__ = "gmail_send_email"
    tools.append(
        _attach(
            gmail_send_email,
            _schema(
                "gmail_send_email",
                "Send an email through Gmail. Requires user approval; the "
                "`account` argument names the sending mailbox on the approval card.",
                {
                    "to": {"type": "string"},
                    "subject": {"type": "string"},
                    "body": {"type": "string"},
                    "cc": {"type": "string"},
                    "account": _ACCOUNT_PROP,
                },
                ["to", "subject", "body"],
            ),
            approval=True,
            caps=["gmail", "write"],
        )
    )

    _CAL_ACCOUNT_PROP = {
        "type": "string",
        "description": "Google account email to use; omit for the default account.",
    }

    def _gcal_result(email: str, result: dict[str, Any]) -> dict[str, Any]:
        # Name the account on every success so approvals/transcripts say whose
        # calendar was touched (same contract as the gmail tools).
        if result.get("ok"):
            result["account"] = email
        return result

    def gcal_list_events(
        calendar_id: str = "primary",
        time_min: str = "",
        time_max: str = "",
        max_results: int = 10,
        account: str = "",
    ) -> dict[str, Any]:
        email, profile, err = _gcal_profile(secrets, account)
        if err:
            return err
        params: dict[str, Any] = {
            "singleEvents": True,
            "orderBy": "startTime",
            "maxResults": max(1, min(int(max_results or 10), 20)),
        }
        if time_min:
            params["timeMin"] = time_min
        if time_max:
            params["timeMax"] = time_max
        return _gcal_result(
            email,
            toolkit._request(
                "GET",
                f"https://www.googleapis.com/calendar/v3/calendars/{calendar_id}/events",
                headers=_google_headers(profile["access_token"]),
                params=params,
            ),
        )

    gcal_list_events.__name__ = "gcal_list_events"
    tools.append(
        _attach(
            gcal_list_events,
            _schema(
                "gcal_list_events",
                "List Google Calendar events. time_min/time_max should be RFC3339 timestamps when provided.",
                {
                    "calendar_id": {"type": "string"},
                    "time_min": {"type": "string"},
                    "time_max": {"type": "string"},
                    "max_results": {"type": "integer"},
                    "account": _CAL_ACCOUNT_PROP,
                },
                [],
            ),
            caps=["calendar", "read"],
        )
    )

    def gcal_free_busy(
        time_min: str,
        time_max: str,
        calendars: str = "primary",
        timezone: str = "UTC",
        account: str = "",
    ) -> dict[str, Any]:
        email, profile, err = _gcal_profile(secrets, account)
        if err:
            return err
        items = [
            {"id": c.strip()}
            for c in str(calendars or "primary").split(",")
            if c.strip()
        ]
        return _gcal_result(
            email,
            toolkit._request(
                "POST",
                "https://www.googleapis.com/calendar/v3/freeBusy",
                headers=_google_headers(profile["access_token"]),
                json={
                    "timeMin": time_min,
                    "timeMax": time_max,
                    "timeZone": timezone,
                    "items": items,
                },
            ),
        )

    gcal_free_busy.__name__ = "gcal_free_busy"
    tools.append(
        _attach(
            gcal_free_busy,
            _schema(
                "gcal_free_busy",
                "Look up busy intervals (availability) for one or more calendars. "
                "time_min/time_max are RFC3339 timestamps; calendars is a comma-separated list of calendar ids.",
                {
                    "time_min": {"type": "string"},
                    "time_max": {"type": "string"},
                    "calendars": {"type": "string"},
                    "timezone": {"type": "string"},
                    "account": _CAL_ACCOUNT_PROP,
                },
                ["time_min", "time_max"],
            ),
            caps=["calendar", "read"],
        )
    )

    def gcal_create_event(
        summary: str,
        start: str,
        end: str,
        calendar_id: str = "primary",
        timezone: str = "UTC",
        description: str = "",
        account: str = "",
    ) -> dict[str, Any]:
        email, profile, err = _gcal_profile(secrets, account)
        if err:
            return err
        payload = {
            "summary": summary,
            "description": description,
            "start": {"dateTime": start, "timeZone": timezone},
            "end": {"dateTime": end, "timeZone": timezone},
        }
        return _gcal_result(
            email,
            toolkit._request(
                "POST",
                f"https://www.googleapis.com/calendar/v3/calendars/{calendar_id}/events",
                headers=_google_headers(profile["access_token"]),
                json=payload,
            ),
        )

    gcal_create_event.__name__ = "gcal_create_event"
    tools.append(
        _attach(
            gcal_create_event,
            _schema(
                "gcal_create_event",
                "Create a Google Calendar event. Requires user approval.",
                {
                    "summary": {"type": "string"},
                    "start": {"type": "string"},
                    "end": {"type": "string"},
                    "calendar_id": {"type": "string"},
                    "timezone": {"type": "string"},
                    "description": {"type": "string"},
                    "account": _CAL_ACCOUNT_PROP,
                },
                ["summary", "start", "end"],
            ),
            approval=True,
            caps=["calendar", "write"],
        )
    )

    def gcal_update_event(
        event_id: str,
        calendar_id: str = "primary",
        summary: str = "",
        start: str = "",
        end: str = "",
        timezone: str = "UTC",
        description: str = "",
        account: str = "",
    ) -> dict[str, Any]:
        email, profile, err = _gcal_profile(secrets, account)
        if err:
            return err
        # PATCH semantics: only the provided fields change.
        payload: dict[str, Any] = {}
        if summary:
            payload["summary"] = summary
        if description:
            payload["description"] = description
        if start:
            payload["start"] = {"dateTime": start, "timeZone": timezone}
        if end:
            payload["end"] = {"dateTime": end, "timeZone": timezone}
        if not payload:
            return {
                "error": "nothing to update — pass summary, description, start, or end"
            }
        return _gcal_result(
            email,
            toolkit._request(
                "PATCH",
                f"https://www.googleapis.com/calendar/v3/calendars/{calendar_id}/events/{event_id}",
                headers=_google_headers(profile["access_token"]),
                json=payload,
            ),
        )

    gcal_update_event.__name__ = "gcal_update_event"
    tools.append(
        _attach(
            gcal_update_event,
            _schema(
                "gcal_update_event",
                "Update fields of a Google Calendar event (only the provided fields change). Requires user approval.",
                {
                    "event_id": {"type": "string"},
                    "calendar_id": {"type": "string"},
                    "summary": {"type": "string"},
                    "start": {"type": "string"},
                    "end": {"type": "string"},
                    "timezone": {"type": "string"},
                    "description": {"type": "string"},
                    "account": _CAL_ACCOUNT_PROP,
                },
                ["event_id"],
            ),
            approval=True,
            caps=["calendar", "write"],
        )
    )

    def gcal_delete_event(
        event_id: str, calendar_id: str = "primary", account: str = ""
    ) -> dict[str, Any]:
        email, profile, err = _gcal_profile(secrets, account)
        if err:
            return err
        return _gcal_result(
            email,
            toolkit._request(
                "DELETE",
                f"https://www.googleapis.com/calendar/v3/calendars/{calendar_id}/events/{event_id}",
                headers=_google_headers(profile["access_token"]),
            ),
        )

    gcal_delete_event.__name__ = "gcal_delete_event"
    tools.append(
        _attach(
            gcal_delete_event,
            _schema(
                "gcal_delete_event",
                "Delete a Google Calendar event. Requires user approval.",
                {
                    "event_id": {"type": "string"},
                    "calendar_id": {"type": "string"},
                    "account": _CAL_ACCOUNT_PROP,
                },
                ["event_id"],
            ),
            approval=True,
            caps=["calendar", "write"],
        )
    )

    def outlook_search_messages(
        query: str = "", max_results: int = 10, account: str = ""
    ) -> dict[str, Any]:
        aid, profile, err = _account_profile(
            secrets, "outlook", account, "access_token"
        )
        if err:
            return err
        params = {"$top": max(1, min(int(max_results or 10), 20))}
        if query:
            params["$search"] = f'"{query}"'
        return _acct_result(
            aid,
            toolkit._request(
                "GET",
                "https://graph.microsoft.com/v1.0/me/messages",
                headers=_graph_headers(profile["access_token"]),
                params=params,
            ),
        )

    outlook_search_messages.__name__ = "outlook_search_messages"
    tools.append(
        _attach(
            outlook_search_messages,
            _schema(
                "outlook_search_messages",
                "Search or list Outlook messages through Microsoft Graph.",
                {
                    "query": {"type": "string"},
                    "max_results": {"type": "integer"},
                    "account": _GEN_ACCOUNT_PROP,
                },
                [],
            ),
            caps=["outlook", "read"],
        )
    )

    def outlook_send_mail(
        to: str, subject: str, body: str, account: str = ""
    ) -> dict[str, Any]:
        aid, profile, err = _account_profile(
            secrets, "outlook", account, "access_token"
        )
        if err:
            return err
        payload = {
            "message": {
                "subject": subject,
                "body": {"contentType": "Text", "content": body},
                "toRecipients": [{"emailAddress": {"address": to}}],
            }
        }
        return _acct_result(
            aid,
            toolkit._request(
                "POST",
                "https://graph.microsoft.com/v1.0/me/sendMail",
                headers=_graph_headers(profile["access_token"]),
                json=payload,
            ),
        )

    outlook_send_mail.__name__ = "outlook_send_mail"
    tools.append(
        _attach(
            outlook_send_mail,
            _schema(
                "outlook_send_mail",
                "Send mail through Outlook/Microsoft Graph. Requires user approval.",
                {
                    "to": {"type": "string"},
                    "subject": {"type": "string"},
                    "body": {"type": "string"},
                    "account": _GEN_ACCOUNT_PROP,
                },
                ["to", "subject", "body"],
            ),
            approval=True,
            caps=["outlook", "write"],
        )
    )

    def outlook_list_events(
        start: str = "", end: str = "", max_results: int = 10, account: str = ""
    ) -> dict[str, Any]:
        aid, profile, err = _account_profile(
            secrets, "outlook", account, "access_token"
        )
        if err:
            return err
        # calendarView expands recurrences and takes a window; /me/events does
        # neither, so a bare call used to return arbitrary (often past) events.
        # Default window: now → +7 days.
        now = _dt.datetime.now(_dt.timezone.utc)
        fmt = "%Y-%m-%dT%H:%M:%SZ"
        return _acct_result(
            aid,
            toolkit._request(
                "GET",
                "https://graph.microsoft.com/v1.0/me/calendarView",
                headers=_graph_headers(profile["access_token"]),
                params={
                    "startDateTime": start or now.strftime(fmt),
                    "endDateTime": end or (now + _dt.timedelta(days=7)).strftime(fmt),
                    "$orderby": "start/dateTime",
                    "$top": max(1, min(int(max_results or 10), 50)),
                },
            ),
        )

    outlook_list_events.__name__ = "outlook_list_events"
    tools.append(
        _attach(
            outlook_list_events,
            _schema(
                "outlook_list_events",
                "List upcoming Outlook calendar events (recurrences expanded, ordered "
                "by start). start/end are ISO timestamps; default window is the next "
                "7 days.",
                {
                    "start": {"type": "string"},
                    "end": {"type": "string"},
                    "max_results": {"type": "integer"},
                    "account": _GEN_ACCOUNT_PROP,
                },
                [],
            ),
            caps=["outlook", "read"],
        )
    )

    def outlook_create_event(
        subject: str,
        start: str,
        end: str,
        timezone: str = "UTC",
        body: str = "",
        attendees: str = "",
        location: str = "",
        teams_meeting: bool = False,
        account: str = "",
    ) -> dict[str, Any]:
        aid, profile, err = _account_profile(
            secrets, "outlook", account, "access_token"
        )
        if err:
            return err
        payload: dict[str, Any] = {
            "subject": subject,
            "body": {"contentType": "Text", "content": body},
            "start": {"dateTime": start, "timeZone": timezone},
            "end": {"dateTime": end, "timeZone": timezone},
        }
        if attendees:
            payload["attendees"] = [
                {"emailAddress": {"address": a.strip()}, "type": "required"}
                for a in attendees.split(",")
                if a.strip()
            ]
        if location:
            payload["location"] = {"displayName": location}
        if teams_meeting:
            payload["isOnlineMeeting"] = True
            payload["onlineMeetingProvider"] = "teamsForBusiness"
        return _acct_result(
            aid,
            toolkit._request(
                "POST",
                "https://graph.microsoft.com/v1.0/me/events",
                headers=_graph_headers(profile["access_token"]),
                json=payload,
            ),
        )

    outlook_create_event.__name__ = "outlook_create_event"
    tools.append(
        _attach(
            outlook_create_event,
            _schema(
                "outlook_create_event",
                "Create an Outlook calendar event; invites go to attendees "
                "(comma-separated emails). teams_meeting adds a Teams link. "
                "Requires user approval.",
                {
                    "subject": {"type": "string"},
                    "start": {"type": "string"},
                    "end": {"type": "string"},
                    "timezone": {"type": "string"},
                    "body": {"type": "string"},
                    "attendees": {"type": "string"},
                    "location": {"type": "string"},
                    "teams_meeting": {"type": "boolean"},
                    "account": _GEN_ACCOUNT_PROP,
                },
                ["subject", "start", "end"],
            ),
            approval=True,
            caps=["outlook", "write"],
        )
    )

    def outlook_update_event(
        event_id: str,
        subject: str = "",
        start: str = "",
        end: str = "",
        timezone: str = "UTC",
        body: str = "",
        location: str = "",
        account: str = "",
    ) -> dict[str, Any]:
        aid, profile, err = _account_profile(
            secrets, "outlook", account, "access_token"
        )
        if err:
            return err
        # PATCH semantics: only the provided fields change.
        payload: dict[str, Any] = {}
        if subject:
            payload["subject"] = subject
        if body:
            payload["body"] = {"contentType": "Text", "content": body}
        if start:
            payload["start"] = {"dateTime": start, "timeZone": timezone}
        if end:
            payload["end"] = {"dateTime": end, "timeZone": timezone}
        if location:
            payload["location"] = {"displayName": location}
        return _acct_result(
            aid,
            toolkit._request(
                "PATCH",
                f"https://graph.microsoft.com/v1.0/me/events/{quote(event_id)}",
                headers=_graph_headers(profile["access_token"]),
                json=payload,
            ),
        )

    outlook_update_event.__name__ = "outlook_update_event"
    tools.append(
        _attach(
            outlook_update_event,
            _schema(
                "outlook_update_event",
                "Change fields of an existing Outlook calendar event (only the "
                "provided fields change). Requires user approval.",
                {
                    "event_id": {"type": "string"},
                    "subject": {"type": "string"},
                    "start": {"type": "string"},
                    "end": {"type": "string"},
                    "timezone": {"type": "string"},
                    "body": {"type": "string"},
                    "location": {"type": "string"},
                    "account": _GEN_ACCOUNT_PROP,
                },
                ["event_id"],
            ),
            approval=True,
            caps=["outlook", "write"],
        )
    )

    def outlook_delete_event(event_id: str, account: str = "") -> dict[str, Any]:
        aid, profile, err = _account_profile(
            secrets, "outlook", account, "access_token"
        )
        if err:
            return err
        return _acct_result(
            aid,
            toolkit._request(
                "DELETE",
                f"https://graph.microsoft.com/v1.0/me/events/{quote(event_id)}",
                headers=_graph_headers(profile["access_token"]),
            ),
        )

    outlook_delete_event.__name__ = "outlook_delete_event"
    tools.append(
        _attach(
            outlook_delete_event,
            _schema(
                "outlook_delete_event",
                "Delete (cancel) an Outlook calendar event. Requires user approval.",
                {"event_id": {"type": "string"}, "account": _GEN_ACCOUNT_PROP},
                ["event_id"],
            ),
            approval=True,
            caps=["outlook", "write"],
        )
    )

    def outlook_respond_event(
        event_id: str, response: str, comment: str = "", account: str = ""
    ) -> dict[str, Any]:
        aid, profile, err = _account_profile(
            secrets, "outlook", account, "access_token"
        )
        if err:
            return err
        actions = {
            "accept": "accept",
            "decline": "decline",
            "tentative": "tentativelyAccept",
        }
        action = actions.get((response or "").strip().lower())
        if not action:
            return {"error": "response must be one of: accept, decline, tentative"}
        return _acct_result(
            aid,
            toolkit._request(
                "POST",
                f"https://graph.microsoft.com/v1.0/me/events/{quote(event_id)}/{action}",
                headers=_graph_headers(profile["access_token"]),
                json={"comment": comment, "sendResponse": True},
            ),
        )

    outlook_respond_event.__name__ = "outlook_respond_event"
    tools.append(
        _attach(
            outlook_respond_event,
            _schema(
                "outlook_respond_event",
                "Respond to an Outlook meeting invite: accept, decline, or "
                "tentative. The organizer is notified. Requires user approval.",
                {
                    "event_id": {"type": "string"},
                    "response": {"type": "string"},
                    "comment": {"type": "string"},
                    "account": _GEN_ACCOUNT_PROP,
                },
                ["event_id", "response"],
            ),
            approval=True,
            caps=["outlook", "write"],
        )
    )
