"""User-owned coding workspaces (Settings → Workspaces; ADR-006).

Nested under ``/me/workspaces`` because a workspace always belongs to the
current user. The chat WebSocket only *references* a workspace id from here —
it never creates one.
"""

from typing import Any
from uuid import UUID

from fastapi import APIRouter, status

from app.api.deps import CurrentUser, WorkspaceSvc
from app.schemas.workspace import WorkspaceCreate, WorkspaceList, WorkspaceRead, WorkspaceUpdate

router = APIRouter()


@router.get("", response_model=WorkspaceList)
async def list_workspaces(service: WorkspaceSvc, user: CurrentUser) -> Any:
    """List the current user's workspaces."""
    items, total = await service.list_for_user(user_id=user.id)
    return WorkspaceList(items=items, total=total)


@router.post("", response_model=WorkspaceRead, status_code=status.HTTP_201_CREATED)
async def create_workspace(data: WorkspaceCreate, service: WorkspaceSvc, user: CurrentUser) -> Any:
    """Create a workspace. The sandbox id is generated server-side."""
    return await service.create(user_id=user.id, data=data)


@router.patch("/{workspace_id}", response_model=WorkspaceRead)
async def update_workspace(
    workspace_id: UUID,
    data: WorkspaceUpdate,
    service: WorkspaceSvc,
    user: CurrentUser,
) -> Any:
    """Patch a workspace. An explicit ``repo_url: null`` detaches the repository."""
    return await service.update(user_id=user.id, workspace_id=workspace_id, data=data)


@router.delete("/{workspace_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def delete_workspace(workspace_id: UUID, service: WorkspaceSvc, user: CurrentUser) -> Any:
    """Remove a workspace row. The sandbox contents are the sandbox service's to reap."""
    await service.delete(user_id=user.id, workspace_id=workspace_id)
    return None
