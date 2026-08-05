"""Direct Gmail and Google Calendar tools backed by generally available APIs.

These integrations deliberately do not use Google's Developer Preview MCP
servers.  A user's ordinary OAuth access token is captured by each toolset and
sent only to the corresponding Google REST API.
"""

from __future__ import annotations

import base64
from datetime import datetime
from email.message import EmailMessage
from typing import Any, Literal
from urllib.parse import quote

import httpx

from app.agents.google_apis import DIRECT_GOOGLE_PRODUCTS, build_product_toolset, probe_product
from app.agents.mcp import McpToolInfo, _tool_prefix

GMAIL_API_URL = "https://gmail.googleapis.com/gmail/v1"
CALENDAR_API_URL = "https://www.googleapis.com/calendar/v3"

GMAIL_SCOPES = (
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose",
)
CALENDAR_SCOPES = ("https://www.googleapis.com/auth/calendar.readonly",)

_API_TIMEOUT = httpx.Timeout(20.0, connect=10.0)


GoogleApiKind = Literal[
    "gmail", "calendar", "drive", "docs", "sheets", "slides", "chat", "contacts"
]


def google_api_kind(url: str) -> GoogleApiKind | None:
    normalized = url.rstrip("/")
    if normalized == GMAIL_API_URL:
        return "gmail"
    if normalized == CALENDAR_API_URL:
        return "calendar"
    for kind, product in DIRECT_GOOGLE_PRODUCTS.items():
        if normalized == product.url:
            return kind  # type: ignore[return-value]
    return None


def google_api_scopes(url: str) -> tuple[str, ...] | None:
    kind = google_api_kind(url)
    if kind == "gmail":
        return GMAIL_SCOPES
    if kind == "calendar":
        return CALENDAR_SCOPES
    if kind in DIRECT_GOOGLE_PRODUCTS:
        return DIRECT_GOOGLE_PRODUCTS[kind].scopes
    return None


async def _request(
    access_token: str,
    method: str,
    url: str,
    *,
    params: dict[str, Any] | None = None,
    json: dict[str, Any] | None = None,
) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=_API_TIMEOUT) as client:
        response = await client.request(
            method,
            url,
            headers={"Authorization": f"Bearer {access_token}"},
            params=params,
            json=json,
        )
    if response.is_error:
        try:
            detail = response.json().get("error", {}).get("message")
        except Exception:
            detail = None
        raise RuntimeError(detail or f"Google API returned HTTP {response.status_code}")
    if not response.content:
        return {}
    return response.json()


def _headers(message: dict[str, Any]) -> dict[str, str]:
    values = message.get("payload", {}).get("headers", [])
    return {str(item.get("name", "")).lower(): str(item.get("value", "")) for item in values}


def _decode_body(payload: dict[str, Any]) -> str:
    body = payload.get("body", {}).get("data")
    if body:
        try:
            return base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)).decode(
                "utf-8", errors="replace"
            )
        except Exception:
            return ""
    plain: list[str] = []
    html: list[str] = []
    for part in payload.get("parts", []):
        text = _decode_body(part)
        if not text:
            continue
        if part.get("mimeType") == "text/plain":
            plain.append(text)
        elif part.get("mimeType") == "text/html":
            html.append(text)
    return "\n".join(plain or html)


def _message_summary(message: dict[str, Any], *, include_body: bool = False) -> dict[str, Any]:
    headers = _headers(message)
    result: dict[str, Any] = {
        "id": message.get("id"),
        "thread_id": message.get("threadId"),
        "from": headers.get("from"),
        "to": headers.get("to"),
        "subject": headers.get("subject"),
        "date": headers.get("date"),
        "snippet": message.get("snippet"),
        "label_ids": message.get("labelIds", []),
    }
    if include_body:
        result["body"] = _decode_body(message.get("payload", {}))[:20_000]
    return result


async def probe_google_api(url: str, access_token: str) -> list[McpToolInfo]:
    kind = google_api_kind(url)
    if kind == "gmail":
        await _request(access_token, "GET", f"{GMAIL_API_URL}/users/me/profile")
        return [
            McpToolInfo(name=name, description=description) for name, description in _GMAIL_TOOLS
        ]
    if kind == "calendar":
        await _request(
            access_token,
            "GET",
            f"{CALENDAR_API_URL}/users/me/calendarList",
            params={"maxResults": 1},
        )
        return [
            McpToolInfo(name=name, description=description) for name, description in _CALENDAR_TOOLS
        ]
    if kind in DIRECT_GOOGLE_PRODUCTS:
        return await probe_product(kind, access_token)
    raise ValueError("Not a supported direct Google API integration URL")


_GMAIL_TOOLS = (
    ("get_profile", "Return the email address and mailbox statistics for the connected account."),
    ("search_threads", "Search Gmail using the same query syntax as the Gmail search box."),
    ("get_thread", "Read every message in a Gmail thread."),
    ("get_message", "Read one Gmail message, including its text body."),
    ("list_labels", "List Gmail labels."),
    ("list_drafts", "List draft messages."),
    ("create_draft", "Create a Gmail draft without sending it."),
    ("send_draft", "Send an existing Gmail draft to its configured recipients."),
    ("send_message", "Compose and immediately send a new Gmail message."),
    ("delete_draft", "Delete a Gmail draft."),
)

_CALENDAR_TOOLS = (
    ("list_calendars", "List calendars available to the connected account."),
    ("list_events", "List events in a time range."),
    ("search_events", "Search calendar events in a time range."),
    ("get_event", "Read one calendar event."),
    ("suggest_time", "Find open time slots around existing calendar events."),
)


def build_google_api_toolset(
    *,
    name: str,
    url: str,
    access_token: str,
    allowed_tools: list[str] | None,
    user_id: str | None = None,
) -> Any:
    """Build a prefixed PydanticAI FunctionToolset for one Google integration."""
    from pydantic_ai.toolsets import FunctionToolset

    kind = google_api_kind(url)
    if kind is None:
        raise ValueError("Not a supported direct Google API integration URL")
    if kind in DIRECT_GOOGLE_PRODUCTS:
        return build_product_toolset(
            kind=kind,
            name=name,
            access_token=access_token,
            allowed_tools=allowed_tools,
            user_id=user_id,
        )
    toolset: Any = FunctionToolset(id=f"google-api:{name}")

    def add(
        function: Any, tool_name: str, description: str, *, requires_approval: bool = False
    ) -> None:
        if allowed_tools is None or tool_name in allowed_tools:
            toolset.add_function(
                function,
                name=tool_name,
                description=description,
                requires_approval=requires_approval,
            )

    if kind == "gmail":

        async def get_profile() -> dict[str, Any]:
            return await _request(access_token, "GET", f"{GMAIL_API_URL}/users/me/profile")

        async def get_message(message_id: str) -> dict[str, Any]:
            message = await _request(
                access_token,
                "GET",
                f"{GMAIL_API_URL}/users/me/messages/{quote(message_id, safe='')}",
                params={"format": "full"},
            )
            return _message_summary(message, include_body=True)

        async def get_thread(thread_id: str) -> dict[str, Any]:
            thread = await _request(
                access_token,
                "GET",
                f"{GMAIL_API_URL}/users/me/threads/{quote(thread_id, safe='')}",
                params={"format": "full"},
            )
            return {
                "id": thread.get("id"),
                "messages": [
                    _message_summary(message, include_body=True)
                    for message in thread.get("messages", [])
                ],
            }

        async def search_threads(query: str = "in:inbox", max_results: int = 10) -> dict[str, Any]:
            count = max(1, min(max_results, 20))
            found = await _request(
                access_token,
                "GET",
                f"{GMAIL_API_URL}/users/me/threads",
                params={"q": query, "maxResults": count},
            )
            summaries: list[dict[str, Any]] = []
            for item in found.get("threads", []):
                thread = await _request(
                    access_token,
                    "GET",
                    f"{GMAIL_API_URL}/users/me/threads/{quote(item['id'], safe='')}",
                    params={
                        "format": "metadata",
                        "metadataHeaders": ["From", "To", "Subject", "Date"],
                    },
                )
                messages = thread.get("messages", [])
                if messages:
                    summaries.append(_message_summary(messages[-1]))
            return {"threads": summaries, "result_size_estimate": found.get("resultSizeEstimate")}

        async def list_labels() -> dict[str, Any]:
            return await _request(access_token, "GET", f"{GMAIL_API_URL}/users/me/labels")

        async def list_drafts(max_results: int = 10) -> dict[str, Any]:
            return await _request(
                access_token,
                "GET",
                f"{GMAIL_API_URL}/users/me/drafts",
                params={"maxResults": max(1, min(max_results, 50))},
            )

        async def create_draft(
            to: str, subject: str, body: str, cc: str | None = None, bcc: str | None = None
        ) -> dict[str, Any]:
            raw = await build_raw_message(to, subject, body, cc, bcc)
            return await _request(
                access_token,
                "POST",
                f"{GMAIL_API_URL}/users/me/drafts",
                json={"message": {"raw": raw}},
            )

        async def build_raw_message(
            to: str, subject: str, body: str, cc: str | None = None, bcc: str | None = None
        ) -> str:
            profile = await get_profile()
            message = EmailMessage()
            message["From"] = profile["emailAddress"]
            message["To"] = to
            message["Subject"] = subject
            if cc:
                message["Cc"] = cc
            if bcc:
                message["Bcc"] = bcc
            message.set_content(body)
            return base64.urlsafe_b64encode(message.as_bytes()).decode().rstrip("=")

        async def send_draft(draft_id: str) -> dict[str, Any]:
            return await _request(
                access_token,
                "POST",
                f"{GMAIL_API_URL}/users/me/drafts/send",
                json={"id": draft_id},
            )

        async def send_message(
            to: str, subject: str, body: str, cc: str | None = None, bcc: str | None = None
        ) -> dict[str, Any]:
            raw = await build_raw_message(to, subject, body, cc, bcc)
            return await _request(
                access_token,
                "POST",
                f"{GMAIL_API_URL}/users/me/messages/send",
                json={"raw": raw},
            )

        async def delete_draft(draft_id: str) -> dict[str, Any]:
            return await _request(
                access_token,
                "DELETE",
                f"{GMAIL_API_URL}/users/me/drafts/{quote(draft_id, safe='')}",
            )

        functions = {
            "get_profile": get_profile,
            "search_threads": search_threads,
            "get_thread": get_thread,
            "get_message": get_message,
            "list_labels": list_labels,
            "list_drafts": list_drafts,
            "create_draft": create_draft,
            "send_draft": send_draft,
            "send_message": send_message,
            "delete_draft": delete_draft,
        }
        for tool_name, description in _GMAIL_TOOLS:
            add(
                functions[tool_name],
                tool_name,
                description,
                requires_approval=tool_name
                in {"create_draft", "send_draft", "send_message", "delete_draft"},
            )
    else:

        async def list_calendars(max_results: int = 100) -> dict[str, Any]:
            return await _request(
                access_token,
                "GET",
                f"{CALENDAR_API_URL}/users/me/calendarList",
                params={"maxResults": max(1, min(max_results, 250))},
            )

        async def list_events(
            time_min: str,
            time_max: str,
            calendar_id: str = "primary",
            max_results: int = 50,
            query: str | None = None,
        ) -> dict[str, Any]:
            params: dict[str, Any] = {
                "timeMin": time_min,
                "timeMax": time_max,
                "singleEvents": "true",
                "orderBy": "startTime",
                "maxResults": max(1, min(max_results, 250)),
            }
            if query:
                params["q"] = query
            return await _request(
                access_token,
                "GET",
                f"{CALENDAR_API_URL}/calendars/{quote(calendar_id, safe='')}/events",
                params=params,
            )

        async def search_events(
            query: str, time_min: str, time_max: str, calendar_id: str = "primary"
        ) -> dict[str, Any]:
            return await list_events(time_min, time_max, calendar_id, 50, query)

        async def get_event(event_id: str, calendar_id: str = "primary") -> dict[str, Any]:
            return await _request(
                access_token,
                "GET",
                f"{CALENDAR_API_URL}/calendars/{quote(calendar_id, safe='')}/events/{quote(event_id, safe='')}",
            )

        async def suggest_time(
            time_min: str,
            time_max: str,
            duration_minutes: int = 30,
            calendar_id: str = "primary",
        ) -> dict[str, Any]:
            """Find open slots. Inputs must be RFC3339 timestamps with timezone offsets."""
            start = datetime.fromisoformat(time_min.replace("Z", "+00:00"))
            end = datetime.fromisoformat(time_max.replace("Z", "+00:00"))
            duration_seconds = max(1, duration_minutes) * 60
            data = await list_events(time_min, time_max, calendar_id, 250)
            busy: list[tuple[datetime, datetime]] = []
            for event in data.get("items", []):
                event_start = event.get("start", {}).get("dateTime")
                event_end = event.get("end", {}).get("dateTime")
                if event_start and event_end:
                    busy.append(
                        (
                            datetime.fromisoformat(event_start.replace("Z", "+00:00")),
                            datetime.fromisoformat(event_end.replace("Z", "+00:00")),
                        )
                    )
            cursor = start
            slots: list[dict[str, str]] = []
            for busy_start, busy_end in sorted(busy):
                if (busy_start - cursor).total_seconds() >= duration_seconds:
                    slots.append({"start": cursor.isoformat(), "end": busy_start.isoformat()})
                cursor = max(cursor, busy_end)
            if (end - cursor).total_seconds() >= duration_seconds:
                slots.append({"start": cursor.isoformat(), "end": end.isoformat()})
            return {"duration_minutes": duration_minutes, "open_slots": slots[:20]}

        functions = {
            "list_calendars": list_calendars,
            "list_events": list_events,
            "search_events": search_events,
            "get_event": get_event,
            "suggest_time": suggest_time,
        }
        for tool_name, description in _CALENDAR_TOOLS:
            add(functions[tool_name], tool_name, description)

    return toolset.prefixed(_tool_prefix(name))
