"""Tests for coding workspaces (ADR-006): schema normalization, the service's
policy rules, and the /me/workspaces HTTP surface through the proxy-shaped client."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from httpx import AsyncClient

from app.api.deps import get_current_user, get_workspace_service
from app.core.config import settings
from app.core.exceptions import AlreadyExistsError, NotFoundError, ValidationError
from app.db.models.workspace import Workspace
from app.main import app
from app.repositories import workspace_repo
from app.schemas.workspace import (
    STRICT_AUTO_APPROVE_MESSAGE,
    WorkspaceCreate,
    WorkspaceUpdate,
    normalize_repo_url,
)
from app.services.workspace import WorkspaceService, new_sandbox_id

pytestmark = pytest.mark.anyio

WORKSPACES_URL = f"{settings.API_V1_STR}/me/workspaces"


class MockUser:
    def __init__(self) -> None:
        self.id = uuid4()
        self.email = "user@example.com"
        self.full_name = "Test User"
        self.is_active = True
        self.role = "user"

    def has_role(self, role) -> bool:
        return self.role == (role.value if hasattr(role, "value") else role)


def _workspace(user_id=None, **overrides) -> Workspace:
    defaults: dict = {
        "id": uuid4(),
        "user_id": user_id or uuid4(),
        "name": "app",
        "backend_kind": "remote",
        "root": "ws-abc123def456",
        "repo_url": "https://github.com/org/app",
        "ruleset": "default",
        "auto_approve": False,
        "created_at": datetime.now(UTC),
        "updated_at": None,
    }
    defaults.update(overrides)
    return Workspace(**defaults)


# --- schema -----------------------------------------------------------------


def test_normalize_repo_url_strips_userinfo_query_and_fragment_but_keeps_path():
    assert (
        normalize_repo_url("https://user:t0ken@github.com/org/app.git/?x=1#frag")
        == "https://github.com/org/app.git"
    )


@pytest.mark.parametrize(
    "url", ["git@github.com:org/app.git", "ssh://git@host/app", "https://", "ftp://h/x"]
)
def test_normalize_repo_url_rejects_non_http_or_hostless(url):
    with pytest.raises(ValueError):
        normalize_repo_url(url)


def test_workspace_create_normalizes_repo_url():
    data = WorkspaceCreate(name="app", repo_url="https://me:pw@example.com/a/b?token=1")
    assert data.repo_url == "https://example.com/a/b"


# --- service ----------------------------------------------------------------


def test_new_sandbox_id_is_opaque_and_container_name_safe():
    sandbox_id = new_sandbox_id()
    assert sandbox_id.startswith("ws-")
    # The sandbox layer names a container after this; keep it inside the
    # 63-character DNS label limit rather than pinning the exact token width.
    assert len(sandbox_id) <= 63
    assert new_sandbox_id() != sandbox_id


async def test_create_workspace_generates_root_and_never_accepts_one(monkeypatch, mock_db_session):
    service = WorkspaceService(mock_db_session)
    user_id = uuid4()
    monkeypatch.setattr(workspace_repo, "get_by_name", AsyncMock(return_value=None))
    created = AsyncMock(side_effect=lambda db, **kw: _workspace(**kw))
    monkeypatch.setattr(workspace_repo, "create", created)

    workspace = await service.create(user_id=user_id, data=WorkspaceCreate(name="app"))

    assert workspace.root.startswith("ws-")
    assert created.call_args.kwargs["root"] == workspace.root
    assert created.call_args.kwargs["user_id"] == user_id


async def test_create_workspace_rejects_auto_approve_with_strict_ruleset(
    monkeypatch, mock_db_session
):
    """The rule has one owner — the service — so create and update agree."""
    service = WorkspaceService(mock_db_session)
    monkeypatch.setattr(workspace_repo, "get_by_name", AsyncMock(return_value=None))
    create = AsyncMock()
    monkeypatch.setattr(workspace_repo, "create", create)

    with pytest.raises(ValidationError):
        await service.create(
            user_id=uuid4(), data=WorkspaceCreate(name="app", ruleset="strict", auto_approve=True)
        )
    create.assert_not_called()


async def test_create_readonly_workspace_clears_meaningless_auto_approve(
    monkeypatch, mock_db_session
):
    """A read-only workspace registers no write or execute tool (ADR-006 §4),
    so the flag is stored false rather than as an override that cannot fire."""
    service = WorkspaceService(mock_db_session)
    monkeypatch.setattr(workspace_repo, "get_by_name", AsyncMock(return_value=None))
    created = AsyncMock(side_effect=lambda db, **kw: _workspace(**kw))
    monkeypatch.setattr(workspace_repo, "create", created)

    workspace = await service.create(
        user_id=uuid4(), data=WorkspaceCreate(name="app", ruleset="readonly", auto_approve=True)
    )

    assert workspace.auto_approve is False


async def test_update_to_readonly_clears_auto_approve_the_request_never_mentioned(
    monkeypatch, mock_db_session
):
    service = WorkspaceService(mock_db_session)
    user_id = uuid4()
    stored = _workspace(user_id=user_id, auto_approve=True)
    monkeypatch.setattr(workspace_repo, "get_by_id", AsyncMock(return_value=stored))
    update = AsyncMock(return_value=stored)
    monkeypatch.setattr(workspace_repo, "update", update)

    await service.update(
        user_id=user_id, workspace_id=stored.id, data=WorkspaceUpdate(ruleset="readonly")
    )

    assert update.call_args.kwargs["update_data"] == {"ruleset": "readonly", "auto_approve": False}


async def test_create_workspace_with_duplicate_name_raises_already_exists(
    monkeypatch, mock_db_session
):
    service = WorkspaceService(mock_db_session)
    monkeypatch.setattr(workspace_repo, "get_by_name", AsyncMock(return_value=_workspace()))
    with pytest.raises(AlreadyExistsError):
        await service.create(user_id=uuid4(), data=WorkspaceCreate(name="app"))


async def test_update_refuses_auto_approve_when_stored_ruleset_is_strict(
    monkeypatch, mock_db_session
):
    """The combination is refused even when the two fields arrive in separate PATCHes."""
    service = WorkspaceService(mock_db_session)
    user_id = uuid4()
    stored = _workspace(user_id=user_id, ruleset="strict")
    monkeypatch.setattr(workspace_repo, "get_by_id", AsyncMock(return_value=stored))
    update = AsyncMock()
    monkeypatch.setattr(workspace_repo, "update", update)

    with pytest.raises(ValidationError):
        await service.update(
            user_id=user_id, workspace_id=stored.id, data=WorkspaceUpdate(auto_approve=True)
        )
    update.assert_not_called()


async def test_update_refuses_strict_when_stored_auto_approve_is_on(monkeypatch, mock_db_session):
    service = WorkspaceService(mock_db_session)
    user_id = uuid4()
    stored = _workspace(user_id=user_id, auto_approve=True)
    monkeypatch.setattr(workspace_repo, "get_by_id", AsyncMock(return_value=stored))
    with pytest.raises(ValidationError):
        await service.update(
            user_id=user_id, workspace_id=stored.id, data=WorkspaceUpdate(ruleset="strict")
        )


async def test_update_explicit_null_repo_url_detaches_repository(monkeypatch, mock_db_session):
    service = WorkspaceService(mock_db_session)
    user_id = uuid4()
    stored = _workspace(user_id=user_id)
    monkeypatch.setattr(workspace_repo, "get_by_id", AsyncMock(return_value=stored))
    update = AsyncMock(return_value=stored)
    monkeypatch.setattr(workspace_repo, "update", update)

    await service.update(
        user_id=user_id,
        workspace_id=stored.id,
        data=WorkspaceUpdate.model_validate({"repo_url": None}),
    )

    assert update.call_args.kwargs["update_data"] == {"repo_url": None}


async def test_patching_auto_approve_on_a_readonly_workspace_stores_false(
    monkeypatch, mock_db_session
):
    """The resolved value is what gets written, not the value asked for."""
    service = WorkspaceService(mock_db_session)
    user_id = uuid4()
    stored = _workspace(user_id=user_id, ruleset="readonly", auto_approve=False)
    monkeypatch.setattr(workspace_repo, "get_by_id", AsyncMock(return_value=stored))
    update = AsyncMock(return_value=stored)
    monkeypatch.setattr(workspace_repo, "update", update)

    await service.update(
        user_id=user_id, workspace_id=stored.id, data=WorkspaceUpdate(auto_approve=True)
    )

    assert update.call_args.kwargs["update_data"]["auto_approve"] is False


async def test_get_for_user_hides_another_users_workspace(monkeypatch, mock_db_session):
    service = WorkspaceService(mock_db_session)
    other = _workspace(user_id=uuid4())
    monkeypatch.setattr(workspace_repo, "get_by_id", AsyncMock(return_value=other))
    with pytest.raises(NotFoundError):
        await service.get_for_user(user_id=uuid4(), workspace_id=other.id)


async def test_delete_removes_owned_workspace(monkeypatch, mock_db_session):
    service = WorkspaceService(mock_db_session)
    user_id = uuid4()
    stored = _workspace(user_id=user_id)
    monkeypatch.setattr(workspace_repo, "get_by_id", AsyncMock(return_value=stored))
    delete = AsyncMock()
    monkeypatch.setattr(workspace_repo, "delete", delete)

    await service.delete(user_id=user_id, workspace_id=stored.id)

    delete.assert_awaited_once_with(mock_db_session, db_workspace=stored)


# --- routes -----------------------------------------------------------------


@pytest.fixture
def mock_user() -> MockUser:
    return MockUser()


@pytest.fixture
def workspace_service() -> AsyncMock:
    return AsyncMock(spec=WorkspaceService)


@pytest.fixture
def authed_client(client: AsyncClient, mock_user: MockUser, workspace_service: AsyncMock):
    app.dependency_overrides[get_current_user] = lambda: mock_user
    app.dependency_overrides[get_workspace_service] = lambda: workspace_service
    yield client
    app.dependency_overrides.pop(get_current_user, None)
    app.dependency_overrides.pop(get_workspace_service, None)


async def test_list_workspaces_returns_items_and_total(authed_client, mock_user, workspace_service):
    rows = [_workspace(user_id=mock_user.id), _workspace(user_id=mock_user.id, name="docs")]
    workspace_service.list_for_user.return_value = (rows, 2)

    resp = await authed_client.get(WORKSPACES_URL)

    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    assert [w["name"] for w in body["items"]] == ["app", "docs"]
    assert set(body["items"][0]) >= {
        "id",
        "name",
        "backend_kind",
        "root",
        "repo_url",
        "ruleset",
        "auto_approve",
    }


async def test_create_workspace_returns_201_with_generated_root(
    authed_client, mock_user, workspace_service
):
    workspace_service.create.return_value = _workspace(user_id=mock_user.id, root="ws-0123456789ab")

    resp = await authed_client.post(
        WORKSPACES_URL, json={"name": "app", "repo_url": "https://github.com/org/app"}
    )

    assert resp.status_code == 201
    assert resp.json()["root"] == "ws-0123456789ab"
    assert workspace_service.create.call_args.kwargs["user_id"] == mock_user.id


async def test_create_workspace_strict_with_auto_approve_is_422(authed_client, workspace_service):
    workspace_service.create.side_effect = ValidationError(
        message=STRICT_AUTO_APPROVE_MESSAGE, details={}
    )
    resp = await authed_client.post(
        WORKSPACES_URL, json={"name": "app", "ruleset": "strict", "auto_approve": True}
    )

    assert resp.status_code == 422


async def test_create_workspace_duplicate_name_is_409(authed_client, workspace_service):
    workspace_service.create.side_effect = AlreadyExistsError(
        message="Workspace with this name already exists", details={"name": "app"}
    )
    resp = await authed_client.post(WORKSPACES_URL, json={"name": "app"})
    assert resp.status_code == 409


async def test_update_workspace_unknown_id_is_404(authed_client, workspace_service):
    workspace_service.update.side_effect = NotFoundError(message="Workspace not found", details={})
    resp = await authed_client.patch(f"{WORKSPACES_URL}/{uuid4()}", json={"auto_approve": True})
    assert resp.status_code == 404


async def test_delete_workspace_returns_204(authed_client, workspace_service):
    workspace_service.delete.return_value = None
    resp = await authed_client.delete(f"{WORKSPACES_URL}/{uuid4()}")
    assert resp.status_code == 204


async def test_workspaces_require_authentication(client: AsyncClient):
    resp = await client.get(WORKSPACES_URL)
    assert resp.status_code == 401
