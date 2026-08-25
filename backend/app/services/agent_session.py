# Thin session wrapper — the route is lifecycle plumbing only; orchestration lives here.
import asyncio
import contextlib
import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from fastapi import WebSocket, WebSocketDisconnect
from pydantic_ai import (
    Agent,
    FinalResultEvent,
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    PartDeltaEvent,
    PartStartEvent,
    TextPartDelta,
    ToolCallPartDelta,
)
from pydantic_ai.messages import (
    BinaryContent,
    TextPart,
    ThinkingPart,
    ThinkingPartDelta,
    ToolCallPart,
)
from pydantic_ai.tools import (
    DeferredToolRequests,
    DeferredToolResults,
    ToolApproved,
    ToolDenied,
)

from app.agents.assistant import Deps, get_agent
from app.agents.google_loadout import GoogleLoadout
from app.agents.mcp import McpDiscoveries
from app.agents.tools.coding import CodingToolkit, WorkspacePolicy, read_repository_briefing
from app.api.deps import get_conversation_service
from app.core.config import settings
from app.core.exceptions import AppException, NotFoundError
from app.db.models.user import User
from app.db.session import get_db_context
from app.services.agent import (
    append_discovery_replay,
    build_message_history,
    discovered_tool_names,
    load_conversation_history,
    persist_assistant_turn,
    persist_user_turn,
    send_event,
)
from app.services.file_storage import get_file_storage
from app.services.mcp_connection import build_toolsets_for_user
from app.services.research import RESEARCH_TOOL_NAMES, ResearchToolkit
from app.services.workspace import WorkspaceService

logger = logging.getLogger(__name__)

#: Conversations whose sticky per-chat state (Google loadout, MCP discoveries)
#: one socket keeps.
_MAX_TRACKED_CONVERSATIONS = 16


class AgentSession:
    """One WebSocket session with the AI agent."""

    def __init__(
        self,
        websocket: WebSocket,
        user: User,
    ) -> None:
        self.websocket = websocket
        self.user = user
        self.conversation_history: list[dict[str, str]] = []
        self.deps = Deps(
            user_id=str(user.id),
            user_name=getattr(user, "full_name", None) or getattr(user, "email", None),
        )
        self.deps.ask_user = self._ask_user
        self.deps.approve_tools = self._approve_tools
        self.current_conversation_id: str | None = None
        self._turn_task: asyncio.Task[None] | None = None
        self._ask_user_future: asyncio.Future[list[dict[str, Any]]] | None = None
        self._approval_future: asyncio.Future[list[dict[str, Any]]] | None = None
        self._research: ResearchToolkit | None = None
        self._coding: CodingToolkit | None = None
        # The target repository's own conventions, read out of the sandbox at
        # attach time and appended to this turn's instructions (ADR-006 §5).
        self._coding_briefing: str = ""
        # Tool names this turn's workspace resolves without asking the browser
        # (ADR-006 §4). Empty unless a workspace opted in.
        self._auto_approved_tools: frozenset[str] = frozenset()
        self._subagent_task_manager: Any | None = None
        # Google loadouts are conversation-scoped, not run-scoped: a follow-up
        # turn inherits the products the previous turns actually used, so "send
        # that to Nikki" still finds Gmail. Keyed by conversation because one
        # socket can switch between chats, and the loadout must switch with it.
        self._google_loadouts: dict[str, GoogleLoadout] = {}
        # MCP tool discoveries share the loadout's lifecycle: conversation-
        # scoped, sticky across turns, evicted LRU when the socket visits too
        # many chats.
        self._mcp_discoveries: dict[str, McpDiscoveries] = {}

    async def handle_frame(self, data: dict[str, Any]) -> None:
        """Dispatch one incoming WebSocket frame.

        A ``stop`` cancels the running turn; an ``ask_user_response`` unblocks a
        paused run; any other control frame is ignored; a bare message starts a
        new turn as a cancellable background task.
        """
        msg_type = data.get("type")

        if msg_type == "stop":
            await self._cancel_turn()
            return

        if msg_type == "ask_user_response":
            fut = self._ask_user_future
            if fut is not None and not fut.done():
                answers = data.get("answers")
                fut.set_result(answers if isinstance(answers, list) else [])
            return

        if msg_type == "resume":
            fut = self._approval_future
            if fut is not None and not fut.done():
                decisions = data.get("decisions")
                fut.set_result(decisions if isinstance(decisions, list) else [])
            return

        if msg_type is not None:
            return

        if self._turn_task is not None and not self._turn_task.done():
            logger.warning("Ignoring message received while a turn is already in progress")
            return
        task = asyncio.create_task(self._run_turn(data))
        self._turn_task = task
        task.add_done_callback(self._on_turn_done)

    def _on_turn_done(self, task: asyncio.Task[None]) -> None:
        """Clear the turn slot and surface unexpected crashes."""
        if self._turn_task is task:
            self._turn_task = None
        if not task.cancelled():
            exc = task.exception()
            if isinstance(exc, WebSocketDisconnect):
                logger.info("Client disconnected during agent turn")
            elif exc is not None:
                logger.error("Agent turn task crashed", exc_info=exc)

    async def _run_turn(self, data: dict[str, Any]) -> None:
        """Run one turn, emitting a terminal ``complete`` even when stopped."""
        try:
            await self.process_message(data)
        except asyncio.CancelledError:
            await send_event(
                self.websocket,
                "complete",
                {
                    "conversation_id": self.current_conversation_id,
                    "stopped": True,
                },
            )
            raise

    async def _cancel_turn(self) -> None:
        """Cancel the in-flight turn task and wait for it to unwind."""
        task = self._turn_task
        if task is None or task.done():
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    async def shutdown(self) -> None:
        """Cancel any in-flight turn."""
        await self._cancel_turn()

    async def process_message(self, data: dict[str, Any]) -> None:
        """Process one user turn: persist input, run the agent, stream events, persist output."""
        user_message = data.get("message", "")
        file_ids = data.get("file_ids", [])

        if not user_message and not file_ids:
            await send_event(self.websocket, "error", {"message": "Empty message"})
            return
        # A WebSocket is only transport state; PostgreSQL is the conversation
        # source of truth. Reload before every resumed turn so reconnecting,
        # opening the same chat in another tab, or switching conversations does
        # not silently give the model an empty/stale history. This happens
        # before persist_user_turn so the current prompt is not duplicated.
        # A null conversation_id is the frontend's "new chat" signal over this
        # same socket — never fall back to the session's previous conversation,
        # or the new chat inherits the old history and the old conversation row.
        history_conversation_id = data.get("conversation_id")
        if history_conversation_id:
            self.conversation_history = await load_conversation_history(
                self.user, history_conversation_id
            )
        else:
            self.conversation_history = []
        self.current_conversation_id, newly_created, organization_id = await persist_user_turn(
            self.user,
            user_message,
            file_ids,
            requested_conversation_id=data.get("conversation_id"),
        )
        if newly_created and self.current_conversation_id:
            await send_event(
                self.websocket,
                "conversation_created",
                {"conversation_id": self.current_conversation_id},
            )

        await send_event(self.websocket, "user_prompt", {"content": user_message})

        try:
            deep_research = settings.ENABLE_DEEP_RESEARCH and bool(data.get("deep_research", False))
            self._research = None
            todo_cap = None
            subagent_cap = None
            ctx_manager_cap = None
            if deep_research and self.current_conversation_id:
                self._research = ResearchToolkit(self._send, model_name=data.get("model"))
                caps = await self._research.build(self.current_conversation_id)
                todo_cap = caps.todo
                subagent_cap = caps.subagents
                ctx_manager_cap = caps.context_manager
            else:
                deep_research = False

            coding_toolsets = await self._build_coding_toolsets(data.get("workspace_id"))

            # Route this turn onto Google products before toolsets are built —
            # build_toolsets_for_user gates each integration against the result.
            loadout = self._google_loadout_for_turn(user_message)
            self.deps.google_loadout = loadout
            discoveries = self._mcp_discoveries_for_turn()
            # Rebuilt every turn so Settings → Integrations changes apply
            # immediately; unreachable/unauthorized servers are skipped there.
            mcp_toolsets = await build_toolsets_for_user(
                self.user.id, loadout=loadout, discoveries=discoveries
            )
            assistant = get_agent(
                model_name=data.get("model"),
                thinking_effort=data.get("thinking_effort"),
                extra_instructions=self._coding_briefing or None,
                extra_toolsets=mcp_toolsets + coding_toolsets,
                deep_research=deep_research,
                todo_capability=todo_cap,
                subagent_capability=subagent_cap,
                context_manager_capability=ctx_manager_cap,
            )
            model_history = build_message_history(self.conversation_history)
            # After the rebuilt history, before the new prompt: deferred MCP
            # tools this conversation already discovered come back without a
            # search_tools round-trip, and the cached prefix above is untouched.
            append_discovery_replay(model_history, discoveries.replay_names())
            user_input = await self._build_multimodal_input(user_message, file_ids)

            collected_tool_calls: list[dict[str, Any]] = []
            collected_thinking: list[str] = []
            self._subagent_task_manager = (
                self._research.subagent_capability.task_manager
                if self._research and self._research.subagent_capability
                else None
            )
            if self._subagent_task_manager is not None:
                self._subagent_task_manager.message_bus.add_handler(self._on_subagent_message)
            poller = (
                asyncio.create_task(self._poll_subagent_status())
                if self._research is not None
                else None
            )
            try:
                async with assistant.agent.iter(
                    user_input, deps=self.deps, message_history=model_history
                ) as agent_run:
                    await self._stream_agent_run(
                        agent_run, user_message, collected_tool_calls, collected_thinking
                    )
            finally:
                if poller is not None:
                    poller.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await poller
                if self._subagent_task_manager is not None:
                    self._subagent_task_manager.message_bus.remove_handler(
                        self._on_subagent_message
                    )
                    self._subagent_task_manager = None
                if self._research is not None:
                    await self._research.flush()
                if self._coding is not None:
                    await self._coding.aclose()
                    self._coding = None
                self._coding_briefing = ""
                self._auto_approved_tools = frozenset()
                # Fold what was actually called into the sticky loadout, even
                # when the turn was stopped — a product the user reached for
                # before cancelling is still the right one to inherit.
                loadout.end_turn([call["tool_name"] for call in collected_tool_calls])
                # Same rule for MCP discoveries: a tool called before the stop
                # is still worth replaying next turn.
                discoveries.record(call["tool_name"] for call in collected_tool_calls)

            # Update in-memory history only after a complete agent run
            if agent_run.result is not None:
                # Tools discovered this run (not just called) stay revealed on
                # the next turn too.
                discoveries.record(discovered_tool_names(agent_run.result.new_messages()))
                self.conversation_history.append({"role": "user", "content": user_message})
                self.conversation_history.append(
                    {"role": "assistant", "content": agent_run.result.output}
                )
            assistant_msg_id: str | None = None
            if self.current_conversation_id and agent_run.result is not None:
                assistant_msg_id = await persist_assistant_turn(
                    self.current_conversation_id,
                    agent_run.result.output,
                    getattr(assistant, "model_name", None),
                    collected_tool_calls,
                    thinking="".join(collected_thinking) or None,
                )

            if assistant_msg_id:
                await send_event(
                    self.websocket,
                    "message_saved",
                    {
                        "message_id": assistant_msg_id,
                        "conversation_id": self.current_conversation_id,
                    },
                )

            await send_event(
                self.websocket,
                "complete",
                {"conversation_id": self.current_conversation_id},
            )
        except WebSocketDisconnect:
            raise
        except Exception as e:
            logger.exception("Error processing agent request")
            await send_event(self.websocket, "error", {"message": str(e)})

    def _google_loadout_for_turn(self, user_message: str) -> GoogleLoadout:
        """The loadout for this conversation, routed against this turn's prompt.

        Sticky state belongs to the conversation, so switching chats on the same
        socket switches loadouts rather than carrying one chat's products into
        another. The map is capped because a long-lived socket can visit many
        conversations, and an unbounded cache of routing state is a slow leak;
        eviction is least-recently-used, so the chat being actively worked in is
        never the one dropped.

        A turn with no conversation id yet gets a throwaway loadout rather than
        sharing one bucket, which would let one unsaved chat inherit another's
        products.
        """
        loadout = GoogleLoadout()
        key = self.current_conversation_id
        if key is not None:
            loadout = self._google_loadouts.pop(key, None) or loadout
            if len(self._google_loadouts) >= _MAX_TRACKED_CONVERSATIONS:
                self._google_loadouts.pop(next(iter(self._google_loadouts)))
            self._google_loadouts[key] = loadout  # re-inserted last = most recent
        loadout.begin_turn(user_message)
        return loadout

    def _mcp_discoveries_for_turn(self) -> McpDiscoveries:
        """The MCP discovery log for this conversation.

        Same lifecycle as the Google loadout above: keyed by conversation so a
        socket switching chats switches logs, LRU-capped against the slow leak
        of a long-lived socket, and a throwaway instance when the turn has no
        conversation id yet.
        """
        discoveries = McpDiscoveries()
        key = self.current_conversation_id
        if key is not None:
            discoveries = self._mcp_discoveries.pop(key, None) or discoveries
            if len(self._mcp_discoveries) >= _MAX_TRACKED_CONVERSATIONS:
                self._mcp_discoveries.pop(next(iter(self._mcp_discoveries)))
            self._mcp_discoveries[key] = discoveries  # re-inserted last = most recent
        discoveries.begin_turn()
        return discoveries

    async def _ask_user(self, questions: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Pause the run: ask the client questions and block until they answer.

        Emits an ``ask_user`` event with the whole batch, then awaits a future the
        frame dispatcher completes when the matching ``ask_user_response`` arrives.
        The client returns a list of answers parallel to the questions.
        """
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[list[dict[str, Any]]] = loop.create_future()
        self._ask_user_future = fut
        try:
            await send_event(self.websocket, "ask_user", {"questions": questions})
            return await fut
        finally:
            self._ask_user_future = None

    async def _build_coding_toolsets(self, workspace_id: Any) -> list[Any]:
        """Attach this turn's workspace tools, or nothing (ADR-006).

        Silent no-op when coding is disabled or the turn named no workspace.
        A workspace the caller does not own resolves to nothing rather than an
        error: the service raises NotFoundError, and a chat turn should not
        become a probe for other users' workspace ids.
        """
        if not settings.ENABLE_CODING or not workspace_id:
            return []
        try:
            parsed_id = UUID(str(workspace_id))
        except (TypeError, ValueError):
            logger.warning("Ignoring malformed workspace_id on a chat turn")
            return []
        try:
            async with get_db_context() as db:
                workspace = await WorkspaceService(db).get_for_user(
                    user_id=self.user.id, workspace_id=parsed_id
                )
                # Copy what the toolkit needs while the session is still open.
                policy = WorkspacePolicy.from_row(workspace)
            toolkit = CodingToolkit(policy)
            tools = toolkit.build()
        except NotFoundError:
            logger.info("Chat turn named a workspace the user does not own; no tools attached")
            return []
        except AppException as exc:
            await send_event(self.websocket, "error", {"message": exc.message})
            return []
        self._coding = toolkit
        self._auto_approved_tools = tools.auto_approved_tools
        self._coding_briefing = await read_repository_briefing(
            tools.backend, repo_url=policy.repo_url
        )
        logger.info(
            "Coding workspace %r attached (ruleset=%s, auto_approve=%s)",
            policy.name,
            policy.ruleset,
            bool(tools.auto_approved_tools),
        )
        return [tools.toolset]

    async def _approve_tools(self, requests: DeferredToolRequests) -> DeferredToolResults:
        """Pause one run until the browser approves, edits, or rejects mutations."""
        # ADR-006 §4: a workspace may resolve approval for its own coding tools
        # without a round trip. It never covers anything else — a Google or MCP
        # mutation in the same batch still reaches the browser.
        auto_calls = [c for c in requests.approvals if c.tool_name in self._auto_approved_tools]
        pending = [c for c in requests.approvals if c.tool_name not in self._auto_approved_tools]
        auto_approved: dict[str, bool | ToolApproved | ToolDenied] = {
            call.tool_call_id: ToolApproved() for call in auto_calls
        }
        if auto_calls:
            # The only mutations with no human in the loop — say so in the
            # transcript and in the log, so they are auditable after the fact.
            logger.info(
                "Auto-approved coding tools for this workspace: %s",
                ", ".join(sorted({call.tool_name for call in auto_calls})),
            )
            await send_event(
                self.websocket,
                "tool_auto_approved",
                {
                    "tool_calls": [
                        {"id": call.tool_call_id, "tool_name": call.tool_name}
                        for call in auto_calls
                    ]
                },
            )
        action_requests = [
            {
                "id": call.tool_call_id,
                "tool_name": call.tool_name,
                "args": call.args_as_dict(raise_if_invalid=False),
                "metadata": requests.metadata.get(call.tool_call_id, {}),
            }
            for call in pending
        ]
        if not action_requests:
            return requests.build_results(
                approvals=auto_approved,
                calls={
                    call.tool_call_id: "External deferred tool execution is not supported."
                    for call in requests.calls
                },
            )

        loop = asyncio.get_running_loop()
        fut: asyncio.Future[list[dict[str, Any]]] = loop.create_future()
        self._approval_future = fut
        try:
            await send_event(
                self.websocket,
                "tool_approval_required",
                {
                    "action_requests": action_requests,
                    "review_configs": [
                        {"tool_name": action["tool_name"], "allow_edit": True}
                        for action in action_requests
                    ],
                },
            )
            decisions = await fut
        finally:
            self._approval_future = None

        by_id: dict[str, dict[str, Any]] = {}
        legacy: list[dict[str, Any]] = []
        for decision in decisions:
            if not isinstance(decision, dict):
                continue
            edited = decision.get("edited_action")
            decision_id = decision.get("id")
            if not decision_id and isinstance(edited, dict):
                decision_id = edited.get("id")
            if isinstance(decision_id, str):
                by_id[decision_id] = decision
            else:
                legacy.append(decision)

        approvals: dict[str, bool | ToolApproved | ToolDenied] = dict(auto_approved)
        for index, call in enumerate(pending):
            decision = by_id.get(call.tool_call_id)
            if decision is None and index < len(legacy):
                decision = legacy[index]
            if decision is None:
                approvals[call.tool_call_id] = ToolDenied("No approval decision was received.")
                continue

            kind = decision.get("decision") or decision.get("type")
            if kind == "approve":
                approvals[call.tool_call_id] = ToolApproved()
            elif kind == "edit":
                edited = decision.get("edited_action")
                override_args = decision.get("args")
                if override_args is None and isinstance(edited, dict):
                    override_args = edited.get("args")
                if isinstance(override_args, dict):
                    approvals[call.tool_call_id] = ToolApproved(override_args=override_args)
                else:
                    approvals[call.tool_call_id] = ToolDenied("Edited arguments were invalid.")
            else:
                approvals[call.tool_call_id] = ToolDenied("The user rejected this action.")

        return requests.build_results(
            approvals=approvals,
            calls={
                call.tool_call_id: "External deferred tool execution is not supported."
                for call in requests.calls
            },
        )

    async def _send(self, event_type: str, data: Any) -> bool:
        """Emit a WebSocket event on this session's socket (bound for callbacks)."""
        return await send_event(self.websocket, event_type, data)

    async def _poll_subagent_status(self) -> None:
        """Emit ``subagent_status`` frames for changing async subagent tasks.

        Polls the subagent capability's task manager ~1/s and forwards a frame
        whenever a task's status changes (or is first seen). Cancelled in the
        run's ``finally``.
        """
        seen: dict[str, str] = {}
        cap = self._research.subagent_capability if self._research else None
        if cap is None:
            return
        try:
            while True:
                task_manager = cap.task_manager
                if task_manager is not None:
                    for handle in task_manager.list_handles():
                        status = getattr(handle.status, "value", str(handle.status))
                        task_id = handle.task_id
                        if seen.get(task_id) == status:
                            continue
                        seen[task_id] = status
                        await self._send(
                            "subagent_status",
                            {
                                "task_id": task_id,
                                "subagent_name": handle.subagent_name,
                                "description": handle.description,
                                "status": status,
                                "error": handle.error,
                            },
                        )
                        ts = datetime.now(UTC).isoformat()
                        if status == "running":
                            await self._send(
                                "subagent_message",
                                {
                                    "task_id": task_id,
                                    "type": "info",
                                    "text": "Task started — running in background",
                                    "timestamp": ts,
                                },
                            )
                        elif status == "waiting_for_answer" and handle.pending_question:
                            await self._send(
                                "subagent_message",
                                {
                                    "task_id": task_id,
                                    "type": "question",
                                    "text": handle.pending_question,
                                    "timestamp": ts,
                                },
                            )
                        elif status == "completed" and handle.result:
                            await self._send(
                                "subagent_message",
                                {
                                    "task_id": task_id,
                                    "type": "result",
                                    "text": handle.result[:1500],
                                    "timestamp": ts,
                                },
                            )
                        elif status == "failed" and handle.error:
                            await self._send(
                                "subagent_message",
                                {
                                    "task_id": task_id,
                                    "type": "error",
                                    "text": handle.error,
                                    "timestamp": ts,
                                },
                            )
                await asyncio.sleep(1.0)
        except asyncio.CancelledError:
            task_manager = cap.task_manager
            if task_manager is not None:
                for handle in task_manager.list_handles():
                    status = getattr(handle.status, "value", str(handle.status))
                    if seen.get(handle.task_id) == status:
                        continue
                    await self._send(
                        "subagent_status",
                        {
                            "task_id": handle.task_id,
                            "subagent_name": handle.subagent_name,
                            "description": handle.description,
                            "status": status,
                            "error": handle.error,
                        },
                    )
                    ts = datetime.now(UTC).isoformat()
                    if status == "completed" and handle.result:
                        await self._send(
                            "subagent_message",
                            {
                                "task_id": handle.task_id,
                                "type": "result",
                                "text": handle.result[:1500],
                                "timestamp": ts,
                            },
                        )
                    elif status == "failed" and handle.error:
                        await self._send(
                            "subagent_message",
                            {
                                "task_id": handle.task_id,
                                "type": "error",
                                "text": handle.error,
                                "timestamp": ts,
                            },
                        )
            raise

    async def _on_subagent_message(self, msg: Any) -> None:
        """Forward TASK_UPDATE (steering) messages from the message bus as SSE events."""
        try:
            from subagents_pydantic_ai.types import MessageType

            if msg.type != MessageType.TASK_UPDATE:
                return
            payload = msg.payload
            text = payload.get("message") if isinstance(payload, dict) else str(payload)
            if not text:
                return
            await self._send(
                "subagent_message",
                {
                    "task_id": msg.task_id,
                    "type": "steering",
                    "text": text,
                    "timestamp": msg.timestamp.isoformat(),
                },
            )
        except Exception:
            pass

    async def _build_multimodal_input(
        self, user_message: str, file_ids: list[Any]
    ) -> str | list[Any]:
        """Fold attached images and parsed file text into the user message."""
        if not file_ids:
            return user_message

        storage = get_file_storage()
        image_parts: list[BinaryContent] = []
        file_context_parts: list[str] = []
        async with get_db_context() as file_db:
            attached_files = await get_conversation_service(file_db).list_attached_files(
                file_ids, user_id=self.user.id
            )
            for chat_file in attached_files:
                try:
                    if chat_file.file_type == "image":
                        file_data = await storage.load(chat_file.storage_path)
                        image_parts.append(
                            BinaryContent(data=file_data, media_type=chat_file.mime_type)
                        )
                    elif chat_file.parsed_content:
                        file_context_parts.append(
                            f"\n---\nAttached file: {chat_file.filename}\n```\n{chat_file.parsed_content}\n```"
                        )
                except Exception:
                    logger.warning("Failed to load file %s", chat_file.id, exc_info=True)

        full_text = user_message + "".join(file_context_parts)
        if image_parts:
            return [full_text, *image_parts]
        return full_text

    async def _stream_agent_run(
        self,
        agent_run: Any,
        user_message: str,
        collected_tool_calls: list[dict[str, Any]],
        collected_thinking: list[str],
    ) -> None:
        """Drive the agent_run iterator, dispatching each node to its streaming helper."""
        async for node in agent_run:
            if Agent.is_user_prompt_node(node):
                prompt_text = (
                    node.user_prompt if isinstance(node.user_prompt, str) else user_message
                )
                await send_event(self.websocket, "user_prompt_processed", {"prompt": prompt_text})
            elif Agent.is_model_request_node(node):
                await send_event(self.websocket, "model_request_start", {})
                async with node.stream(agent_run.ctx) as request_stream:
                    await self._stream_request_events(request_stream, collected_thinking)
            elif Agent.is_call_tools_node(node):
                await send_event(self.websocket, "call_tools_start", {})
                async with node.stream(agent_run.ctx) as handle_stream:
                    await self._stream_tool_events(handle_stream, collected_tool_calls)
            elif Agent.is_end_node(node) and agent_run.result is not None:
                await send_event(
                    self.websocket, "final_result", {"output": agent_run.result.output}
                )

    async def _stream_request_events(
        self, request_stream: Any, collected_thinking: list[str]
    ) -> None:
        """Forward model-request events (text/thinking/tool deltas + final-result start).

        During a deep research turn the model narrates every delegation step.
        A plain-text response ends a PydanticAI run, so a step that issues a
        planning/delegation tool call (``RESEARCH_TOOL_NAMES``) is interstitial:
        its text is buffered and dropped. A step with only content tools (charts,
        RAG) or no tool calls is the final answer and its text is released.
        Reasoning and tool events are always forwarded.
        """
        deep_research = self._research is not None
        buffered_text: list[tuple[int, str]] = []
        tool_names: dict[int, str] = {}

        async def emit_text(index: int, content: str) -> None:
            if not content:
                return
            if deep_research:
                buffered_text.append((index, content))
            else:
                await send_event(self.websocket, "text_delta", {"index": index, "content": content})

        async for event in request_stream:
            if isinstance(event, PartStartEvent):
                await send_event(
                    self.websocket,
                    "part_start",
                    {"index": event.index, "part_type": type(event.part).__name__},
                )
                if isinstance(event.part, ToolCallPart):
                    if event.part.tool_name:
                        tool_names[event.index] = event.part.tool_name
                elif isinstance(event.part, TextPart) and event.part.content:
                    await emit_text(event.index, event.part.content)
                elif isinstance(event.part, ThinkingPart) and event.part.content:
                    if collected_thinking:
                        collected_thinking.append(" ")
                    collected_thinking.append(event.part.content)
                    await send_event(
                        self.websocket,
                        "thinking_delta",
                        {"index": event.index, "content": event.part.content},
                    )
            elif isinstance(event, PartDeltaEvent):
                if isinstance(event.delta, TextPartDelta):
                    await emit_text(event.index, event.delta.content_delta)
                elif isinstance(event.delta, ThinkingPartDelta):
                    if event.delta.content_delta:
                        collected_thinking.append(event.delta.content_delta)
                        await send_event(
                            self.websocket,
                            "thinking_delta",
                            {"index": event.index, "content": event.delta.content_delta},
                        )
                elif isinstance(event.delta, ToolCallPartDelta):
                    if event.delta.tool_name_delta:
                        tool_names[event.index] = (
                            tool_names.get(event.index, "") + event.delta.tool_name_delta
                        )
                    await send_event(
                        self.websocket,
                        "tool_call_delta",
                        {"index": event.index, "args_delta": event.delta.args_delta},
                    )
            elif isinstance(event, FinalResultEvent):
                await send_event(
                    self.websocket,
                    "final_result_start",
                    {"tool_name": event.tool_name},
                )

        made_research_call = any(name in RESEARCH_TOOL_NAMES for name in tool_names.values())
        if deep_research and buffered_text and not made_research_call:
            for index, content in buffered_text:
                await send_event(self.websocket, "text_delta", {"index": index, "content": content})

    async def _stream_tool_events(
        self,
        handle_stream: Any,
        collected_tool_calls: list[dict[str, Any]],
    ) -> None:
        """Forward tool-call/result events; collect tool calls (with results) for persistence."""
        pending: dict[str, dict[str, Any]] = {}
        async for tool_event in handle_stream:
            if isinstance(tool_event, FunctionToolCallEvent):
                tc = {
                    "tool_call_id": tool_event.part.tool_call_id,
                    "tool_name": tool_event.part.tool_name,
                    "args": tool_event.part.args_as_dict(raise_if_invalid=False),
                }
                collected_tool_calls.append(tc)
                pending[tool_event.part.tool_call_id] = tc
                await send_event(self.websocket, "tool_call", tc)
            elif isinstance(tool_event, FunctionToolResultEvent):
                result_content = str(tool_event.part.content)
                tc = pending.get(tool_event.tool_call_id)
                if tc is not None:
                    tc["result"] = result_content
                await send_event(
                    self.websocket,
                    "tool_result",
                    {
                        "tool_call_id": tool_event.tool_call_id,
                        "content": result_content,
                    },
                )
