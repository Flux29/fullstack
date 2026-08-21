"""Regression tests for the Taskiq worker: scheduler configuration (issue #92) and
the RAG status publish that feeds the admin SSE stream."""

import json
import sys

import pytest
from taskiq.schedule_sources import LabelScheduleSource

from app.core.config import settings
from app.worker.taskiq_app import scheduler
from app.worker.tasks import rag_tasks


class TestTaskiqScheduler:
    """The scheduler must receive ScheduleSource instances, not module paths."""

    def test_sources_are_schedule_source_instances(self):
        """A bare string source crashes startup: 'str' has no attribute 'startup'."""
        assert scheduler.sources
        for source in scheduler.sources:
            assert not isinstance(source, str)
            assert hasattr(source, "startup")

    def test_uses_label_schedule_source(self):
        """Schedules are declared via @broker.task(schedule=...) labels."""
        assert any(isinstance(s, LabelScheduleSource) for s in scheduler.sources)

    @pytest.mark.anyio
    async def test_scheduler_sources_start_and_list_schedules(self):
        """Each source starts cleanly and exposes its schedules without error."""
        for source in scheduler.sources:
            await source.startup()
            schedules = await source.get_schedules()
            assert isinstance(schedules, list)


class TestTaskRegistration:
    """Importing the broker module must register tasks for the worker process."""

    def test_importing_taskiq_app_imports_task_modules(self):
        """Without this side-effect import the worker discovers zero tasks."""
        assert "app.worker.tasks.schedules" in sys.modules


class FakeRedis:
    """Captures what _notify_ws publishes, and whether it cleaned up."""

    def __init__(self):
        self.published: list[tuple[str, str]] = []
        self.closed = False

    async def publish(self, channel: str, payload: str) -> int:
        self.published.append((channel, payload))
        return 1

    async def aclose(self) -> None:
        self.closed = True


@pytest.fixture
def fake_redis(monkeypatch) -> FakeRedis:
    """Swap aioredis.from_url for a recorder and hand back the captured URL."""
    fake = FakeRedis()
    fake.urls = []

    def from_url(url, *args, **kwargs):
        fake.urls.append(url)
        return fake

    monkeypatch.setattr(rag_tasks.aioredis, "from_url", from_url)
    return fake


class TestRagStatusPublish:
    """_notify_ws must authenticate, or RAG ingestion status never reaches the SSE stream.

    The publish is best-effort: its body is wrapped in a bare `except` that only logs.
    A connection built without REDIS_PASSWORD therefore fails Redis AUTH silently, and
    GET /api/v1/rag/status/stream stays empty while ingestion looks healthy. These
    tests pin the URL rather than the fact that *some* publish happened, because the
    swallowed failure is exactly what made the bug invisible.
    """

    @pytest.mark.anyio
    async def test_publish_uses_the_password_bearing_url(self, monkeypatch, fake_redis):
        """The regression guard: a hand-built URL drops the password and fails AUTH."""
        monkeypatch.setattr(settings, "REDIS_PASSWORD", "s3cr3t")
        monkeypatch.setattr(settings, "REDIS_HOST", "redis")
        monkeypatch.setattr(settings, "REDIS_PORT", 6379)
        monkeypatch.setattr(settings, "REDIS_DB", 0)

        await rag_tasks._notify_ws("doc-1", "done", "handbook.pdf")

        assert fake_redis.urls == ["redis://:s3cr3t@redis:6379/0"]
        assert fake_redis.published, "publish never ran - the connection step swallowed an error"

    @pytest.mark.anyio
    async def test_publish_url_tracks_settings_redis_url_exactly(self, monkeypatch, fake_redis):
        """Pin the source, not a copy of the format string, so the two cannot drift."""
        monkeypatch.setattr(settings, "REDIS_PASSWORD", "another-secret")
        monkeypatch.setattr(settings, "REDIS_DB", 4)

        await rag_tasks._notify_ws("doc-1", "done", "handbook.pdf")

        assert fake_redis.urls == [settings.REDIS_URL]

    @pytest.mark.anyio
    async def test_publish_omits_credentials_when_no_password_is_set(self, monkeypatch, fake_redis):
        """Local and dev Redis run unauthenticated; the URL must stay usable there."""
        monkeypatch.setattr(settings, "REDIS_PASSWORD", None)
        monkeypatch.setattr(settings, "REDIS_HOST", "localhost")
        monkeypatch.setattr(settings, "REDIS_PORT", 6379)
        monkeypatch.setattr(settings, "REDIS_DB", 0)

        await rag_tasks._notify_ws("doc-1", "done", "handbook.pdf")

        assert fake_redis.urls == ["redis://localhost:6379/0"]

    @pytest.mark.anyio
    async def test_publish_sends_the_status_payload_on_the_rag_status_channel(self, fake_redis):
        """The channel and payload the SSE stream consumes - see tests/api/test_rag_status_stream.py."""
        await rag_tasks._notify_ws("doc-42", "done", "handbook.pdf")

        assert len(fake_redis.published) == 1
        channel, payload = fake_redis.published[0]
        assert channel == "rag_status"
        assert json.loads(payload) == {
            "document_id": "doc-42",
            "status": "done",
            "filename": "handbook.pdf",
        }

    @pytest.mark.anyio
    async def test_publish_closes_its_connection(self, fake_redis):
        """One connection per notification, so it must not leak one per ingestion."""
        await rag_tasks._notify_ws("doc-1", "done", "handbook.pdf")

        assert fake_redis.closed is True

    @pytest.mark.anyio
    async def test_publish_failure_never_fails_the_ingestion(self, monkeypatch, caplog):
        """Best-effort by design: a Redis outage must not undo a completed ingestion."""

        def exploding_from_url(url, *args, **kwargs):
            raise ConnectionError("redis is down")

        monkeypatch.setattr(rag_tasks.aioredis, "from_url", exploding_from_url)

        await rag_tasks._notify_ws("doc-1", "done", "handbook.pdf")  # must not raise

        assert "Failed to send WS notification" in caplog.text
