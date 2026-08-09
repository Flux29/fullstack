"""Tests for the sync-source routes in rag.py — the HTTP surface of the credential flow.

Two harnesses, deliberately. The mocked-service client pins what the route layer alone
owes: admin gating, status codes, the error envelope, and response shaping — the service
logic behind it is already covered by tests/test_services_sync_source.py. Two end-to-end
tests then run the REAL service with only the repository patched, so at least one path
proves route -> service -> masked JSON with real crypto rather than trusting the mocks.
"""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.deps import get_current_user, get_db_session, get_redis, get_sync_source_service
from app.core.config import settings
from app.core.crypto import is_encrypted
from app.core.exceptions import BadRequestError, NotFoundError
from app.main import app
from app.services.sync_source import _SECRET_MASK, _encrypt_config

SOURCES_URL = f"{settings.API_V1_STR}/rag/sync/sources"
CONNECTORS_URL = f"{settings.API_V1_STR}/rag/sync/connectors"
S3_CONFIG = {
    "bucket": "docs",
    "prefix": "",
    "access_key_id": "AKIA123",
    "secret_access_key": "shhh",
}
S3_SECRETS = ("access_key_id", "secret_access_key")


class MockUser:
    """Mock user for testing."""

    def __init__(self, role="user"):
        self.id = uuid4()
        self.email = f"{role}@example.com"
        self.full_name = "Test User"
        self.is_active = True
        self.role = role

    def has_role(self, role) -> bool:
        return self.role == (role.value if hasattr(role, "value") else role)


class MockSyncSource:
    """Mock sync source row for the real-service tests."""

    def __init__(self, config=None, collection_name="kb-main"):
        self.id = uuid4()
        self.organization_id = None
        self.name = "Docs bucket"
        self.connector_type = "s3"
        self.collection_name = collection_name
        self.config = config if config is not None else _encrypt_config(dict(S3_CONFIG), "s3")
        self.sync_mode = "new_only"
        self.schedule_minutes = None
        self.is_active = True
        self.last_sync_at = None
        self.last_sync_status = None
        self.last_error = None
        self.created_at = None


def _read_model(source: MockSyncSource) -> dict:
    """A SyncSourceRead-shaped payload for the mocked service to return."""
    from app.schemas.sync_source import SyncSourceRead

    return SyncSourceRead(
        id=str(source.id),
        organization_id=None,
        name=source.name,
        connector_type=source.connector_type,
        collection_name=source.collection_name,
        config={"bucket": "docs", "access_key_id": _SECRET_MASK, "secret_access_key": _SECRET_MASK},
        sync_mode=source.sync_mode,
        schedule_minutes=None,
        is_active=True,
        last_sync_at=None,
        last_sync_status=None,
        last_error=None,
        created_at=None,
    )


@pytest.fixture
def mock_service() -> MagicMock:
    return MagicMock()


@pytest.fixture
async def admin_client(mock_service, mock_redis, mock_db_session) -> AsyncClient:
    """Client authenticated as an admin, with the sync-source service mocked."""
    app.dependency_overrides[get_current_user] = lambda: MockUser(role="admin")
    app.dependency_overrides[get_sync_source_service] = lambda: mock_service
    app.dependency_overrides[get_redis] = lambda: mock_redis
    app.dependency_overrides[get_db_session] = lambda: mock_db_session

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()


@pytest.fixture
async def non_admin_client(mock_redis, mock_db_session) -> AsyncClient:
    """Client authenticated as a plain user - RoleChecker must refuse it."""
    app.dependency_overrides[get_current_user] = lambda: MockUser(role="user")
    app.dependency_overrides[get_redis] = lambda: mock_redis
    app.dependency_overrides[get_db_session] = lambda: mock_db_session

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()


@pytest.fixture
async def real_service_admin_client(mock_redis, mock_db_session) -> AsyncClient:
    """Admin client with the REAL service running - only auth and the DB are overridden."""
    app.dependency_overrides[get_current_user] = lambda: MockUser(role="admin")
    app.dependency_overrides[get_redis] = lambda: mock_redis
    app.dependency_overrides[get_db_session] = lambda: mock_db_session

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()


class TestAuthGating:
    @pytest.mark.anyio
    async def test_unauthenticated_list_returns_401_with_challenge(self, client: AsyncClient):
        response = await client.get(SOURCES_URL)
        assert response.status_code == 401
        assert "WWW-Authenticate" in response.headers

    @pytest.mark.anyio
    async def test_non_admin_list_returns_403(self, non_admin_client: AsyncClient):
        response = await non_admin_client.get(SOURCES_URL)
        assert response.status_code == 403

    @pytest.mark.anyio
    async def test_non_admin_create_returns_403(self, non_admin_client: AsyncClient):
        response = await non_admin_client.post(
            SOURCES_URL, json={"name": "x", "connector_type": "s3", "config": {}}
        )
        assert response.status_code == 403


class TestHttpContract:
    @pytest.mark.anyio
    async def test_list_sources_returns_items_and_total(self, admin_client, mock_service):
        from app.schemas.sync_source import SyncSourceList

        source = MockSyncSource()
        mock_service.list_sources = AsyncMock(
            return_value=SyncSourceList(items=[_read_model(source)], total=1)
        )

        response = await admin_client.get(SOURCES_URL)

        assert response.status_code == 200
        payload = response.json()
        assert payload["total"] == 1
        assert payload["items"][0]["name"] == "Docs bucket"

    @pytest.mark.anyio
    async def test_create_returns_201_and_parses_the_body(self, admin_client, mock_service):
        source = MockSyncSource()
        mock_service.create_source = AsyncMock(return_value=_read_model(source))

        response = await admin_client.post(
            SOURCES_URL,
            json={"name": "Docs bucket", "connector_type": "s3", "config": dict(S3_CONFIG)},
        )

        assert response.status_code == 201
        sent = mock_service.create_source.call_args.args[0]
        assert sent.name == "Docs bucket"
        assert sent.connector_type == "s3"
        assert sent.config["bucket"] == "docs"

    @pytest.mark.anyio
    async def test_update_returns_200_and_passes_id_and_fields(self, admin_client, mock_service):
        source = MockSyncSource()
        mock_service.update_source = AsyncMock(return_value=_read_model(source))

        response = await admin_client.patch(f"{SOURCES_URL}/{source.id}", json={"name": "Renamed"})

        assert response.status_code == 200
        source_id, update = mock_service.update_source.call_args.args
        assert source_id == str(source.id)
        assert update.name == "Renamed"

    @pytest.mark.anyio
    async def test_delete_returns_204_with_empty_body(self, admin_client, mock_service):
        mock_service.delete_source = AsyncMock()
        source_id = uuid4()

        response = await admin_client.delete(f"{SOURCES_URL}/{source_id}")

        assert response.status_code == 204
        assert response.content == b""
        mock_service.delete_source.assert_awaited_once_with(str(source_id))

    @pytest.mark.anyio
    async def test_trigger_shapes_the_sync_response_from_the_log(self, admin_client, mock_service):
        sync_log = MagicMock(id=uuid4())
        mock_service.trigger_sync = AsyncMock(return_value=sync_log)
        source_id = uuid4()

        response = await admin_client.post(f"{SOURCES_URL}/{source_id}/trigger")

        assert response.status_code == 200
        payload = response.json()
        assert payload["id"] == str(sync_log.id)
        assert payload["status"] == "running"
        assert str(source_id) in payload["message"]

    @pytest.mark.anyio
    async def test_trigger_missing_source_returns_404_error_envelope(
        self, admin_client, mock_service
    ):
        mock_service.trigger_sync = AsyncMock(
            side_effect=NotFoundError(message="Sync source not found", details={"source_id": "x"})
        )

        response = await admin_client.post(f"{SOURCES_URL}/{uuid4()}/trigger")

        assert response.status_code == 404
        envelope = response.json()["error"]
        assert set(envelope) == {"code", "message", "details"}
        assert envelope["message"] == "Sync source not found"

    @pytest.mark.anyio
    async def test_create_bad_request_returns_400_error_envelope(self, admin_client, mock_service):
        mock_service.create_source = AsyncMock(
            side_effect=BadRequestError(message="Unknown connector type: carrier-pigeon")
        )

        response = await admin_client.post(
            SOURCES_URL, json={"name": "x", "connector_type": "carrier-pigeon", "config": {}}
        )

        assert response.status_code == 400
        envelope = response.json()["error"]
        assert set(envelope) == {"code", "message", "details"}

    @pytest.mark.anyio
    async def test_list_connectors_keeps_the_secret_flags(self, admin_client, mock_service):
        from app.services.sync_source import SyncSourceService

        mock_service.list_connectors = MagicMock(return_value=SyncSourceService.list_connectors())

        response = await admin_client.get(CONNECTORS_URL)

        assert response.status_code == 200
        s3 = next(item for item in response.json()["items"] if item["type"] == "s3")
        for field in S3_SECRETS:
            assert s3["config_schema"][field]["secret"] is True


class TestEndToEndCredentialFlow:
    """The real service under the route - only the repository is patched."""

    @pytest.mark.anyio
    async def test_create_over_http_encrypts_at_rest_and_masks_the_response(
        self, real_service_admin_client
    ):
        created_row = {}

        async def capture_create(db, **kwargs):
            created_row.update(kwargs)
            return MockSyncSource(config=kwargs["config"])

        with patch("app.services.sync_source.sync_source_repo") as repo:
            repo.create = AsyncMock(side_effect=capture_create)

            response = await real_service_admin_client.post(
                SOURCES_URL,
                json={"name": "Docs bucket", "connector_type": "s3", "config": dict(S3_CONFIG)},
            )

        assert response.status_code == 201
        body_config = response.json()["config"]
        for field in S3_SECRETS:
            assert body_config[field] == _SECRET_MASK, f"{field} leaked through the HTTP response"
            assert is_encrypted(created_row["config"][field]), (
                f"{field} hit the repository in plaintext"
            )
        assert body_config["bucket"] == "docs"

    @pytest.mark.anyio
    async def test_collection_filter_reaches_the_repository(self, real_service_admin_client):
        with patch("app.services.sync_source.sync_source_repo") as repo:
            repo.get_all = AsyncMock(return_value=[])

            response = await real_service_admin_client.get(f"{SOURCES_URL}?collection_name=kb-two")

        assert response.status_code == 200
        assert repo.get_all.call_args.kwargs["collection_name"] == "kb-two"
