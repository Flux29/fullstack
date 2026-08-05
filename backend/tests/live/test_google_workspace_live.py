"""Opt-in destructive smoke tests for all direct Google Workspace APIs.

Run only against controlled development accounts:
    GOOGLE_LIVE_E2E=1 GOOGLE_LIVE_USER_ID=<full-stack-user-uuid> pytest tests/live -v

Sending real email is separately gated:
    GOOGLE_LIVE_SEND_E2E=1 GOOGLE_LIVE_SEND_TO=<controlled-recipient>
"""

import os
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from pydantic_ai.messages import ToolCallPart
from pydantic_ai.tools import ToolDenied

from app.agents.assistant import Deps, get_agent
from app.agents.google_workspace_api import build_google_api_toolset, google_api_kind
from app.core.config import settings
from app.core.crypto import encrypt_value
from app.db.session import get_db_context
from app.repositories import chat_file as chat_file_repo
from app.repositories import mcp_connection as mcp_connection_repo
from app.services.file_storage import get_file_storage
from app.services.mcp_connection import (
    McpConnectionService,
    _decode_payload,
    _resolve_auth_headers,
    build_toolsets_for_user,
)

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.skipif(
        os.getenv("GOOGLE_LIVE_E2E") != "1",
        reason="Set GOOGLE_LIVE_E2E=1 to run destructive Google API tests",
    ),
]


def _tool(toolset, name):
    return toolset.wrapped.tools[name].function


@pytest.fixture
async def google_tools():
    raw_user_id = os.environ.get("GOOGLE_LIVE_USER_ID")
    if not raw_user_id:
        pytest.fail("GOOGLE_LIVE_USER_ID is required when GOOGLE_LIVE_E2E=1")
    user_id = UUID(raw_user_id)
    result = {}
    async with get_db_context() as db:
        connections, _ = await mcp_connection_repo.list_for_user(
            db, user_id=user_id, enabled_only=True
        )
        for connection in connections:
            kind = google_api_kind(connection.url)
            if not kind:
                continue
            headers = await _resolve_auth_headers(db, connection)
            if not headers:
                pytest.fail(f"{connection.name} has no usable OAuth token")
            result[kind] = build_google_api_toolset(
                name=connection.name,
                url=connection.url,
                access_token=headers["Authorization"].removeprefix("Bearer "),
                allowed_tools=None,
                user_id=raw_user_id,
            )
    missing = {"gmail", "calendar", "drive", "docs", "sheets", "slides", "chat", "contacts"} - set(
        result
    )
    if missing:
        pytest.fail(f"Reauthorize/upgrade these Google connections first: {sorted(missing)}")
    return result


async def test_gmail_and_calendar_live(google_tools):
    gmail = google_tools["gmail"]
    calendar = google_tools["calendar"]
    run_id = uuid4().hex[:10]
    profile = await _tool(gmail, "get_profile")()
    assert profile["emailAddress"]
    assert "threads" in await _tool(gmail, "search_threads")("in:inbox", 1)
    draft = await _tool(gmail, "create_draft")(
        profile["emailAddress"], f"fullstack-e2e-{run_id}", "Delete this automated draft."
    )
    try:
        now = datetime.now(UTC)
        calendars = await _tool(calendar, "list_calendars")(10)
        assert "items" in calendars
        events = await _tool(calendar, "list_events")(
            (now - timedelta(days=1)).isoformat(),
            (now + timedelta(days=14)).isoformat(),
        )
        if events.get("items"):
            assert await _tool(calendar, "get_event")(events["items"][0]["id"])
        slots = await _tool(calendar, "suggest_time")(
            now.isoformat(), (now + timedelta(hours=8)).isoformat(), 30
        )
        assert "open_slots" in slots
    finally:
        await _tool(gmail, "delete_draft")(draft["id"])


async def test_gmail_send_live(google_tools):
    if os.getenv("GOOGLE_LIVE_SEND_E2E") != "1":
        pytest.skip("Set GOOGLE_LIVE_SEND_E2E=1 to send real test emails")
    recipient = os.getenv("GOOGLE_LIVE_SEND_TO")
    if not recipient:
        pytest.fail("GOOGLE_LIVE_SEND_TO is required when GOOGLE_LIVE_SEND_E2E=1")

    gmail = google_tools["gmail"]
    run_id = uuid4().hex[:10]
    draft = await _tool(gmail, "create_draft")(
        recipient,
        f"fullstack-e2e-send-draft-{run_id}",
        "Controlled live test of Gmail drafts.send.",
    )
    sent_draft = await _tool(gmail, "send_draft")(draft["id"])
    assert sent_draft["id"]
    assert sent_draft.get("threadId")

    sent_message = await _tool(gmail, "send_message")(
        recipient,
        f"fullstack-e2e-send-message-{run_id}",
        "Controlled live test of Gmail messages.send.",
    )
    assert sent_message["id"]
    assert sent_message.get("threadId")


async def test_every_connection_test_action_live():
    raw_user_id = os.environ.get("GOOGLE_LIVE_USER_ID")
    if not raw_user_id:
        pytest.fail("GOOGLE_LIVE_USER_ID is required when GOOGLE_LIVE_E2E=1")
    user_id = UUID(raw_user_id)
    tested = set()
    async with get_db_context() as db:
        service = McpConnectionService(db)
        connections, _ = await service.list_for_user(user_id=user_id)
        for connection in connections:
            kind = google_api_kind(connection.url)
            if not kind:
                continue
            _, tools, error = await service.test(user_id=user_id, connection_id=connection.id)
            assert error is None, f"{kind} Test action failed: {error}"
            assert tools, f"{kind} Test action returned no tools"
            tested.add(kind)
    assert tested == {
        "gmail",
        "calendar",
        "drive",
        "docs",
        "sheets",
        "slides",
        "chat",
        "contacts",
    }


async def test_expired_access_token_refreshes_live():
    user_id = UUID(os.environ["GOOGLE_LIVE_USER_ID"])
    connection_id = None
    old_access_token = None
    async with get_db_context() as db:
        connections, _ = await mcp_connection_repo.list_for_user(db, user_id=user_id)
        connection = next(item for item in connections if google_api_kind(item.url) == "gmail")
        payload = _decode_payload(connection.oauth_payload, connection.name)
        assert payload and payload.refresh_token, "Gmail needs an offline refresh token"
        connection_id = connection.id
        old_access_token = payload.access_token
        expired = payload.model_copy(update={"expires_at": 0.0})
        await mcp_connection_repo.update(
            db,
            db_connection=connection,
            update_data={
                "oauth_payload": encrypt_value(expired.model_dump_json(), settings.SECRET_KEY)
            },
        )

    await build_toolsets_for_user(user_id)

    async with get_db_context() as db:
        refreshed = await mcp_connection_repo.get_by_id(db, connection_id)
        payload = _decode_payload(refreshed.oauth_payload, refreshed.name)
        assert payload and payload.access_token
        assert payload.expires_at and payload.expires_at > datetime.now(UTC).timestamp()
        assert payload.access_token != old_access_token or payload.expires_at > 0


async def test_drive_docs_sheets_and_slides_live(google_tools):
    drive = google_tools["drive"]
    docs = google_tools["docs"]
    sheets = google_tools["sheets"]
    slides = google_tools["slides"]
    run_id = uuid4().hex[:10]
    prefix = f"fullstack-e2e-{run_id}"
    cleanup: list[tuple[object, tuple]] = []
    residue: list[str] = []
    internal_file = None
    try:
        folder = await _tool(drive, "create_folder")(prefix)
        cleanup.append((_tool(drive, "delete_file"), (folder["id"],)))

        # Upload a Full Stack file and verify it can be read back and moved.
        from app.agents.google_apis.products import _store_user_file

        internal_file = await _store_user_file(
            os.environ["GOOGLE_LIVE_USER_ID"],
            f"{prefix}.txt",
            "text/plain",
            b"fullstack live upload",
        )
        uploaded = await _tool(drive, "upload_file")(
            internal_file["file_id"], parent_id=folder["id"]
        )
        cleanup.append((_tool(drive, "delete_file"), (uploaded["id"],)))
        assert (
            "fullstack live upload"
            in (await _tool(drive, "read_file_content")(uploaded["id"]))["content"]
        )
        assert (await _tool(drive, "search_files")(f"name = '{prefix}.txt'", 10))["files"]

        second_email = os.getenv("GOOGLE_LIVE_SECOND_EMAIL")
        if second_email:
            permission = await _tool(drive, "create_permission")(
                uploaded["id"], second_email, send_notification=False
            )
            await _tool(drive, "delete_permission")(uploaded["id"], permission["id"])

        document = await _tool(docs, "create_document")(f"{prefix}-doc")
        cleanup.append((_tool(docs, "delete_document"), (document["documentId"],)))
        await _tool(docs, "insert_text")(document["documentId"], "hello workspace", 1)
        read_document = await _tool(docs, "read_doc")(document["documentId"])
        assert "hello workspace" in str(read_document)

        spreadsheet = await _tool(sheets, "create_spreadsheet")(f"{prefix}-sheet")
        spreadsheet_id = spreadsheet["spreadsheetId"]
        cleanup.append((_tool(sheets, "delete_spreadsheet"), (spreadsheet_id,)))
        await _tool(sheets, "update_values")(spreadsheet_id, "A1:B1", [["name", "value"]])
        await _tool(sheets, "append_values")(spreadsheet_id, "A:B", [[prefix, 1]])
        assert (await _tool(sheets, "get_values")(spreadsheet_id, "A1:B2"))["values"][1][
            0
        ] == prefix
        added_sheet = await _tool(sheets, "add_sheet")(spreadsheet_id, "temporary")
        sheet_id = added_sheet["replies"][0]["addSheet"]["properties"]["sheetId"]
        await _tool(sheets, "rename_sheet")(spreadsheet_id, sheet_id, "renamed")
        await _tool(sheets, "delete_sheet")(spreadsheet_id, sheet_id)

        presentation = await _tool(slides, "create_presentation")(f"{prefix}-slides")
        presentation_id = presentation["presentationId"]
        cleanup.append((_tool(slides, "delete_presentation"), (presentation_id,)))
        added = await _tool(slides, "add_slide")(presentation_id)
        slide_id = added["replies"][0]["createSlide"]["objectId"]
        box = await _tool(slides, "create_text_box")(presentation_id, slide_id, 10, 10, 300, 80)
        object_id = box["replies"][0]["createShape"]["objectId"]
        await _tool(slides, "insert_text")(presentation_id, object_id, prefix)
        assert prefix in str(await _tool(slides, "read_presentation")(presentation_id))
        assert await _tool(slides, "get_thumbnail")(presentation_id, slide_id)
        duplicate = await _tool(slides, "duplicate_slide")(presentation_id, slide_id)
        duplicate_id = duplicate["replies"][0]["duplicateObject"]["objectId"]
        await _tool(slides, "move_slides")(presentation_id, [duplicate_id], 0)
        await _tool(slides, "delete_slide")(presentation_id, duplicate_id)
    finally:
        for function, args in reversed(cleanup):
            try:
                await function(*args)
            except Exception as exc:  # report every leaked Google resource
                residue.append(f"{args}: {exc}")
        if internal_file:
            async with get_db_context() as db:
                record = await chat_file_repo.get_by_id(db, UUID(internal_file["file_id"]))
                if record:
                    await get_file_storage().delete(record.storage_path)
                    await db.delete(record)
                    await db.flush()
        assert not residue, f"Google cleanup residue: {residue}"


async def test_contacts_live(google_tools):
    contacts = google_tools["contacts"]
    run_id = uuid4().hex[:10]
    assert await _tool(contacts, "get_user_profile")()
    contact = await _tool(contacts, "create_contact")(
        f"fullstack-e2e-{run_id}", [f"{run_id}@example.com"]
    )
    resource_name = contact["resourceName"]
    group = None
    try:
        found = await _tool(contacts, "search_contacts")(run_id)
        assert found.get("results")
        current = await _tool(contacts, "get_contact")(resource_name)
        await _tool(contacts, "update_contact")(
            resource_name, current["etag"], f"fullstack-e2e-{run_id}-updated"
        )
        group = await _tool(contacts, "create_contact_group")(f"fullstack-e2e-{run_id}")
        await _tool(contacts, "modify_contact_group")(group["resourceName"], [resource_name])
        await _tool(contacts, "modify_contact_group")(
            group["resourceName"], remove_resource_names=[resource_name]
        )
        try:
            await _tool(contacts, "search_directory_people")(run_id, 1)
        except RuntimeError as exc:
            if "permission" not in str(exc).casefold() and "workspace" not in str(exc).casefold():
                raise
    finally:
        if group:
            await _tool(contacts, "delete_contact_group")(group["resourceName"])
        await _tool(contacts, "delete_contact")(resource_name)


async def test_chat_live(google_tools):
    second_email = os.getenv("GOOGLE_LIVE_SECOND_EMAIL")
    if not second_email:
        pytest.skip("GOOGLE_LIVE_SECOND_EMAIL is required for Chat membership validation")
    chat = google_tools["chat"]
    run_id = uuid4().hex[:10]
    space = await _tool(chat, "create_space")(f"fullstack-e2e-{run_id}")
    space_name = space["name"]
    try:
        membership = await _tool(chat, "create_membership")(space_name, second_email)
        message = await _tool(chat, "create_message")(space_name, f"fullstack-e2e-{run_id}")
        message_name = message["name"]
        reaction = await _tool(chat, "create_reaction")(message_name, "👍")
        assert (await _tool(chat, "search_messages")(run_id, space_name))["messages"]
        await _tool(chat, "update_message")(message_name, f"fullstack-e2e-{run_id}-updated")
        await _tool(chat, "delete_reaction")(reaction["name"])
        await _tool(chat, "delete_message")(message_name)
        await _tool(chat, "delete_membership")(membership["name"])
    finally:
        await _tool(chat, "delete_space")(space_name)


async def test_real_agent_chat_routes_all_integrations_live(google_tools):
    """Exercise safe read tools through the production model/tool orchestration path."""
    assistant = get_agent(extra_toolsets=list(google_tools.values()))
    cases = [
        ("Use Gmail get_profile and report only the connected address.", "get_profile"),
        ("Use Google Calendar list_calendars and report the count.", "list_calendars"),
        ("Use Google Drive list_recent_files with one result.", "list_recent_files"),
        ("Use Google Docs search_docs for fullstack-e2e.", "search_docs"),
        ("Use Google Sheets search_spreadsheets for fullstack-e2e.", "search_spreadsheets"),
        ("Use Google Slides search_presentations for fullstack-e2e.", "search_presentations"),
        ("Use Google Chat list_spaces and report the count.", "list_spaces"),
        ("Use Google Contacts get_user_profile and report the primary name.", "get_user_profile"),
    ]
    for prompt, expected_tool in cases:
        result = await assistant.agent.run(
            prompt,
            deps=Deps(
                user_id=os.environ["GOOGLE_LIVE_USER_ID"],
                user_name="Google live E2E user",
            ),
        )
        called = {
            part.tool_name
            for message in result.all_messages()
            for part in message.parts
            if isinstance(part, ToolCallPart)
        }
        assert any(name.endswith(expected_tool) for name in called), (prompt, called)


async def test_real_agent_mutations_stop_at_approval_live(google_tools):
    """Prove model-selected mutations defer before any Google request is made."""
    assistant = get_agent(extra_toolsets=list(google_tools.values()))
    cases = [
        (
            "Create a Gmail draft to nobody@example.com with subject E2E and body test.",
            "create_draft",
        ),
        (
            "Send an email message to nobody@example.com with subject E2E and body test.",
            "send_message",
        ),
        ("Create a Google Drive folder named fullstack-e2e-approval-only.", "create_folder"),
        ("Create a Google Doc named fullstack-e2e-approval-only.", "create_document"),
        ("Create a Google Sheet named fullstack-e2e-approval-only.", "create_spreadsheet"),
        (
            "Create a Google Slides presentation named fullstack-e2e-approval-only.",
            "create_presentation",
        ),
        ("Create a Google Chat space named fullstack-e2e-approval-only.", "create_space"),
        ("Create a contact named fullstack-e2e-approval-only.", "create_contact"),
    ]
    for prompt, expected_tool in cases:
        requested: list[str] = []

        async def deny(requests, sink=requested):
            sink.extend(call.tool_name for call in requests.approvals)
            return requests.build_results(
                approvals={
                    call.tool_call_id: ToolDenied("Live approval-path test denial")
                    for call in requests.approvals
                }
            )

        await assistant.agent.run(
            prompt,
            deps=Deps(
                user_id=os.environ["GOOGLE_LIVE_USER_ID"],
                user_name="Google live E2E user",
                approve_tools=deny,
            ),
        )
        assert any(name.endswith(expected_tool) for name in requested), (prompt, requested)
