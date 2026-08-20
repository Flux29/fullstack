"""Chat file repository."""

from collections.abc import Iterable
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy import update as sql_update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.chat_file import ChatFile


async def sum_size_for_user(db: AsyncSession, user_id: UUID) -> int:
    """Return total bytes of chat files uploaded by a user."""
    result = await db.execute(
        select(func.coalesce(func.sum(ChatFile.size), 0)).where(ChatFile.user_id == user_id)
    )
    return int(result.scalar_one())


async def get_by_id(db: AsyncSession, file_id: UUID) -> ChatFile | None:
    """Get a chat file by ID."""
    return await db.get(ChatFile, file_id)


async def get_many(
    db: AsyncSession, file_ids: Iterable[UUID], *, user_id: UUID | None = None
) -> list[ChatFile]:
    """Batch-load multiple chat files by IDs.

    When user_id is provided, only files owned by that user are returned; file_ids
    are client-supplied on the chat path, so the filter is the IDOR guard.
    """
    ids = list(file_ids)
    if not ids:
        return []
    stmt = select(ChatFile).where(ChatFile.id.in_(ids))
    if user_id is not None:
        stmt = stmt.where(ChatFile.user_id == user_id)
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def link_to_message(
    db: AsyncSession, *, message_id: UUID, file_ids: Iterable[UUID], user_id: UUID | None = None
) -> None:
    """Link multiple chat files to a message by setting message_id on each.

    When user_id is provided, only files owned by that user are re-linked; file_ids
    are client-supplied on the chat path, so the filter is the IDOR guard.
    """
    ids = list(file_ids)
    if not ids:
        return
    stmt = sql_update(ChatFile).where(ChatFile.id.in_(ids))
    if user_id is not None:
        stmt = stmt.where(ChatFile.user_id == user_id)
    await db.execute(stmt.values(message_id=message_id))
    await db.flush()


async def create(
    db: AsyncSession,
    *,
    user_id: UUID,
    filename: str,
    mime_type: str,
    size: int,
    storage_path: str,
    file_type: str,
    parsed_content: str | None = None,
) -> ChatFile:
    """Create a new chat file record."""
    chat_file = ChatFile(
        user_id=user_id,
        filename=filename,
        mime_type=mime_type,
        size=size,
        storage_path=storage_path,
        file_type=file_type,
        parsed_content=parsed_content,
    )
    db.add(chat_file)
    await db.flush()
    return chat_file
