# How to: Add a Background Task

## Overview

Background tasks run asynchronously outside the request-response cycle. Your project uses **taskiq** as the task queue.

## Step-by-Step

### 1. Create the task
```python
# app/worker/tasks/notifications.py
from app.worker.taskiq_app import broker


@broker.task
async def send_notification(user_id: str, message: str) -> dict:
    """Send a notification to a user."""
    # Your async logic here
    print(f"Sending to {user_id}: {message}")
    return {"status": "sent", "user_id": user_id}
```

### 2. Call it from your API

```python
# In any route or service:
from app.worker.tasks.notifications import send_notification

# Fire and forget
await send_notification.kiq("user_123", "Your order is ready!")
```

### 3. Add scheduling (optional)
In `tasks/schedules.py`, add to `SCHEDULES`:
```python
SCHEDULES.append({
    "task": "app.worker.tasks.notifications:send_notification",
    "cron": "0 9 * * *",  # Daily at 9 AM
    "args": ["broadcast", "Daily digest"],
})
```

### 4. Run the worker

```bash
make taskiq-worker    # Start worker
make taskiq-scheduler # Start scheduler (for cron jobs)
```
