"""Data access for user-owned coding workspaces (PostgreSQL async)."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.workspace import Workspace


async def get_by_id(db: AsyncSession, workspace_id: UUID) -> Workspace | None:
    result = await db.execute(select(Workspace).where(Workspace.id == workspace_id))
    return result.scalar_one_or_none()


async def get_by_name(db: AsyncSession, *, user_id: UUID, name: str) -> Workspace | None:
    result = await db.execute(
        select(Workspace).where(Workspace.user_id == user_id, Workspace.name == name)
    )
    return result.scalar_one_or_none()


async def list_for_user(db: AsyncSession, *, user_id: UUID) -> tuple[list[Workspace], int]:
    stmt = (
        select(Workspace).where(Workspace.user_id == user_id).order_by(Workspace.created_at.asc())
    )
    # Unpaginated by design (a user has a handful of workspaces), so ``total``
    # is the list length — a COUNT here would be a second round trip for a
    # number we already hold.
    items = list((await db.execute(stmt)).scalars())
    return items, len(items)


async def create(
    db: AsyncSession,
    *,
    user_id: UUID,
    name: str,
    backend_kind: str,
    root: str,
    repo_url: str | None,
    ruleset: str,
    auto_approve: bool,
) -> Workspace:
    workspace = Workspace(
        user_id=user_id,
        name=name,
        backend_kind=backend_kind,
        root=root,
        repo_url=repo_url,
        ruleset=ruleset,
        auto_approve=auto_approve,
    )
    db.add(workspace)
    await db.flush()
    await db.refresh(workspace)
    return workspace


async def update(
    db: AsyncSession, *, db_workspace: Workspace, update_data: dict[str, Any]
) -> Workspace:
    for field, value in update_data.items():
        setattr(db_workspace, field, value)
    await db.flush()
    await db.refresh(db_workspace)
    return db_workspace


async def delete(db: AsyncSession, *, db_workspace: Workspace) -> None:
    await db.delete(db_workspace)
    await db.flush()
