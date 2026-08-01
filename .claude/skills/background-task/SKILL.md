---
name: background-task
description: Add or modify work that runs outside the request/response cycle — emails, document ingestion, webhooks, cleanups, scheduled jobs. Use when something is slow or fire-and-forget, or when adding a periodic/cron task. This project's queue is taskiq.
---

# Background Tasks (taskiq)

Tasks live in `backend/app/worker/tasks/` (e.g. `email_tasks.py`, `rag_tasks.py`, `cleanup_tasks.py`). The app uses **taskiq**. An in-process fallback (`worker/background/`) exists for trivial cases.

## When to use a task vs. inline

- **Task:** anything slow, retryable, or fire-and-forget — sending email, ingesting/embedding documents, calling slow external APIs, periodic cleanups, materialized-view refreshes.
- **Inline:** fast, transactional work that the response depends on.

## Add a task

1. **Define it** in `backend/app/worker/tasks/<area>.py`:
   ```python
   from app.worker.taskiq_app import broker

   @broker.task
   async def send_welcome_email(user_id: str) -> dict: ...
   ```
   Enqueue: `await send_welcome_email.kiq(user_id)`.

2. **Call it from a service** (not from the route directly) — keep business logic in `services/`, enqueue at the end of the unit of work.

3. **Schedule it (optional):**
   append to `SCHEDULES` in `tasks/schedules.py` (run `make taskiq-scheduler`).

4. **Run / verify:**
   `make taskiq-worker` (+ `make taskiq-scheduler` for schedules).

## Rules

- Tasks take **serializable args** (ids, primitives) — not ORM objects or sessions. Re-fetch inside the task with a fresh session.
- Make tasks **idempotent** where possible (safe to retry).
- Keep heavy imports inside the task function to keep the API import-light.
- See `docs/howto/add-background-task.md` for the full walkthrough.
