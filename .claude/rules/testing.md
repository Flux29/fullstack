# Testing

The backend suite runs with **no real database**: `tests/conftest.py` overrides
`get_db_session` with an `AsyncMock` through FastAPI `dependency_overrides`. Async tests
use **anyio, not pytest-asyncio**. If you write `@pytest.mark.asyncio` or a fixture that
expects a live session, you are writing against a different project.

## Running Tests

```bash
cd backend
uv run pytest                              # all tests
uv run pytest tests/test_services.py -v    # single file
uv run pytest -k "test_name" -v            # by name
uv run pytest --cov=app                    # with coverage
```

## Structure

- Tests live in `backend/tests/`, flat files named by surface: `test_services.py`,
  `test_repositories.py`, `test_agents.py`, `test_commands.py`, `test_core.py`
- Shared fixtures in `tests/conftest.py`

## Key fixtures (`tests/conftest.py`)

- `client` — `httpx.AsyncClient` over `ASGITransport(app=app)`; use this, **never**
  Starlette `TestClient`
- `mock_db_session` — `AsyncMock` standing in for `AsyncSession`
- `mock_redis` — `MagicMock(spec=RedisClient)` with async method mocks
- `anyio_backend` — pins anyio to asyncio (what uvicorn uses)

## Async Tests (anyio)

```python
import pytest

pytestmark = pytest.mark.anyio  # module-wide, or mark per-test


async def test_get_user_by_id(mock_db_session):
    ...
```

## Service Tests

Stub what the repo returns; assert behavior and domain exceptions:

```python
async def test_get_user_not_found_raises(monkeypatch, mock_db_session):
    service = UserService(mock_db_session)
    monkeypatch.setattr(user_repo, "get_by_id", AsyncMock(return_value=None))
    with pytest.raises(NotFoundError):
        await service.get_by_id(UUID("00000000-0000-0000-0000-000000000000"))
```

## API Tests

Drive routes through `client`; override auth deps with a mock user:

```python
async def test_create_user_returns_201(client: AsyncClient):
    app.dependency_overrides[get_current_user] = lambda: mock_user
    resp = await client.post("/api/v1/users", json={"email": "a@b.com", "password": "secret123"})
    assert resp.status_code == 201
```

Clear `app.dependency_overrides` after the test — the `client` fixture already restores
the DB/Redis overrides it owns.

## Naming

```python
# test_<action>_<scenario>_<expected_result>
def test_create_user_with_duplicate_email_raises_already_exists_error(): ...
def test_get_conversation_not_found_raises_not_found_error(): ...
def test_list_conversations_returns_only_user_owned(): ...
```

## Rules

- Each test is independent — no shared mutable state
- Use plain `assert` (pytest rewrites for detailed output)
- One behavior per test (multiple asserts are fine if they test one behavior)
- Factory fixtures for test data, not raw dicts
- A model change without a migration still passes this suite (the session is mocked)
  and breaks on real Postgres — chain the `migrations` validator for schema changes
