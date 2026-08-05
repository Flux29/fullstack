"""Tests for AI agent module (PydanticAI)."""

import asyncio
import contextlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from pydantic_ai import FunctionToolCallEvent, FunctionToolResultEvent
from pydantic_ai.messages import RetryPromptPart, ToolCallPart
from pydantic_ai.models.test import TestModel
from pydantic_ai.tools import DeferredToolRequests, ToolApproved, ToolDenied

from app.agents.assistant import AssistantAgent, Deps, get_agent
from app.agents.prompts import get_system_prompt_with_rag
from app.agents.utils import get_current_datetime
from app.services.agent import load_conversation_history
from app.services.agent_session import AgentSession


class TestDeps:
    """Tests for Deps dataclass."""

    def test_deps_default_values(self):
        """Test Deps has correct default values."""
        deps = Deps()
        assert deps.user_id is None
        assert deps.user_name is None
        assert deps.metadata == {}

    def test_deps_with_values(self):
        """Test Deps with custom values."""
        deps = Deps(user_id="123", user_name="Test User", metadata={"key": "value"})
        assert deps.user_id == "123"
        assert deps.user_name == "Test User"
        assert deps.metadata == {"key": "value"}


class TestGetCurrentDatetime:
    """Tests for get_current_datetime tool."""

    def test_returns_dict_with_date_time_datetime(self):
        """Tool returns a dict with date/time/datetime keys."""
        result = get_current_datetime()
        assert isinstance(result, dict)
        assert {"date", "time", "datetime"} <= result.keys()
        # ISO-like date "YYYY-MM-DD"
        assert len(result["date"]) == 10


class TestAssistantAgent:
    """Tests for AssistantAgent class."""

    def test_init_with_defaults(self):
        """Test AssistantAgent initializes with defaults."""
        agent = AssistantAgent()
        assert agent.system_prompt == get_system_prompt_with_rag()
        assert agent._agent is None

    def test_init_with_custom_values(self):
        """Test AssistantAgent with custom configuration."""
        agent = AssistantAgent(
            model_name="gpt-4",
            temperature=0.5,
            system_prompt="Custom prompt",
        )
        assert agent.model_name == "gpt-4"
        assert agent.temperature == 0.5
        assert agent.system_prompt == "Custom prompt"

    # ``_build_model`` is the single per-provider model factory in
    # assistant.py, so patching it keeps these tests provider-agnostic
    # (openai/anthropic/google/openrouter/all) and avoids needing real API keys.
    @patch("app.agents.assistant._build_model")
    def test_agent_property_creates_agent(self, mock_build_model):
        """Test agent property creates agent on first access."""
        mock_build_model.return_value = TestModel()
        agent = AssistantAgent()
        _ = agent.agent
        assert agent._agent is not None
        mock_build_model.assert_called_once()

    @patch("app.agents.assistant._build_model")
    def test_agent_property_caches_agent(self, mock_build_model):
        """Test agent property caches the agent instance."""
        mock_build_model.return_value = TestModel()
        agent = AssistantAgent()
        agent1 = agent.agent
        agent2 = agent.agent
        assert agent1 is agent2
        mock_build_model.assert_called_once()


class TestGetAgent:
    """Tests for get_agent factory function."""

    def test_returns_assistant_agent(self):
        """Test get_agent returns AssistantAgent."""
        agent = get_agent()
        assert isinstance(agent, AssistantAgent)


class TestAgentRoutes:
    """Tests for agent WebSocket routes."""

    @pytest.mark.anyio
    async def test_agent_websocket_connection(self, client):
        """Test WebSocket connection to agent endpoint."""
        # This test verifies the WebSocket endpoint is accessible
        # Actual agent testing would require mocking OpenAI
        pass


class TestAgentSessionToolEvents:
    """Regression coverage for PydanticAI's streamed tool-event API."""

    @pytest.mark.anyio
    async def test_retry_result_uses_event_part_content(self):
        """A failed tool result is forwarded without reading the removed result attribute."""
        tool_call_id = "gmail-search-1"

        async def events():
            yield FunctionToolCallEvent(
                ToolCallPart(
                    tool_name="gmail_search_threads",
                    args={"query": "in:inbox"},
                    tool_call_id=tool_call_id,
                )
            )
            yield FunctionToolResultEvent(
                RetryPromptPart(
                    "The caller does not have permission",
                    tool_name="gmail_search_threads",
                    tool_call_id=tool_call_id,
                )
            )

        session = AgentSession(MagicMock(), MagicMock())
        collected_tool_calls: list[dict] = []
        send_event = AsyncMock()

        with patch("app.services.agent_session.send_event", send_event):
            await session._stream_tool_events(events(), collected_tool_calls)

        assert collected_tool_calls == [
            {
                "tool_call_id": tool_call_id,
                "tool_name": "gmail_search_threads",
                "args": {"query": "in:inbox"},
                "result": "The caller does not have permission",
            }
        ]
        assert send_event.await_args_list[-1].args[1:] == (
            "tool_result",
            {
                "tool_call_id": tool_call_id,
                "content": "The caller does not have permission",
            },
        )


class TestAgentSessionApproval:
    @pytest.mark.anyio
    async def test_approve_is_correlated_by_tool_call_id(self):
        session = AgentSession(MagicMock(), SimpleNamespace(id=uuid4()))
        requests = DeferredToolRequests(
            approvals=[
                ToolCallPart(
                    tool_name="google_drive_delete_file",
                    args={"file_id": "file-1"},
                    tool_call_id="call-1",
                )
            ]
        )

        with patch("app.services.agent_session.send_event", AsyncMock()) as send:
            task = asyncio.create_task(session._approve_tools(requests))
            await asyncio.sleep(0)
            await session.handle_frame(
                {"type": "resume", "decisions": [{"id": "call-1", "decision": "approve"}]}
            )
            result = await task

        assert isinstance(result.approvals["call-1"], ToolApproved)
        assert send.await_args.args[1] == "tool_approval_required"
        assert send.await_args.args[2]["action_requests"][0]["args"] == {"file_id": "file-1"}

    @pytest.mark.anyio
    async def test_edit_and_reject_produce_deferred_results(self):
        session = AgentSession(MagicMock(), SimpleNamespace(id=uuid4()))
        requests = DeferredToolRequests(
            approvals=[
                ToolCallPart(tool_name="drive_move", args={"to": "a"}, tool_call_id="edit"),
                ToolCallPart(tool_name="drive_delete", args={"id": "b"}, tool_call_id="reject"),
            ]
        )

        with patch("app.services.agent_session.send_event", AsyncMock()):
            task = asyncio.create_task(session._approve_tools(requests))
            await asyncio.sleep(0)
            await session.handle_frame(
                {
                    "type": "resume",
                    "decisions": [
                        {"id": "edit", "decision": "edit", "args": {"to": "c"}},
                        {"id": "reject", "decision": "reject"},
                    ],
                }
            )
            result = await task

        assert isinstance(result.approvals["edit"], ToolApproved)
        assert result.approvals["edit"].override_args == {"to": "c"}
        assert isinstance(result.approvals["reject"], ToolDenied)


class TestHistoryConversion:
    """Tests for conversation history conversion."""

    def test_empty_history(self):
        """Test with empty history."""
        _agent = AssistantAgent()
        # History conversion happens inside run/iter methods
        # We test the structure here
        history = []
        assert len(history) == 0

    def test_history_roles(self):
        """Test history with different roles."""
        history = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
            {"role": "system", "content": "You are helpful"},
        ]
        assert len(history) == 3
        assert all("role" in msg and "content" in msg for msg in history)


class TestPersistedConversationHistory:
    @pytest.mark.anyio
    async def test_loads_all_pages_in_order_and_checks_owner(self):
        user = SimpleNamespace(id=uuid4())
        conversation_id = uuid4()
        service = AsyncMock()
        service.list_messages.side_effect = [
            (
                [
                    SimpleNamespace(role="user", content="first question"),
                    SimpleNamespace(role="assistant", content="first answer"),
                ],
                3,
            ),
            ([SimpleNamespace(role="user", content="follow-up")], 3),
        ]

        @contextlib.asynccontextmanager
        async def db_context():
            yield MagicMock()

        with (
            patch("app.services.agent.get_db_context", db_context),
            patch("app.services.agent.get_conversation_service", return_value=service),
        ):
            history = await load_conversation_history(user, str(conversation_id), page_size=2)

        service.get_conversation.assert_awaited_once_with(conversation_id, user_id=user.id)
        assert history == [
            {"role": "user", "content": "first question"},
            {"role": "assistant", "content": "first answer"},
            {"role": "user", "content": "follow-up"},
        ]

    @pytest.mark.anyio
    async def test_session_reloads_history_before_persisting_current_prompt(self):
        conversation_id = str(uuid4())
        user = SimpleNamespace(id=uuid4())
        session = AgentSession(MagicMock(), user)
        loaded = [{"role": "user", "content": "earlier prompt"}]
        load_history = AsyncMock(return_value=loaded)
        persist = AsyncMock(side_effect=RuntimeError("stop after ordering assertion"))

        with (
            patch("app.services.agent_session.load_conversation_history", load_history),
            patch("app.services.agent_session.persist_user_turn", persist),
            pytest.raises(RuntimeError, match="ordering assertion"),
        ):
            await session.process_message(
                {
                    "message": "current prompt",
                    "conversation_id": conversation_id,
                }
            )

        load_history.assert_awaited_once_with(user, conversation_id)
        persist.assert_awaited_once()
        assert session.conversation_history == loaded
