"""Taskiq scheduled tasks (cron-like)."""

from app.worker.taskiq_app import broker
from app.worker.tasks.rag_tasks import check_scheduled_syncs


@broker.task(schedule=[{"cron": "* * * * *"}])
async def scheduled_rag_sync_check() -> dict:
    """Scheduled task: check for connector sources due for sync and dispatch."""
    result = await check_scheduled_syncs.kiq()
    return {"scheduled": True, "task_id": str(result.task_id)}
