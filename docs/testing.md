# Testing Guide

## Running Tests

`make test` and `make test-cov` are the registered validators; the commands below are for
iterating on one file or one case.

```bash
# Run all tests
uv run --directory backend pytest

# Run with coverage
uv run --directory backend pytest --cov=app --cov-report=term-missing

# Run a specific test file
uv run --directory backend pytest tests/api/test_health.py -v

# Run a specific test
uv run --directory backend pytest tests/api/test_health.py::test_health_check -v

# Run tests whose name matches
uv run --directory backend pytest -k "conversation" -v

# Stop on first failure
uv run --directory backend pytest -x
```

## Test Structure

```
tests/
├── conftest.py              # Shared fixtures
├── api/                     # Route-level tests, driven through the async client
│   ├── test_health.py
│   ├── test_auth.py
│   └── ...
├── test_services.py         # Everything else is flat, named by the surface it exercises
├── test_repositories.py
├── test_agents.py
├── test_core.py
└── live/                    # Opt-in, hits real external accounts; never part of a normal run
    └── test_google_workspace_live.py
```

There is no `unit/` or `integration/` split: outside `api/`, files are flat and named by
surface.

## Key Fixtures (`conftest.py`)

The suite runs against **no real database and no real Redis**, and async tests use
**anyio**, not pytest-asyncio. Conventions for writing tests live in
`.claude/rules/testing.md`; this section only describes what the fixtures are.

```python
# Async HTTP client over the ASGI app — NOT Starlette's TestClient.
# Registers the mocked Redis and DB overrides, and clears them afterwards.
@pytest.fixture
async def client(mock_redis, mock_db_session) -> AsyncGenerator[AsyncClient, None]:
    ...

# An AsyncMock standing in for AsyncSession: execute, commit, rollback, close.
@pytest.fixture
async def mock_db_session() -> AsyncGenerator[AsyncMock, None]:
    ...

# MagicMock(spec=RedisClient) with async method mocks.
@pytest.fixture
def mock_redis() -> MagicMock:
    ...

# Headers carrying a valid service API key.
@pytest.fixture
def api_key_headers() -> dict[str, str]:
    ...
```

There is no `auth_client` fixture. For an authenticated user, override the auth dependency
with a mock user via `app.dependency_overrides` and clear it after the test — see
`tests/api/test_auth.py` and `tests/api/test_users.py`.

## Writing Tests

Mark the module `pytestmark = pytest.mark.anyio` (or mark each test), and make every test
that touches the client or a service `async`.

### API Endpoint Test
```python
import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.anyio


async def test_health_check(client: AsyncClient):
    response = await client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json()["status"] == "healthy"
```

### Service Test

Stub what the repository returns; assert behaviour and domain exceptions.

```python
async def test_get_item_not_found_raises(monkeypatch, mock_db_session):
    service = ItemService(mock_db_session)
    monkeypatch.setattr(item_repo, "get_by_id", AsyncMock(return_value=None))
    with pytest.raises(NotFoundError):
        await service.get_by_id(uuid4())
```

### Test with Authentication
```python
async def test_protected_endpoint(client: AsyncClient):
    app.dependency_overrides[get_current_user] = lambda: mock_user
    try:
        response = await client.get("/api/v1/users/me")
        assert response.status_code == 200
    finally:
        app.dependency_overrides.clear()
```

## Frontend Tests

`make frontend-test` is the registered validator — it runs lint, type-check, and vitest with
coverage in one pass. The individual scripts are for iterating:

```bash
cd frontend

# Unit tests, watch mode (vitest watches by default)
bun run test

# Unit tests, one shot
bun run test:run

# E2E — needs a running stack
bun run test:e2e

# E2E in headed mode (see the browser)
bun run test:e2e:headed
```

## Test Database

Tests don't hit a real database. The `client` fixture in `tests/conftest.py` overrides
`get_db_session` with a mocked async session (`AsyncMock`) via FastAPI's
`app.dependency_overrides`, so the suite runs fast and needs no Postgres container:

- `mock_db_session` — an `AsyncMock` standing in for `AsyncSession` (`execute`, `commit`, `rollback`, `close`)
- Overrides are registered before each test and cleared afterwards
- Assert against the mock's calls, or stub `execute(...)` return values for the path under test

For tests that need to exercise real SQL, instantiate your own async engine/session
inside the test rather than relying on a shared fixture.
