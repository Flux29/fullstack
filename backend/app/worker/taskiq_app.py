"""Taskiq application configuration."""

from taskiq import TaskiqEvents, TaskiqScheduler, TaskiqState
from taskiq.schedule_sources import LabelScheduleSource
from taskiq_redis import ListQueueBroker, RedisAsyncResultBackend

from app.core.config import settings

broker = ListQueueBroker(
    url=settings.TASKIQ_BROKER_URL,
    # ListQueueBroker waits indefinitely in BRPOP. redis-py 6.4 defaults to a
    # five-second read timeout, which would recycle an otherwise healthy worker.
    socket_timeout=None,
    socket_connect_timeout=5,
).with_result_backend(
    RedisAsyncResultBackend(
        redis_url=settings.TASKIQ_RESULT_BACKEND,
    )
)

scheduler = TaskiqScheduler(
    broker=broker,
    sources=[LabelScheduleSource(broker)],
)


@broker.on_event(TaskiqEvents.WORKER_STARTUP)
async def startup(_state: TaskiqState) -> None:
    pass


@broker.on_event(TaskiqEvents.WORKER_SHUTDOWN)
async def shutdown(_state: TaskiqState) -> None:
    from app.services.rag import close_embedding_service
    from app.worker.tasks.rag_tasks import close_rag_task_resources

    await close_rag_task_resources()
    await close_embedding_service()


# Direct Taskiq entrypoints must register every task. The scheduled wrapper
# imports its RAG dependency inside the task body, so these imports are safe in
# either application-first or worker-first module order.
import app.worker.tasks.rag_tasks
import app.worker.tasks.schedules  # noqa: F401
