"""Tests for the RAG ingestion status SSE stream — GET /api/v1/rag/status/stream.

This route's dependency wiring *is* the feature. ``RAGStatusService`` requires the
shared lifespan Redis client at construction time, and a ``get_rag_status_service``
that builds it with no arguments raises ``TypeError`` -> 500 on every request. The
browser's EventSource reconnects silently on a failed stream, so that regression is
invisible from the UI and shows up only as status updates that never arrive.

These tests therefore drive the REAL ``get_rag_status_service`` and fake only Redis
pub/sub beneath it. Overriding the service itself would pass against exactly the bug
it is here to catch.
"""

from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.deps import get_current_user, get_db_session, get_redis
from app.clients.redis import RedisClient
from app.core.config import settings
from app.main import app

STREAM_URL = f"{settings.API_V1_STR}/rag/status/stream"
CHANNEL = "rag_status"

# What the worker publishes (app/worker/tasks/rag_tasks.py::_notify_ws), as redis-py
# hands it to a subscriber: a subscribe confirmation first, then the payloads.
DONE_PAYLOAD = '{"document_id": "doc-1", "status": "done", "filename": "handbook.pdf"}'
FAILED_PAYLOAD = '{"document_id": "doc-2", "status": "failed", "filename": "broken.pdf"}'
MESSAGES = [
    {"type": "subscribe", "channel": CHANNEL, "data": 1},
    # bytes and str both occur: RedisClient sets decode_responses=True, the worker's
    # own connection does not. The service must handle either.
    {"type": "message", "channel": CHANNEL, "data": DONE_PAYLOAD.encode()},
    {"type": "message", "channel": CHANNEL, "data": FAILED_PAYLOAD},
]


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


class FakePubSub:
    """Stand-in for ``redis.asyncio.client.PubSub``.

    ``listen()`` yields a fixed script and then stops, so the route's async generator
    terminates and the response completes — a real pub/sub connection would stream
    forever and hang the test client.
    """

    def __init__(self, messages):
        self._messages = messages
        self.subscribed: list[str] = []
        self.unsubscribed: list[str] = []

    async def subscribe(self, channel: str) -> None:
        self.subscribed.append(channel)

    async def unsubscribe(self, channel: str) -> None:
        self.unsubscribed.append(channel)

    async def listen(self):
        for message in self._messages:
            yield message


class FakeRawRedis:
    """The ``aioredis.Redis`` surface the service actually uses."""

    def __init__(self, pubsub: FakePubSub):
        self._pubsub = pubsub
        self.pubsub_calls = 0

    def pubsub(self) -> FakePubSub:
        self.pubsub_calls += 1
        return self._pubsub


@pytest.fixture
def fake_pubsub() -> FakePubSub:
    return FakePubSub(MESSAGES)


@pytest.fixture
def fake_redis_client(fake_pubsub: FakePubSub) -> MagicMock:
    """A RedisClient whose ``.raw`` is the fake — what lifespan state provides."""
    client = MagicMock(spec=RedisClient)
    client.raw = FakeRawRedis(fake_pubsub)
    return client


@pytest.fixture
async def admin_client(fake_redis_client, mock_db_session) -> AsyncClient:
    """Admin client with the REAL RAGStatusService wiring in place."""
    app.dependency_overrides[get_current_user] = lambda: MockUser(role="admin")
    app.dependency_overrides[get_redis] = lambda: fake_redis_client
    app.dependency_overrides[get_db_session] = lambda: mock_db_session

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()


@pytest.fixture
async def non_admin_client(fake_redis_client, mock_db_session) -> AsyncClient:
    """Plain user - RoleChecker must refuse the stream."""
    app.dependency_overrides[get_current_user] = lambda: MockUser(role="user")
    app.dependency_overrides[get_redis] = lambda: fake_redis_client
    app.dependency_overrides[get_db_session] = lambda: mock_db_session

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()


class TestAuthGating:
    @pytest.mark.anyio
    async def test_unauthenticated_stream_returns_401_with_challenge(self, client: AsyncClient):
        response = await client.get(STREAM_URL)
        assert response.status_code == 401
        assert "WWW-Authenticate" in response.headers

    @pytest.mark.anyio
    async def test_non_admin_stream_returns_403(self, non_admin_client: AsyncClient):
        response = await non_admin_client.get(STREAM_URL)
        assert response.status_code == 403


class TestStatusStream:
    @pytest.mark.anyio
    async def test_admin_stream_returns_200_as_event_stream(self, admin_client: AsyncClient):
        """The regression guard: with an un-injected client this is a 500, not a 200."""
        response = await admin_client.get(STREAM_URL)

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")

    @pytest.mark.anyio
    async def test_stream_emits_published_status_events(self, admin_client: AsyncClient):
        response = await admin_client.get(STREAM_URL)
        body = response.text

        assert response.status_code == 200
        # Both payloads arrive, decoded, tagged as `status` events and id-sequenced.
        assert f"data: {DONE_PAYLOAD}" in body
        assert f"data: {FAILED_PAYLOAD}" in body
        assert "event: status" in body
        assert "id: 1" in body
        assert "id: 2" in body

    @pytest.mark.anyio
    async def test_stream_ignores_non_message_pubsub_frames(self, admin_client: AsyncClient):
        """The subscribe confirmation must not be forwarded as a status event."""
        response = await admin_client.get(STREAM_URL)

        assert response.text.count("event: status") == 2
        assert "data: 1\n" not in response.text

    @pytest.mark.anyio
    async def test_stream_uses_the_shared_lifespan_client(
        self, admin_client: AsyncClient, fake_redis_client: MagicMock
    ):
        """pub/sub is opened on the injected client, not a connection of its own."""
        await admin_client.get(STREAM_URL)

        assert fake_redis_client.raw.pubsub_calls == 1

    @pytest.mark.anyio
    async def test_stream_subscribes_and_unsubscribes_the_channel(
        self, admin_client: AsyncClient, fake_pubsub: FakePubSub
    ):
        await admin_client.get(STREAM_URL)

        assert fake_pubsub.subscribed == [CHANNEL]
        assert fake_pubsub.unsubscribed == [CHANNEL]
