# ruff: noqa: I001 - Imports structured for Jinja2 template conditionals
"""Tests for credential-endpoint rate limiting."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.deps import get_user_service
from app.clients.redis import RedisClient
from app.core.config import settings
from app.main import app
from app.api.deps import get_redis
from app.api.deps import get_db_session

pytestmark = pytest.mark.anyio


class MockUser:
    def __init__(self):
        self.id = uuid4()
        self.email = "test@example.com"
        self.full_name = "Test User"
        self.is_active = True
        self.role = "user"
        self.hashed_password = "hashed"
        self.avatar_url = None
        self.oauth_provider = None
        self.created_at = datetime.now(UTC)
        self.updated_at = datetime.now(UTC)


@pytest.fixture
def mock_user_service() -> MagicMock:
    user = MockUser()
    service = MagicMock()
    service.authenticate = AsyncMock(return_value=user)
    service.register = AsyncMock(return_value=user)
    service.issue_password_reset_token = AsyncMock(return_value=None)
    service.issue_magic_link_token = AsyncMock(return_value=None)
    return service


@pytest.fixture
async def client_with_limits(mock_user_service: MagicMock, mock_redis: MagicMock, mock_db_session):
    app.dependency_overrides[get_user_service] = lambda: mock_user_service
    app.dependency_overrides[get_redis] = lambda: mock_redis
    app.dependency_overrides[get_db_session] = lambda: mock_db_session

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()


async def test_login_within_limit_succeeds(client_with_limits: AsyncClient, mock_redis: MagicMock):
    mock_redis.incr_with_ttl.return_value = 1
    resp = await client_with_limits.post(
        f"{settings.API_V1_STR}/auth/login",
        data={"username": "test@example.com", "password": "pw"},
    )
    assert resp.status_code == 200


async def test_login_over_ip_limit_returns_429(
    client_with_limits: AsyncClient, mock_redis: MagicMock
):
    mock_redis.incr_with_ttl.return_value = 11
    resp = await client_with_limits.post(
        f"{settings.API_V1_STR}/auth/login",
        data={"username": "test@example.com", "password": "pw"},
    )
    assert resp.status_code == 429
    assert resp.json()["error"]["code"] == "RATE_LIMIT_EXCEEDED"


async def test_login_over_account_limit_returns_429(
    client_with_limits: AsyncClient, mock_redis: MagicMock
):
    # IP counter fine (1), account counter above its halved allowance (6 > 5).
    mock_redis.incr_with_ttl.side_effect = [1, 6]
    resp = await client_with_limits.post(
        f"{settings.API_V1_STR}/auth/login",
        data={"username": "victim@example.com", "password": "pw"},
    )
    assert resp.status_code == 429


async def test_account_key_is_case_insensitive(
    client_with_limits: AsyncClient, mock_redis: MagicMock
):
    mock_redis.incr_with_ttl.side_effect = [1, 1]
    await client_with_limits.post(
        f"{settings.API_V1_STR}/auth/login",
        data={"username": "MiXeD@Example.COM", "password": "pw"},
    )
    account_key = mock_redis.incr_with_ttl.await_args_list[1].args[0]
    assert account_key == "ratelimit:login:acct:mixed@example.com"


async def test_forwarded_client_ip_is_the_key(
    client_with_limits: AsyncClient, mock_redis: MagicMock
):
    mock_redis.incr_with_ttl.side_effect = [1, 1]
    await client_with_limits.post(
        f"{settings.API_V1_STR}/auth/login",
        data={"username": "test@example.com", "password": "pw"},
        headers={"X-Forwarded-For": "203.0.113.7, 10.0.0.2"},
    )
    ip_key = mock_redis.incr_with_ttl.await_args_list[0].args[0]
    assert ip_key == "ratelimit:login:ip:203.0.113.7"


async def test_password_reset_over_limit_returns_429(
    client_with_limits: AsyncClient, mock_redis: MagicMock
):
    mock_redis.incr_with_ttl.return_value = 4
    resp = await client_with_limits.post(
        f"{settings.API_V1_STR}/auth/password-reset/request",
        json={"email": "test@example.com"},
    )
    assert resp.status_code == 429


async def test_magic_link_over_limit_returns_429(
    client_with_limits: AsyncClient, mock_redis: MagicMock
):
    mock_redis.incr_with_ttl.return_value = 4
    resp = await client_with_limits.post(
        f"{settings.API_V1_STR}/auth/magic-link/request",
        json={"email": "test@example.com"},
    )
    assert resp.status_code == 429


async def test_register_over_limit_returns_429(
    client_with_limits: AsyncClient, mock_redis: MagicMock
):
    mock_redis.incr_with_ttl.return_value = 6
    resp = await client_with_limits.post(
        f"{settings.API_V1_STR}/auth/register",
        json={"email": "new@example.com", "password": "password123"},
    )
    assert resp.status_code == 429


async def test_redis_unavailable_fails_open(client_with_limits: AsyncClient, mock_redis: MagicMock):
    mock_redis.incr_with_ttl.side_effect = ConnectionError("redis down")
    resp = await client_with_limits.post(
        f"{settings.API_V1_STR}/auth/login",
        data={"username": "test@example.com", "password": "pw"},
    )
    assert resp.status_code == 200


class TestIncrWithTtl:
    """RedisClient.incr_with_ttl starts the window only on the first hit."""

    async def test_first_increment_sets_ttl(self):
        client = RedisClient()
        client.client = MagicMock()
        client.client.incr = AsyncMock(return_value=1)
        client.client.expire = AsyncMock(return_value=True)

        count = await client.incr_with_ttl("k", 60)

        assert count == 1
        client.client.expire.assert_awaited_once_with("k", 60)

    async def test_later_increments_leave_ttl_alone(self):
        client = RedisClient()
        client.client = MagicMock()
        client.client.incr = AsyncMock(return_value=3)
        client.client.expire = AsyncMock(return_value=True)

        count = await client.incr_with_ttl("k", 60)

        assert count == 3
        client.client.expire.assert_not_awaited()
