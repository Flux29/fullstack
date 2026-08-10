---
name: background-task
description: Add or modify work that runs outside the request/response cycle — document ingestion, connector syncs, webhooks, cleanups, scheduled jobs. Use when something is slow or fire-and-forget, or when adding a periodic/cron task. This project's queue is taskiq; the work runs inside the gov-change envelope.
---

# Background Tasks (taskiq)

Tasks live in `backend/app/worker/tasks/` — `rag_tasks.py` (ingestion and connector
sync) is the existing module; schedules live in `tasks/schedules.py`. The app uses
**taskiq** with the broker in `backend/app/worker/taskiq_app.py`. An in-process fallback
(`worker/background/`) exists for trivial cases.

This is a governed change: open with `gov-change` GOV-OPEN
(`PATHS="backend/app/worker"`), close with GOV-CLOSE. Expect `governance-impact` to
select `backend-unit`; the worker suite lives in `backend/tests/test_worker_taskiq.py`.

## When to use a task vs. inline

- **Task:** anything slow, retryable, or fire-and-forget — ingesting/embedding
  documents, syncing connectors, calling slow external APIs, periodic cleanups.
- **Inline:** fast, transactional work that the response depends on.

## Add a task

1. **Define it** in `backend/app/worker/tasks/` — extend `rag_tasks.py` when the work is
   RAG-shaped; otherwise add a module named by area:
   ```python
   from app.worker.taskiq_app import broker

   @broker.task
   async def send_welcome_email(user_id: str) -> dict: ...
   ```
   Enqueue: `await send_welcome_email.kiq(user_id)`.

2. **Call it from a service** (not from the route directly) — keep business logic in
   `services/`, enqueue at the end of the unit of work.

3. **Schedule it (optional):** append to `SCHEDULES` in `tasks/schedules.py`
   (run `make taskiq-scheduler`).

4. **Run / verify:** `make taskiq-worker` (+ `make taskiq-scheduler` for schedules), then
   extend `backend/tests/test_worker_taskiq.py` and run the `backend-unit` validator.

## Rules

- Tasks take **serializable args** (ids, primitives) — not ORM objects or sessions.
  Re-fetch inside the task with a fresh session.
- Make tasks **idempotent** where possible (safe to retry).
- Keep heavy imports inside the task function to keep the API import-light.
- See `docs/howto/add-background-task.md` for the full walkthrough.
