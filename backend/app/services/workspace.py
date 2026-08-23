"""Business logic for user-owned coding workspaces (ADR-006).

The service owns the rules the schema cannot enforce on its own:
  - a workspace's sandbox id (``root``) is generated here and never accepted
    from the client, so a row can never point at a host path;
  - ``auto_approve`` and the ``strict`` ruleset are refused together, also
    when a PATCH would produce that combination from two separate edits — the
    schema sees one request, not the stored row, so this cannot live there;
  - a ``readonly`` workspace registers no write or execute tool (ADR-006 §4),
    so there is nothing for ``auto_approve`` to approve: it is cleared rather
    than stored as a flag the UI would have to render as permanently stuck on.
"""

from __future__ import annotations

import secrets
from typing import Any
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AlreadyExistsError, NotFoundError, ValidationError
from app.db.models.workspace import Workspace
from app.repositories import workspace_repo
from app.schemas.workspace import STRICT_AUTO_APPROVE_MESSAGE, WorkspaceCreate, WorkspaceUpdate


def new_sandbox_id() -> str:
    """An opaque sandbox session id. Short enough for a container name."""
    return f"ws-{secrets.token_hex(6)}"


def _resolve_policy(*, ruleset: str, auto_approve: bool) -> bool:
    """Refuse the contradictory combination; return the effective auto_approve."""
    if auto_approve and ruleset == "strict":
        raise ValidationError(
            message=STRICT_AUTO_APPROVE_MESSAGE,
            details={"ruleset": ruleset, "auto_approve": auto_approve},
        )
    return auto_approve and ruleset != "readonly"


class WorkspaceService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def list_for_user(self, *, user_id: UUID) -> tuple[list[Workspace], int]:
        return await workspace_repo.list_for_user(self.db, user_id=user_id)

    async def get_for_user(self, *, user_id: UUID, workspace_id: UUID) -> Workspace:
        """The workspace a chat turn names — must belong to the caller."""
        workspace = await workspace_repo.get_by_id(self.db, workspace_id)
        if workspace is None or workspace.user_id != user_id:
            raise NotFoundError(
                message="Workspace not found", details={"workspace_id": str(workspace_id)}
            )
        return workspace

    async def create(self, *, user_id: UUID, data: WorkspaceCreate) -> Workspace:
        auto_approve = _resolve_policy(ruleset=data.ruleset, auto_approve=data.auto_approve)
        existing = await workspace_repo.get_by_name(self.db, user_id=user_id, name=data.name)
        if existing is not None:
            raise AlreadyExistsError(
                message="Workspace with this name already exists", details={"name": data.name}
            )
        try:
            return await workspace_repo.create(
                self.db,
                user_id=user_id,
                name=data.name,
                backend_kind=data.backend_kind,
                root=new_sandbox_id(),
                repo_url=data.repo_url,
                ruleset=data.ruleset,
                auto_approve=auto_approve,
            )
        except IntegrityError as exc:
            raise AlreadyExistsError(
                message="Workspace with this name already exists", details={"name": data.name}
            ) from exc

    async def update(
        self, *, user_id: UUID, workspace_id: UUID, data: WorkspaceUpdate
    ) -> Workspace:
        db_workspace = await self.get_for_user(user_id=user_id, workspace_id=workspace_id)
        update_data: dict[str, Any] = data.model_dump(exclude_unset=True)

        ruleset = update_data.get("ruleset", db_workspace.ruleset)
        auto_approve = _resolve_policy(
            ruleset=ruleset,
            auto_approve=update_data.get("auto_approve", db_workspace.auto_approve),
        )
        # A ruleset change can clear a flag the request never mentioned.
        if auto_approve != db_workspace.auto_approve:
            update_data["auto_approve"] = auto_approve

        if "name" in update_data and update_data["name"] != db_workspace.name:
            collision = await workspace_repo.get_by_name(
                self.db, user_id=user_id, name=update_data["name"]
            )
            if collision is not None and collision.id != db_workspace.id:
                raise AlreadyExistsError(
                    message="Workspace with this name already exists",
                    details={"name": update_data["name"]},
                )

        if not update_data:
            return db_workspace
        return await workspace_repo.update(
            self.db, db_workspace=db_workspace, update_data=update_data
        )

    async def delete(self, *, user_id: UUID, workspace_id: UUID) -> None:
        db_workspace = await self.get_for_user(user_id=user_id, workspace_id=workspace_id)
        await workspace_repo.delete(self.db, db_workspace=db_workspace)
