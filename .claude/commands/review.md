---
description: Review code changes against project conventions
---

Review all staged and unstaged changes in the current branch. Verify against the
authoritative sources — the diff, the neighboring code, the actual imports — never
against a briefing the implementing agent already consumed.

**Architecture (backend):**
- Routes only call services, never repositories
- Services raise domain exceptions (NotFoundError, AlreadyExistsError, etc.), not HTTP exceptions
- Repositories use `db.flush()` + `db.refresh()`, never `db.commit()`
- DI uses Annotated aliases from `deps.py` (CurrentUser, *Svc), not raw `Depends()` in signatures

**The proxy hop (any REST contract change):**
- Every REST call from the browser goes through a handler in `frontend/src/app/api/**`;
  the chat WebSocket (`/api/v1/ws/agent`) is the one exception
- A changed route, shape, or status code must name its proxy handler, its client
  function in `frontend/src/lib/`, and the caller that consumes it — all moving in this
  change. Backend-only is not done.

**Schemas & Types:**
- Separate Create/Update/Read/List Pydantic models
- Type hints on all function signatures; modern syntax (`str | None`)
- Route return type is `-> Any`

**Frontend:**
- Server Components by default; `"use client"` only where interactivity requires it
- Components consume `frontend/src/lib/` clients, never raw `fetch`
- User-facing copy through `next-intl`, never hardcoded

**Code Quality:**
- No debug code (print, commented-out code, TODO without issue reference)
- No security issues (SQL injection, exposed secrets, missing auth)
- Imports ordered: stdlib → third-party → local

**Validation:**
1. `make lint` (backend changes)
2. `make test` (backend changes) / `make frontend-test` (frontend changes)

Provide findings with specific file:line references and suggest fixes. If the changes
ran outside a governance session, say so — the `Stop` hook will block turn-end until
the envelope is repaired.
