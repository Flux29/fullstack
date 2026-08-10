---
name: pytest-suite
description: Write or extend the backend test suite following this project's conventions. Use when adding tests for a new service/route/repository, when coverage is missing, or when asked to test a feature. Knows the mocked-session + httpx AsyncClient setup so tests run with no database. Runs inside the gov-change envelope; the registered validators are backend-unit and backend-coverage.
---

# Backend Tests (pytest + anyio)

Tests live in `backend/tests/` as **flat files named by surface** — `test_services.py`,
`test_repositories.py`, `test_agents.py`, `test_services_conversation.py` — extend the
file for the surface you touched rather than inventing a mirrored directory tree. The
suite runs with **no real database**: `conftest.py` overrides `get_db_session` with an
`AsyncMock` via FastAPI `dependency_overrides`.

Testing existing code is a governed change like any other: open with `gov-change`
GOV-OPEN, close with GOV-CLOSE. The registered validators are `backend-unit`
(`make test`) and `backend-coverage` (`make test-cov`) — the coverage floor is a
ratchet; never lower it to make a session pass.

## Key fixtures (`tests/conftest.py`)

- `client` — `httpx.AsyncClient` over `ASGITransport(app=app)` (use this, **not** Starlette `TestClient`)
- `mock_db_session` — `AsyncMock` standing in for `AsyncSession`
- `mock_redis` (when Redis is enabled), `api_key_headers` (when API keys are enabled)

## Patterns

**Async tests** use anyio, not `@pytest.mark.asyncio`:
```python
import pytest

pytestmark = pytest.mark.anyio  # at module top, or mark per-test
```

**Service test** — stub what the repo/session returns, assert behavior + exceptions:
```python
async def test_get_user_not_found_raises(monkeypatch, mock_db_session):
    service = UserService(mock_db_session)
    monkeypatch.setattr(user_repo, "get_by_id", AsyncMock(return_value=None))
    with pytest.raises(NotFoundError):
        await service.get_by_id(UUID("00000000-0000-0000-0000-000000000000"))
```

**API test** — drive the route through `client`, override auth deps with a mock user:
```python
async def test_create_user_returns_201(client: AsyncClient):
    app.dependency_overrides[get_current_user] = lambda: mock_user
    resp = await client.post("/api/v1/users", json={"email": "a@b.com", "password": "secret123"})
    assert resp.status_code == 201
```

## Naming & rules

- `test_<action>_<scenario>_<expected_result>` (e.g. `test_create_user_with_duplicate_email_raises_already_exists_error`)
- One behavior per test; plain `assert` (pytest rewrites it)
- Use factory fixtures for data, not raw dicts; each test independent
- Clear `app.dependency_overrides` after the test (the `client` fixture already does this for the DB/Redis overrides)

## Run

```bash
cd backend && uv run pytest                 # all
uv run pytest tests/test_services.py -v      # one surface
uv run pytest --cov=app                      # coverage
```

After writing tests, run `uv run ruff check . --fix && uv run ruff format .` and confirm `uv run pytest` is green.
