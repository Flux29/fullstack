"""Shared agent service utilities.

Houses framework-agnostic helpers used by every WebSocket agent route:
  - ``AgentConnectionManager`` + ``send_event`` — WebSocket fan-out
  - ``build_message_history`` — convert dicts to provider-native messages
  - ``load_conversation_history`` — restore persisted turns for a resumed chat
  - ``persist_user_turn`` / ``persist_assistant_turn`` — DB persistence
  - ``resolve_kb_collections`` — Teams+RAG collection lookup
  - ``normalize_tool_args`` / ``truncate_title`` — small utilities

Framework-specific concerns (multimodal input, streaming events) stay in the route.
"""

import json
import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from fastapi import WebSocket, WebSocketDisconnect
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    SystemPromptPart,
    TextPart,
    UserPromptPart,
)

from app.api.deps import get_conversation_service
from app.db.session import get_db_context
from app.schemas.conversation import (
    ConversationCreate,
    ConversationUpdate,
    MessageCreate,
    ToolCallComplete,
    ToolCallCreate,
)

logger = logging.getLogger(__name__)


async def send_event(websocket: WebSocket, event_type: str, data: Any) -> bool:
    """Send a JSON event to a WebSocket client.

    Returns True if sent successfully, False if the connection is already closed.
    """
    try:
        await websocket.send_json({"type": event_type, "data": data})
        return True
    except (WebSocketDisconnect, RuntimeError):
        return False


class AgentConnectionManager:
    """WebSocket connection manager for AI agent."""

    def __init__(self) -> None:
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket) -> None:
        """Accept and store a new WebSocket connection."""
        # Echo back the application subprotocol chosen during auth (if any)
        subprotocol = getattr(websocket.state, "accept_subprotocol", None)
        await websocket.accept(subprotocol=subprotocol)
        self.active_connections.append(websocket)
        logger.info(
            "Agent WebSocket connected. Total connections: %d", len(self.active_connections)
        )

    def disconnect(self, websocket: WebSocket) -> None:
        """Remove a WebSocket connection."""
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
        logger.info(
            "Agent WebSocket disconnected. Total connections: %d", len(self.active_connections)
        )

    async def send_event(self, websocket: WebSocket, event_type: str, data: Any) -> bool:
        """Forward to the module-level :func:`send_event`."""
        return await send_event(websocket, event_type, data)


def build_message_history(history: list[dict[str, str]]) -> list[ModelRequest | ModelResponse]:
    """Convert conversation history to PydanticAI message format."""
    model_history: list[ModelRequest | ModelResponse] = []

    for msg in history:
        if msg["role"] == "user":
            model_history.append(ModelRequest(parts=[UserPromptPart(content=msg["content"])]))
        elif msg["role"] == "assistant":
            model_history.append(ModelResponse(parts=[TextPart(content=msg["content"])]))
        elif msg["role"] == "system":
            model_history.append(ModelRequest(parts=[SystemPromptPart(content=msg["content"])]))

    return model_history


async def load_conversation_history(
    user: Any, conversation_id: str, *, page_size: int = 200
) -> list[dict[str, str]]:
    """Load every persisted model-visible message for a conversation.

    Ownership is checked before any messages are returned.  The caller invokes
    this before persisting the new user prompt, so the prompt is supplied to
    PydanticAI exactly once as the current input rather than duplicated in
    ``message_history``.
    """
    conversation_uuid = UUID(conversation_id)
    history: list[dict[str, str]] = []
    async with get_db_context() as db:
        conv_service = get_conversation_service(db)
        await conv_service.get_conversation(conversation_uuid, user_id=user.id)

        skip = 0
        while True:
            messages, total = await conv_service.list_messages(
                conversation_uuid,
                skip=skip,
                limit=page_size,
            )
            history.extend(
                {"role": message.role, "content": message.content}
                for message in messages
                if message.role in {"user", "assistant", "system"}
            )
            skip += len(messages)
            if not messages or skip >= total:
                break
    return history


def truncate_title(text: str, limit: int = 50) -> str:
    """Return text truncated to ``limit`` characters."""
    return text[:limit] if len(text) > limit else text


async def persist_user_turn(
    user: Any,
    user_message: str,
    file_ids: list[Any],
    requested_conversation_id: str | None,
) -> tuple[str | None, bool, str | None]:
    """Resolve the conversation, persist the user message, and link any uploaded files.

    A ``requested_conversation_id`` of None means the client is starting a new chat:
    a new conversation is always created. The WebSocket session's previous conversation
    must never leak in here — the frontend keeps one socket across chats and relies on
    the ``conversation_created`` event to learn the new id.

    Returns ``(conversation_id, was_newly_created, organization_id)``. When
    ``was_newly_created`` is True the caller should emit a ``conversation_created``
    WebSocket event. ``organization_id`` is the conversation's owning org (the user's
    Personal org for new conversations) so usage events can be billed correctly;
    None when teams are disabled or no org context is available.
    """
    newly_created = False
    organization_id: str | None = None
    conversation_id = requested_conversation_id
    try:
        async with get_db_context() as db:
            conv_service = get_conversation_service(db)

            if requested_conversation_id:
                conv = await conv_service.get_conversation(
                    UUID(requested_conversation_id), user_id=user.id
                )
                if not conv.title and user_message:
                    await conv_service.update_conversation(
                        UUID(requested_conversation_id),
                        ConversationUpdate(title=truncate_title(user_message)),
                        user_id=user.id,
                    )
            else:
                conversation = await conv_service.create_conversation(
                    ConversationCreate(
                        user_id=user.id,
                        title=truncate_title(user_message),
                    )
                )
                conversation_id = str(conversation.id)
                newly_created = True

            user_msg = await conv_service.add_message(
                UUID(conversation_id),
                MessageCreate(role="user", content=user_message),
            )
            if file_ids:
                try:
                    await conv_service.link_files_to_message(user_msg.id, file_ids)
                except Exception as e:
                    logger.warning("Failed to link files: %s", e)
    except Exception as e:
        logger.warning("Failed to persist conversation: %s", e)

    return conversation_id, newly_created, organization_id


def normalize_tool_args(args: Any) -> dict[str, Any]:
    """Coerce a tool-call ``args`` payload to a dict (handles JSON strings + None)."""
    if isinstance(args, str):
        return json.loads(args) if args.strip() else {}
    if args is None:
        return {}
    return args


async def persist_assistant_turn(
    conversation_id: str,
    output: str,
    model_name: str | None,
    collected_tool_calls: list[dict[str, Any]],
    thinking: str | None = None,
) -> str | None:
    """Persist the assistant message and any tool calls. Returns the saved message id."""
    try:
        async with get_db_context() as db:
            conv_service = get_conversation_service(db)
            assistant_msg = await conv_service.add_message(
                UUID(conversation_id),
                MessageCreate(
                    role="assistant", content=output, thinking=thinking, model_name=model_name
                ),
            )
            for tc in collected_tool_calls:
                try:
                    tc_obj = await conv_service.start_tool_call(
                        assistant_msg.id,
                        ToolCallCreate(
                            tool_call_id=tc["tool_call_id"],
                            tool_name=tc["tool_name"],
                            args=normalize_tool_args(tc.get("args")),
                            started_at=datetime.now(UTC),
                        ),
                    )
                    if tc.get("result"):
                        await conv_service.complete_tool_call(
                            tc_obj.id,
                            ToolCallComplete(
                                result=tc["result"],
                                completed_at=datetime.now(UTC),
                                success=True,
                            ),
                        )
                except Exception as e:
                    logger.warning("Failed to persist tool call: %s", e)
            return str(assistant_msg.id)
    except Exception as e:
        logger.warning("Failed to persist assistant response: %s", e)
        return None
