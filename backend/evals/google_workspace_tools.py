"""Deterministic Pydantic Evals for Google Workspace tool routing and approval policy."""

from typing import TypedDict

from pydantic_evals import Case, Dataset
from pydantic_evals.evaluators import EqualsExpected

from app.agents.google_apis.products import DIRECT_GOOGLE_PRODUCTS
from app.agents.google_workspace_api import (
    CALENDAR_API_URL,
    GMAIL_API_URL,
    build_google_api_toolset,
)


class RoutingInput(TypedDict):
    integration: str
    prompt: str


class RoutingOutput(TypedDict):
    tool: str
    requires_approval: bool


_ROUTES = {
    "calendar": ("list_events", ("calendar", "events")),
    "drive": ("upload_file", ("upload", "drive")),
    "docs": ("append_text", ("append", "document")),
    "sheets": ("append_values", ("append", "row")),
    "slides": ("create_image", ("image", "slide")),
    "chat": ("create_message", ("send", "chat")),
    "contacts": ("update_contact", ("update", "contact")),
}


def evaluate_route(inputs: RoutingInput) -> RoutingOutput:
    """Select the intended tool and verify its real registered approval metadata."""
    integration = inputs["integration"]
    lowered = inputs["prompt"].casefold()
    if integration == "gmail":
        if "send" in lowered and "draft" in lowered:
            tool_name, keywords = "send_draft", ("send", "draft")
        elif "send" in lowered and ("message" in lowered or "email" in lowered):
            tool_name, keywords = "send_message", ("send",)
        else:
            tool_name, keywords = "create_draft", ("draft", "compose")
    else:
        tool_name, keywords = _ROUTES[integration]
    if not all(keyword in lowered for keyword in keywords):
        raise ValueError(f"Prompt does not express the {integration} evaluation intent")
    url = (
        GMAIL_API_URL
        if integration == "gmail"
        else CALENDAR_API_URL
        if integration == "calendar"
        else DIRECT_GOOGLE_PRODUCTS[integration].url
    )
    toolset = build_google_api_toolset(
        name=integration,
        url=url,
        access_token="eval-token",
        allowed_tools=[tool_name],
        user_id="00000000-0000-0000-0000-000000000001",
    )
    tool = toolset.wrapped.tools[tool_name]
    return {"tool": tool_name, "requires_approval": tool.requires_approval}


GOOGLE_WORKSPACE_ROUTING = Dataset[RoutingInput, RoutingOutput, None](
    name="google-workspace-tool-routing",
    evaluators=[EqualsExpected()],
    cases=[
        Case(
            name="gmail-draft-pauses",
            inputs={"integration": "gmail", "prompt": "Compose a draft reply in Gmail"},
            expected_output={"tool": "create_draft", "requires_approval": True},
        ),
        Case(
            name="gmail-send-draft-pauses",
            inputs={"integration": "gmail", "prompt": "Send the approved Gmail draft"},
            expected_output={"tool": "send_draft", "requires_approval": True},
        ),
        Case(
            name="gmail-send-message-pauses",
            inputs={"integration": "gmail", "prompt": "Send this email message now"},
            expected_output={"tool": "send_message", "requires_approval": True},
        ),
        Case(
            name="calendar-read-is-immediate",
            inputs={"integration": "calendar", "prompt": "List my calendar events"},
            expected_output={"tool": "list_events", "requires_approval": False},
        ),
        Case(
            name="drive-upload-pauses",
            inputs={"integration": "drive", "prompt": "Upload this file to Drive"},
            expected_output={"tool": "upload_file", "requires_approval": True},
        ),
        Case(
            name="docs-edit-pauses",
            inputs={"integration": "docs", "prompt": "Append this paragraph to the document"},
            expected_output={"tool": "append_text", "requires_approval": True},
        ),
        Case(
            name="sheets-edit-pauses",
            inputs={"integration": "sheets", "prompt": "Append a row to the sheet"},
            expected_output={"tool": "append_values", "requires_approval": True},
        ),
        Case(
            name="slides-edit-pauses",
            inputs={"integration": "slides", "prompt": "Add an image to the slide"},
            expected_output={"tool": "create_image", "requires_approval": True},
        ),
        Case(
            name="chat-send-pauses",
            inputs={"integration": "chat", "prompt": "Send this message in Chat"},
            expected_output={"tool": "create_message", "requires_approval": True},
        ),
        Case(
            name="contacts-edit-pauses",
            inputs={"integration": "contacts", "prompt": "Update this contact"},
            expected_output={"tool": "update_contact", "requires_approval": True},
        ),
    ],
)
