"""Tests for direct, generally available Gmail and Calendar API tools."""

import base64
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.agents import google_workspace_api as google_api
from app.agents.google_apis import products
from app.agents.google_apis.products import DIRECT_GOOGLE_PRODUCTS


def _function(toolset, name):
    return toolset.wrapped.tools[name].function


class TestGoogleApiRecognition:
    def test_only_exact_api_roots_are_recognized(self):
        assert google_api.google_api_kind(google_api.GMAIL_API_URL) == "gmail"
        assert google_api.google_api_kind(f"{google_api.CALENDAR_API_URL}/") == "calendar"
        assert google_api.google_api_kind("https://gmail.googleapis.com.evil.test/gmail/v1") is None

    def test_scopes_are_least_privilege_for_exposed_tools(self):
        assert google_api.google_api_scopes(google_api.GMAIL_API_URL) == (
            "https://www.googleapis.com/auth/gmail.readonly",
            "https://www.googleapis.com/auth/gmail.compose",
        )
        assert google_api.google_api_scopes(google_api.CALENDAR_API_URL) == (
            "https://www.googleapis.com/auth/calendar.readonly",
        )

    @pytest.mark.parametrize("kind", ["drive", "docs", "sheets", "slides", "chat", "contacts"])
    def test_standard_product_roots_and_scopes_are_recognized(self, kind):
        product = DIRECT_GOOGLE_PRODUCTS[kind]
        assert google_api.google_api_kind(product.url) == kind
        assert google_api.google_api_scopes(product.url) == product.scopes
        assert google_api.google_api_kind(f"{product.url}.evil.test") is None


class TestGmailToolset:
    def test_allowlist_and_prefix_are_applied(self):
        toolset = google_api.build_google_api_toolset(
            name="gmail",
            url=google_api.GMAIL_API_URL,
            access_token="AT",
            allowed_tools=["get_profile", "search_threads"],
        )
        assert toolset.prefix == "gmail"
        assert set(toolset.wrapped.tools) == {"get_profile", "search_threads"}

    @pytest.mark.anyio
    async def test_profile_uses_connected_users_token(self, monkeypatch):
        request = AsyncMock(return_value={"emailAddress": "person@gmail.com"})
        monkeypatch.setattr(google_api, "_request", request)
        toolset = google_api.build_google_api_toolset(
            name="gmail",
            url=google_api.GMAIL_API_URL,
            access_token="user-token",
            allowed_tools=None,
        )

        result = await _function(toolset, "get_profile")()

        assert result["emailAddress"] == "person@gmail.com"
        assert request.await_args.args[0] == "user-token"
        assert request.await_args.args[2].endswith("/users/me/profile")

    @pytest.mark.anyio
    async def test_create_draft_builds_mime_without_sending(self, monkeypatch):
        calls = []

        async def request(token, method, url, **kwargs):
            calls.append((token, method, url, kwargs))
            if url.endswith("/profile"):
                return {"emailAddress": "person@gmail.com"}
            return {"id": "draft-1", "message": {"id": "message-1"}}

        monkeypatch.setattr(google_api, "_request", request)
        toolset = google_api.build_google_api_toolset(
            name="gmail", url=google_api.GMAIL_API_URL, access_token="AT", allowed_tools=None
        )

        result = await _function(toolset, "create_draft")(
            "recipient@example.com", "Subject", "Draft body"
        )

        assert result["id"] == "draft-1"
        assert calls[-1][1:3] == ("POST", f"{google_api.GMAIL_API_URL}/users/me/drafts")
        raw = calls[-1][3]["json"]["message"]["raw"]
        decoded = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)).decode()
        assert "From: person@gmail.com" in decoded
        assert "To: recipient@example.com" in decoded
        assert "Draft body" in decoded

    @pytest.mark.anyio
    async def test_send_draft_uses_existing_draft_id(self, monkeypatch):
        request = AsyncMock(return_value={"id": "sent-message-1", "threadId": "thread-1"})
        monkeypatch.setattr(google_api, "_request", request)
        toolset = google_api.build_google_api_toolset(
            name="gmail", url=google_api.GMAIL_API_URL, access_token="AT", allowed_tools=None
        )

        result = await _function(toolset, "send_draft")("draft-1")

        assert result["id"] == "sent-message-1"
        assert request.await_args.args == (
            "AT",
            "POST",
            f"{google_api.GMAIL_API_URL}/users/me/drafts/send",
        )
        assert request.await_args.kwargs["json"] == {"id": "draft-1"}

    @pytest.mark.anyio
    async def test_send_message_builds_mime_and_sends_directly(self, monkeypatch):
        calls = []

        async def request(token, method, url, **kwargs):
            calls.append((token, method, url, kwargs))
            if url.endswith("/profile"):
                return {"emailAddress": "person@gmail.com"}
            return {"id": "sent-message-1", "threadId": "thread-1"}

        monkeypatch.setattr(google_api, "_request", request)
        toolset = google_api.build_google_api_toolset(
            name="gmail", url=google_api.GMAIL_API_URL, access_token="AT", allowed_tools=None
        )

        result = await _function(toolset, "send_message")(
            "recipient@example.com",
            "Approved subject",
            "Approved body",
            "copy@example.com",
        )

        assert result["id"] == "sent-message-1"
        assert calls[-1][1:3] == (
            "POST",
            f"{google_api.GMAIL_API_URL}/users/me/messages/send",
        )
        raw = calls[-1][3]["json"]["raw"]
        decoded = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)).decode()
        assert "From: person@gmail.com" in decoded
        assert "To: recipient@example.com" in decoded
        assert "Cc: copy@example.com" in decoded
        assert "Subject: Approved subject" in decoded
        assert "Approved body" in decoded

    def test_gmail_mutations_require_human_approval(self):
        toolset = google_api.build_google_api_toolset(
            name="gmail", url=google_api.GMAIL_API_URL, access_token="AT", allowed_tools=None
        )
        for name in ("create_draft", "send_draft", "send_message", "delete_draft"):
            assert toolset.wrapped.tools[name].requires_approval is True
        assert toolset.wrapped.tools["get_profile"].requires_approval is False


class TestCalendarToolset:
    @pytest.mark.anyio
    async def test_list_events_calls_calendar_v3(self, monkeypatch):
        request = AsyncMock(return_value={"items": []})
        monkeypatch.setattr(google_api, "_request", request)
        toolset = google_api.build_google_api_toolset(
            name="google-calendar",
            url=google_api.CALENDAR_API_URL,
            access_token="CAL-TOKEN",
            allowed_tools=["list_events"],
        )

        await _function(toolset, "list_events")(
            "2026-08-03T00:00:00-04:00", "2026-08-04T00:00:00-04:00"
        )

        assert request.await_args.args[0] == "CAL-TOKEN"
        assert request.await_args.args[2].endswith("/calendars/primary/events")
        assert request.await_args.kwargs["params"]["singleEvents"] == "true"


class TestStandardProductToolsets:
    @pytest.mark.parametrize("kind", ["drive", "docs", "sheets", "slides", "chat", "contacts"])
    def test_tool_catalog_is_complete_and_mutations_require_approval(self, kind):
        product = DIRECT_GOOGLE_PRODUCTS[kind]
        toolset = google_api.build_google_api_toolset(
            name=f"google-{kind}",
            url=product.url,
            access_token="AT",
            allowed_tools=None,
            user_id="00000000-0000-0000-0000-000000000001",
        )
        tools = toolset.wrapped.tools
        assert set(tools) == {name for name, _ in product.tools}
        assert any(tool.requires_approval for tool in tools.values())
        for name, tool in tools.items():
            is_mutation = name.startswith(
                (
                    "create_",
                    "update_",
                    "delete_",
                    "upload_",
                    "copy_",
                    "move_",
                    "add_",
                    "append_",
                    "insert_",
                    "replace_",
                    "clear_",
                    "format_",
                    "modify_",
                    "rename_",
                    "duplicate_",
                    "trash_",
                    "restore_",
                )
            )
            assert tool.requires_approval is is_mutation, name

    @pytest.mark.anyio
    @pytest.mark.parametrize(
        ("kind", "tool_name", "args"),
        [
            (
                "docs",
                "append_text",
                {"document_id": "doc", "text": "hello", "required_revision_id": "doc-rev"},
            ),
            (
                "slides",
                "add_slide",
                {"presentation_id": "slides", "required_revision_id": "slides-rev"},
            ),
        ],
    )
    async def test_revision_controls_are_sent_with_mutations(
        self, monkeypatch, kind, tool_name, args
    ):
        request = AsyncMock(return_value={})
        monkeypatch.setattr(
            products,
            "GoogleApiClient",
            lambda access_token: SimpleNamespace(request=request),
        )
        product = DIRECT_GOOGLE_PRODUCTS[kind]
        toolset = google_api.build_google_api_toolset(
            name=kind,
            url=product.url,
            access_token="AT",
            allowed_tools=[tool_name],
        )

        await _function(toolset, tool_name)(**args)

        revision = args["required_revision_id"]
        assert request.await_args.kwargs["json"]["writeControl"] == {"requiredRevisionId": revision}

    def test_people_update_requires_an_etag(self):
        product = DIRECT_GOOGLE_PRODUCTS["contacts"]
        toolset = google_api.build_google_api_toolset(
            name="contacts",
            url=product.url,
            access_token="AT",
            allowed_tools=["update_contact"],
        )
        required = toolset.wrapped.tools["update_contact"].function_schema.json_schema["required"]
        assert "etag" in required
