# ruff: noqa: I001 - Imports structured for Jinja2 template conditionals
"""Tests for authentication routes."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

ServiceMock = AsyncMock
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.deps import get_current_user, get_user_service
from app.core.config import settings
from app.core.exceptions import AlreadyExistsError, AuthenticationError
from app.main import app
from app.api.deps import get_redis
from app.api.deps import get_db_session


class MockUser:
    """Mock user for testing."""

    def __init__(
        self,
        id=None,
        email="test@example.com",
        full_name="Test User",
        is_active=True,
        role="user",
    ):
        self.id = id or uuid4()
        self.email = email
        self.full_name = full_name
        self.is_active = is_active
        self.role = role
        self.hashed_password = "hashed"
        self.avatar_url = None
        self.oauth_provider = None
        self.created_at = datetime.now(UTC)
        self.updated_at = datetime.now(UTC)


@pytest.fixture
def mock_user() -> MockUser:
    """Create a mock user."""
    return MockUser()


@pytest.fixture
def mock_user_service(mock_user: MockUser) -> MagicMock:
    """Create a mock user service."""
    service = MagicMock()
    service.authenticate = ServiceMock(return_value=mock_user)
    service.register = ServiceMock(return_value=mock_user)
    service.get_by_id = ServiceMock(return_value=mock_user)
    service.get_by_email = ServiceMock(return_value=mock_user)
    return service


@pytest.fixture
async def client_with_mock_service(
    mock_user_service: MagicMock,
    mock_redis: MagicMock,
    mock_db_session,
) -> AsyncClient:
    """Client with mocked user service."""
    app.dependency_overrides[get_user_service] = lambda: mock_user_service
    app.dependency_overrides[get_redis] = lambda: mock_redis
    app.dependency_overrides[get_db_session] = lambda: mock_db_session

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        yield ac

    app.dependency_overrides.clear()


@pytest.mark.anyio
async def test_login_success(client_with_mock_service: AsyncClient):
    """Test successful login."""
    response = await client_with_mock_service.post(
        f"{settings.API_V1_STR}/auth/login",
        data={"username": "test@example.com", "password": "password123"},
    )
    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert "refresh_token" in data
    assert data["token_type"] == "bearer"


@pytest.mark.anyio
async def test_login_invalid_credentials(
    client_with_mock_service: AsyncClient,
    mock_user_service: MagicMock,
):
    """Test login with invalid credentials."""
    mock_user_service.authenticate = ServiceMock(
        side_effect=AuthenticationError(message="Invalid credentials")
    )

    response = await client_with_mock_service.post(
        f"{settings.API_V1_STR}/auth/login",
        data={"username": "test@example.com", "password": "wrongpassword"},
    )
    assert response.status_code == 401


@pytest.mark.anyio
async def test_register_success(client_with_mock_service: AsyncClient):
    """Test successful registration."""
    response = await client_with_mock_service.post(
        f"{settings.API_V1_STR}/auth/register",
        json={
            "email": "new@example.com",
            "password": "password123",
            "full_name": "New User",
        },
    )
    assert response.status_code == 201
    data = response.json()
    assert data["email"] == "test@example.com"  # From mock


@pytest.mark.anyio
async def test_register_role_in_body_never_reaches_the_service(
    client_with_mock_service: AsyncClient,
    mock_user_service: MagicMock,
):
    """A role field in the registration payload is dropped at the schema boundary."""
    response = await client_with_mock_service.post(
        f"{settings.API_V1_STR}/auth/register",
        json={
            "email": "attacker@example.com",
            "password": "password123",
            "role": "admin",
        },
    )
    assert response.status_code == 201
    user_in = mock_user_service.register.call_args.args[0]
    assert "role" not in user_in.model_dump()


@pytest.mark.anyio
async def test_register_duplicate_email(
    client_with_mock_service: AsyncClient,
    mock_user_service: MagicMock,
):
    """Test registration with duplicate email."""
    mock_user_service.register = ServiceMock(
        side_effect=AlreadyExistsError(message="Email already registered")
    )

    response = await client_with_mock_service.post(
        f"{settings.API_V1_STR}/auth/register",
        json={
            "email": "existing@example.com",
            "password": "password123",
            "full_name": "Test User",
        },
    )
    assert response.status_code == 409


@pytest.fixture
def mock_session_service() -> MagicMock:
    """Create a mock session service."""
    service = MagicMock()
    service.create_session = ServiceMock(return_value=None)
    return service


@pytest.fixture
async def oauth_client(
    mock_user_service: MagicMock,
    mock_session_service: MagicMock,
    mock_redis: MagicMock,
    mock_db_session,
) -> AsyncClient:
    """Client with user, session, redis, and db dependencies mocked for OAuth flows."""
    from app.api.deps import get_session_service

    app.dependency_overrides[get_user_service] = lambda: mock_user_service
    app.dependency_overrides[get_session_service] = lambda: mock_session_service
    app.dependency_overrides[get_redis] = lambda: mock_redis
    app.dependency_overrides[get_db_session] = lambda: mock_db_session

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        yield ac

    app.dependency_overrides.clear()


@pytest.mark.anyio
async def test_oauth_exchange_valid_code_returns_token_pair(
    oauth_client: AsyncClient,
    mock_user: MockUser,
    mock_redis: MagicMock,
    mock_session_service: MagicMock,
):
    """A stored single-use code yields tokens and a server-side session."""
    mock_redis.getdel = AsyncMock(return_value=str(mock_user.id))

    response = await oauth_client.post(
        f"{settings.API_V1_STR}/oauth/exchange",
        json={"code": "c" * 43},
    )

    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert "refresh_token" in data
    mock_redis.getdel.assert_awaited_once_with("oauth:login-code:" + "c" * 43)
    mock_session_service.create_session.assert_awaited_once()


@pytest.mark.anyio
async def test_oauth_exchange_unknown_code_returns_401(oauth_client: AsyncClient):
    """A code Redis has never seen (or already consumed) is rejected."""
    response = await oauth_client.post(
        f"{settings.API_V1_STR}/oauth/exchange",
        json={"code": "x" * 43},
    )
    assert response.status_code == 401


@pytest.mark.anyio
async def test_oauth_exchange_inactive_user_returns_401(
    oauth_client: AsyncClient,
    mock_user: MockUser,
    mock_redis: MagicMock,
):
    """A disabled account cannot complete sign-in even with a valid code."""
    mock_user.is_active = False
    mock_redis.getdel = AsyncMock(return_value=str(mock_user.id))

    response = await oauth_client.post(
        f"{settings.API_V1_STR}/oauth/exchange",
        json={"code": "c" * 43},
    )
    assert response.status_code == 401


@pytest.mark.anyio
async def test_google_callback_redirects_with_code_and_never_with_tokens(
    oauth_client: AsyncClient,
    mock_user: MockUser,
    mock_user_service: MagicMock,
    mock_redis: MagicMock,
):
    """The OAuth callback URL carries only an opaque code — no JWTs in the query."""
    from unittest.mock import patch as mock_patch

    from app.api.routes.v1 import oauth as oauth_module

    mock_user_service.get_or_create_oauth_user = ServiceMock(return_value=mock_user)
    google = MagicMock()
    google.authorize_access_token = AsyncMock(
        return_value={"userinfo": {"sub": "gid", "email": mock_user.email, "name": "Test"}}
    )

    with mock_patch.object(oauth_module.oauth, "google", google):
        response = await oauth_client.get(f"{settings.API_V1_STR}/oauth/google/callback")

    assert response.status_code in (302, 307)
    location = response.headers["location"]
    assert "/auth/callback?code=" in location
    assert "access_token" not in location
    assert "refresh_token" not in location
    # The code is stored single-use with a short TTL, keyed to the user id.
    args, kwargs = mock_redis.set.await_args
    assert args[0].startswith("oauth:login-code:")
    assert args[1] == str(mock_user.id)
    assert kwargs["ttl"] == 60


@pytest.mark.anyio
async def test_get_current_user(
    client_with_mock_service: AsyncClient,
    mock_user: MockUser,
    mock_user_service: MagicMock,
):
    """Test getting current user info."""
    # Override get_current_user to return mock user
    app.dependency_overrides[get_current_user] = lambda: mock_user

    response = await client_with_mock_service.get(
        f"{settings.API_V1_STR}/auth/me",
    )
    assert response.status_code == 200
    data = response.json()
    assert data["email"] == mock_user.email
