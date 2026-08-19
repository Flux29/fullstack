"""Tests for AI agent module (PydanticAI)."""

import asyncio
import contextlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from pydantic_ai import FunctionToolCallEvent, FunctionToolResultEvent
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    NativeToolSearchReturnPart,
    RetryPromptPart,
    SystemPromptPart,
    TextPart,
    ToolCallPart,
    ToolSearchCallPart,
    ToolSearchReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.test import TestModel
from pydantic_ai.tools import DeferredToolRequests, ToolApproved, ToolDenied
from pydantic_ai.toolsets._tool_search import parse_discovered_tools

from app.agents.assistant import AssistantAgent, Deps, get_agent
from app.agents.prompts import get_system_prompt_with_rag
from app.agents.utils import get_current_datetime
from app.services.agent import (
    append_discovery_replay,
    build_message_history,
    discovered_tool_names,
    load_conversation_history,
    persist_user_turn,
)
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

    @patch("app.agents.assistant._build_model")
    def test_agent_enables_openrouter_prompt_caching(self, mock_build_model):
        """Every request opts into cache_control for tools, instructions, and messages."""
        mock_build_model.return_value = TestModel()
        agent = AssistantAgent()
        model_settings = agent.agent.model_settings
        assert model_settings is not None
        assert model_settings.get("openrouter_cache_instructions") is True
        assert model_settings.get("openrouter_cache_tool_definitions") is True
        assert model_settings.get("openrouter_cache_messages") is True


class TestGetAgent:
    """Tests for get_agent factory function."""

    def test_returns_assistant_agent(self):
        """Test get_agent returns AssistantAgent."""
        agent = get_agent()
        assert isinstance(agent, AssistantAgent)


class TestModelProvider:
    """LLM_PROVIDER selects the model _build_model returns; `test` is the deterministic fake."""

    def test_openrouter_is_the_default_provider(self, monkeypatch):
        from pydantic_ai.models.openrouter import OpenRouterModel

        from app.agents.assistant import _build_model, settings

        monkeypatch.setattr(settings, "LLM_PROVIDER", "openrouter")
        # A placeholder, not a credential: the provider refuses to construct without one,
        # and this asserts provider selection, not that CI has a key.
        monkeypatch.setattr(settings, "OPENROUTER_API_KEY", "test-not-a-real-key")
        assert isinstance(_build_model("anthropic/claude-sonnet-5"), OpenRouterModel)

    def test_test_provider_builds_the_pydantic_ai_test_model_without_tool_calls(self, monkeypatch):
        from app.agents.assistant import _build_model, settings

        monkeypatch.setattr(settings, "LLM_PROVIDER", "test")
        model = _build_model("anything")
        assert isinstance(model, TestModel)
        assert model.call_tools == []

    def test_test_provider_is_refused_in_production(self):
        from pydantic import ValidationError

        from app.core.config import Settings

        with pytest.raises(ValidationError, match="cannot run in production"):
            Settings(
                ENVIRONMENT="production", LLM_PROVIDER="test", SECRET_KEY="x" * 32, _env_file=None
            )
        assert (
            Settings(ENVIRONMENT="local", LLM_PROVIDER="test", _env_file=None).LLM_PROVIDER
            == "test"
        )

    def test_an_unknown_provider_is_rejected(self):
        from pydantic import ValidationError

        from app.core.config import Settings

        with pytest.raises(ValidationError):
            Settings(LLM_PROVIDER="not-a-provider", _env_file=None)

    @pytest.mark.anyio
    async def test_the_fake_round_trips_a_chat_turn_with_no_network(self, monkeypatch):
        """What the e2e suite relies on: a real AssistantAgent, a real reply, no key, no tools."""
        from app.agents.assistant import settings

        monkeypatch.setattr(settings, "LLM_PROVIDER", "test")
        monkeypatch.setattr(settings, "OPENROUTER_API_KEY", "")
        agent = AssistantAgent()
        output, tool_events, _deps = await agent.run("Hello!")
        assert output
        assert tool_events == []


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

    def test_build_message_history_converts_each_role_and_preserves_order(self):
        history = [
            {"role": "system", "content": "You are helpful"},
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
        ]

        converted = build_message_history(history)

        assert len(converted) == 3
        assert isinstance(converted[0], ModelRequest)
        assert isinstance(converted[0].parts[0], SystemPromptPart)
        assert isinstance(converted[1], ModelRequest)
        assert isinstance(converted[1].parts[0], UserPromptPart)
        assert converted[1].parts[0].content == "Hello"
        assert isinstance(converted[2], ModelResponse)
        assert isinstance(converted[2].parts[0], TextPart)
        assert converted[2].parts[0].content == "Hi there!"

    def test_build_message_history_drops_unknown_roles_rather_than_crashing(self):
        history = [
            {"role": "user", "content": "question"},
            {"role": "tool", "content": "tool output"},
            {"role": "assistant", "content": "answer"},
        ]

        converted = build_message_history(history)

        assert len(converted) == 2
        assert isinstance(converted[0], ModelRequest)
        assert isinstance(converted[1], ModelResponse)


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

    @pytest.mark.anyio
    async def test_loader_excludes_non_model_roles(self):
        user = SimpleNamespace(id=uuid4())
        conversation_id = uuid4()
        service = AsyncMock()
        service.list_messages.side_effect = [
            (
                [
                    SimpleNamespace(role="user", content="question"),
                    SimpleNamespace(role="tool", content="raw tool payload"),
                    SimpleNamespace(role="assistant", content="answer"),
                ],
                3,
            ),
        ]

        @contextlib.asynccontextmanager
        async def db_context():
            yield MagicMock()

        with (
            patch("app.services.agent.get_db_context", db_context),
            patch("app.services.agent.get_conversation_service", return_value=service),
        ):
            history = await load_conversation_history(user, str(conversation_id))

        assert history == [
            {"role": "user", "content": "question"},
            {"role": "assistant", "content": "answer"},
        ]

    @pytest.mark.anyio
    async def test_completed_turn_hands_loaded_history_to_the_model_and_appends_the_new_turn(self):
        """The retention seam itself: history that was loaded must reach the model.

        The incident this pins: history was loaded and stored on the session but the
        model was run without it — nothing crashed, the chat just forgot its past.
        """
        conversation_id = str(uuid4())
        user = SimpleNamespace(id=uuid4())
        session = AgentSession(MagicMock(), user)
        session._stream_agent_run = AsyncMock()
        session._google_loadout_for_turn = MagicMock(return_value=MagicMock())
        loaded = [
            {"role": "user", "content": "earlier prompt"},
            {"role": "assistant", "content": "earlier answer"},
        ]

        captured: dict = {}
        agent_run = SimpleNamespace(
            result=SimpleNamespace(output="new answer", new_messages=lambda: [])
        )

        @contextlib.asynccontextmanager
        async def fake_iter(user_input, **kwargs):
            captured["user_input"] = user_input
            captured.update(kwargs)
            yield agent_run

        assistant = MagicMock()
        assistant.agent.iter = fake_iter
        assistant.model_name = "test-model"

        with (
            patch(
                "app.services.agent_session.load_conversation_history",
                AsyncMock(return_value=list(loaded)),
            ),
            patch(
                "app.services.agent_session.persist_user_turn",
                AsyncMock(return_value=(conversation_id, False, None)),
            ),
            patch(
                "app.services.agent_session.persist_assistant_turn",
                AsyncMock(return_value="msg-id"),
            ),
            patch("app.services.agent_session.send_event", AsyncMock(return_value=True)),
            patch("app.services.agent_session.build_toolsets_for_user", AsyncMock(return_value=[])),
            patch("app.services.agent_session.get_agent", return_value=assistant),
        ):
            await session.process_message(
                {"message": "new prompt", "conversation_id": conversation_id}
            )

        history_arg = captured["message_history"]
        assert [type(message) for message in history_arg] == [ModelRequest, ModelResponse]
        assert history_arg[0].parts[0].content == "earlier prompt"
        assert history_arg[1].parts[0].content == "earlier answer"
        assert session.conversation_history == [
            *loaded,
            {"role": "user", "content": "new prompt"},
            {"role": "assistant", "content": "new answer"},
        ]

    @pytest.mark.anyio
    async def test_persist_without_a_requested_id_always_creates(self):
        user = SimpleNamespace(id=uuid4())
        created = SimpleNamespace(id=uuid4())
        service = AsyncMock()
        service.create_conversation = AsyncMock(return_value=created)
        service.add_message = AsyncMock(return_value=SimpleNamespace(id=uuid4()))

        @contextlib.asynccontextmanager
        async def db_context():
            yield MagicMock()

        with (
            patch("app.services.agent.get_db_context", db_context),
            patch("app.services.agent.get_conversation_service", return_value=service),
        ):
            conv_id, newly_created, _ = await persist_user_turn(
                user, "hello", [], requested_conversation_id=None
            )

        assert newly_created is True
        assert conv_id == str(created.id)
        service.add_message.assert_awaited_once()

    @pytest.mark.anyio
    async def test_persist_with_a_requested_id_continues_that_conversation(self):
        user = SimpleNamespace(id=uuid4())
        requested = str(uuid4())
        service = AsyncMock()
        service.get_conversation = AsyncMock(return_value=SimpleNamespace(title="existing"))
        service.add_message = AsyncMock(return_value=SimpleNamespace(id=uuid4()))

        @contextlib.asynccontextmanager
        async def db_context():
            yield MagicMock()

        with (
            patch("app.services.agent.get_db_context", db_context),
            patch("app.services.agent.get_conversation_service", return_value=service),
        ):
            conv_id, newly_created, _ = await persist_user_turn(
                user, "follow-up", [], requested_conversation_id=requested
            )

        assert newly_created is False
        assert conv_id == requested
        service.create_conversation.assert_not_awaited()

    @pytest.mark.anyio
    async def test_new_chat_over_a_live_session_creates_a_fresh_conversation(self):
        """The new-chat incident: conversation_id null means NEW CHAT, even mid-session.

        The frontend's startNewChat keeps the WebSocket open, sends the next message with
        conversation_id null, and waits for conversation_created to learn the new id. A
        session that falls back to its sticky current_conversation_id instead appends the
        message to the OLD conversation and hands the model the OLD history — chats that
        never end, which is exactly what shipped inside the d11400c snapshot.
        """
        user = SimpleNamespace(id=uuid4())
        session = AgentSession(MagicMock(), user)
        session.current_conversation_id = str(uuid4())  # a previous chat on this socket
        session._stream_agent_run = AsyncMock()
        session._google_loadout_for_turn = MagicMock(return_value=MagicMock())

        new_conversation = SimpleNamespace(id=uuid4())
        conv_service = AsyncMock()
        conv_service.create_conversation = AsyncMock(return_value=new_conversation)
        conv_service.add_message = AsyncMock(return_value=SimpleNamespace(id=uuid4()))

        @contextlib.asynccontextmanager
        async def db_context():
            yield MagicMock()

        captured: dict = {}
        agent_run = SimpleNamespace(
            result=SimpleNamespace(output="answer", new_messages=lambda: [])
        )

        @contextlib.asynccontextmanager
        async def fake_iter(user_input, **kwargs):
            captured.update(kwargs)
            yield agent_run

        assistant = MagicMock()
        assistant.agent.iter = fake_iter
        assistant.model_name = "test-model"
        load_history = AsyncMock(return_value=[{"role": "user", "content": "old chat"}])
        send_event = AsyncMock(return_value=True)

        with (
            patch("app.services.agent.get_db_context", db_context),
            patch("app.services.agent.get_conversation_service", return_value=conv_service),
            patch("app.services.agent_session.load_conversation_history", load_history),
            patch("app.services.agent_session.persist_assistant_turn", AsyncMock(return_value="m")),
            patch("app.services.agent_session.send_event", send_event),
            patch("app.services.agent_session.build_toolsets_for_user", AsyncMock(return_value=[])),
            patch("app.services.agent_session.get_agent", return_value=assistant),
        ):
            await session.process_message({"message": "fresh start", "conversation_id": None})

        conv_service.create_conversation.assert_awaited_once()
        assert session.current_conversation_id == str(new_conversation.id)
        load_history.assert_not_awaited()  # the old chat's history must not leak in
        assert captured["message_history"] == []
        event_names = [call.args[1] for call in send_event.await_args_list]
        assert "conversation_created" in event_names


class TestDiscoveryReplay:
    """The replay helpers that make MCP tool discovery survive the Postgres rebuild."""

    def test_appends_the_exchange_pydantic_ai_parses_back(self):
        """The contract seam: pydantic-ai derives discovered tools from the history
        it is handed, so the replay is only real if its own parser reads it back."""
        history: list = []
        append_discovery_replay(history, ("github_get_me", "logfire_query_run"))
        assert [type(message) for message in history] == [ModelResponse, ModelRequest]
        assert isinstance(history[0].parts[0], ToolSearchCallPart)
        assert isinstance(history[1].parts[0], ToolSearchReturnPart)
        assert history[0].parts[0].tool_call_id == history[1].parts[0].tool_call_id
        assert parse_discovered_tools(history) == {"github_get_me", "logfire_query_run"}

    def test_same_names_replay_byte_identically(self):
        """The exchange participates in the provider prompt-cache prefix — an
        unchanged discovery set must serialize to the same bytes every turn."""
        first: list = []
        second: list = []
        append_discovery_replay(first, ("github_get_me",))
        append_discovery_replay(second, ("github_get_me",))
        assert first[0].parts[0].tool_call_id == second[0].parts[0].tool_call_id
        assert first[0].parts[0].args == second[0].parts[0].args
        assert first[1].parts[0].content == second[1].parts[0].content

    def test_no_names_appends_nothing(self):
        history: list = []
        append_discovery_replay(history, ())
        assert history == []

    def test_discovered_tool_names_reads_both_part_shapes(self):
        """Local search_tools returns and provider-native results both count."""
        messages = [
            ModelRequest(
                parts=[
                    ToolSearchReturnPart(
                        content={"discovered_tools": [{"name": "local_tool"}]},
                        tool_call_id="c1",
                    )
                ]
            ),
            ModelResponse(
                parts=[
                    NativeToolSearchReturnPart(
                        content={"discovered_tools": [{"name": "native_tool"}]},
                        tool_call_id="c2",
                    )
                ]
            ),
            ModelRequest(parts=[UserPromptPart(content="unrelated")]),
        ]
        assert discovered_tool_names(messages) == {"local_tool", "native_tool"}


class TestDiscoveryPersistsAcrossTurns:
    """Turn two must reveal turn one's MCP tools without a search round-trip."""

    @pytest.mark.anyio
    async def test_second_turn_replays_and_a_dead_server_drops_out(self):
        """Turn 1 discovers github_get_me via search_tools; the persisted history is
        plain text, so turn 2 only knows it through the session's discovery log. The
        replayed exchange must sit after the rebuilt history (cache prefix stays
        stable) and vanish the moment the server stops being attached."""
        conversation_id = str(uuid4())
        user = SimpleNamespace(id=uuid4())
        session = AgentSession(MagicMock(), user)
        session._stream_agent_run = AsyncMock()
        session._google_loadout_for_turn = MagicMock(return_value=MagicMock())

        attached = {"github_get_me", "github_search_issues"}

        async def fake_build_toolsets(user_id, loadout=None, discoveries=None):
            discoveries.attach(attached)
            return []

        discovery_exchange = [
            ModelResponse(
                parts=[ToolSearchCallPart(args={"queries": ["github user"]}, tool_call_id="c1")]
            ),
            ModelRequest(
                parts=[
                    ToolSearchReturnPart(
                        content={"discovered_tools": [{"name": "github_get_me"}]},
                        tool_call_id="c1",
                    )
                ]
            ),
        ]
        results = iter(
            [
                SimpleNamespace(output="found you", new_messages=lambda: discovery_exchange),
                SimpleNamespace(output="again", new_messages=lambda: []),
                SimpleNamespace(output="gone", new_messages=lambda: []),
            ]
        )
        histories: list = []

        @contextlib.asynccontextmanager
        async def fake_iter(user_input, **kwargs):
            histories.append(kwargs["message_history"])
            yield SimpleNamespace(result=next(results))

        assistant = MagicMock()
        assistant.agent.iter = fake_iter
        assistant.model_name = "test-model"
        loaded = [
            {"role": "user", "content": "who am I on github?"},
            {"role": "assistant", "content": "found you"},
        ]

        with (
            patch(
                "app.services.agent_session.load_conversation_history",
                AsyncMock(side_effect=lambda *args, **kwargs: list(loaded)),
            ),
            patch(
                "app.services.agent_session.persist_user_turn",
                AsyncMock(return_value=(conversation_id, False, None)),
            ),
            patch(
                "app.services.agent_session.persist_assistant_turn",
                AsyncMock(return_value="m"),
            ),
            patch("app.services.agent_session.send_event", AsyncMock(return_value=True)),
            patch("app.services.agent_session.build_toolsets_for_user", fake_build_toolsets),
            patch("app.services.agent_session.get_agent", return_value=assistant),
        ):
            await session.process_message(
                {"message": "who am I on github?", "conversation_id": conversation_id}
            )
            await session.process_message(
                {"message": "and my open issues?", "conversation_id": conversation_id}
            )
            # The server disappears: nothing attached, nothing may replay.
            attached.clear()
            await session.process_message(
                {"message": "one more thing", "conversation_id": conversation_id}
            )

        # Turn 1: nothing discovered yet, so the model saw plain history only.
        assert parse_discovered_tools(histories[0]) == set()
        # Turn 2: the discovery is replayed after the rebuilt plain-text history,
        # and pydantic-ai's own parser reads it back.
        second = histories[1]
        assert parse_discovered_tools(second) == {"github_get_me"}
        assert isinstance(second[-2].parts[0], ToolSearchCallPart)
        assert isinstance(second[-1].parts[0], ToolSearchReturnPart)
        assert [type(message) for message in second[:-2]] == [ModelRequest, ModelResponse]
        # Turn 3: the server is gone — its tools must not leak into the history.
        assert parse_discovered_tools(histories[2]) == set()
