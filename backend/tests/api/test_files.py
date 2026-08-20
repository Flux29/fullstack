# ruff: noqa: I001 - Imports structured for Jinja2 template conditionals
"""Tests for file serving routes — security headers on downloads."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.deps import get_current_user, get_file_upload_service
from app.core.config import settings
from app.main import app
from app.api.deps import get_redis
from app.api.deps import get_db_session

pytestmark = pytest.mark.anyio


class MockUser:
    """Mock user for testing."""

    def __init__(self):
        self.id = uuid4()
        self.email = "test@example.com"
        self.is_active = True
        self.role = "user"


class MockChatFile:
    """Mock ChatFile row for testing."""

    def __init__(self, filename: str, mime_type: str):
        self.id = uuid4()
        self.filename = filename
        self.mime_type = mime_type
        self.size = 12
        self.file_type = "text"
        self.storage_path = f"user/{filename}"
        self.user_id = uuid4()
        self.created_at = datetime.now(UTC)


@pytest.fixture
def mock_user() -> MockUser:
    return MockUser()


def _make_file_service(chat_file: MockChatFile, path: str) -> MagicMock:
    service = MagicMock()
    service.get_user_file = AsyncMock(return_value=chat_file)
    service.get_file_path = MagicMock(return_value=path)
    return service


@pytest.fixture
async def make_client(mock_user: MockUser, mock_redis: MagicMock, mock_db_session):
    """Factory: client with the file service serving one mocked file."""
    clients: list[AsyncClient] = []

    async def _make(chat_file: MockChatFile, path: str) -> AsyncClient:
        app.dependency_overrides[get_current_user] = lambda: mock_user
        app.dependency_overrides[get_file_upload_service] = lambda: _make_file_service(
            chat_file, path
        )
        app.dependency_overrides[get_redis] = lambda: mock_redis
        app.dependency_overrides[get_db_session] = lambda: mock_db_session
        ac = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
        clients.append(ac)
        return ac

    yield _make

    for ac in clients:
        await ac.aclose()
    app.dependency_overrides.clear()


async def test_download_html_file_is_sandboxed(make_client, tmp_path):
    """Uploaded HTML must not be able to run scripts on the app origin."""
    file_path = tmp_path / "page.html"
    file_path.write_text("<script>alert(1)</script>")
    chat_file = MockChatFile("page.html", "text/html")
    client = await make_client(chat_file, str(file_path))

    response = await client.get(f"{settings.API_V1_STR}/files/{chat_file.id}")

    assert response.status_code == 200
    csp = response.headers["content-security-policy"]
    assert "sandbox" in csp
    assert "frame-ancestors 'self'" in csp
    assert response.headers["x-content-type-options"] == "nosniff"


async def test_download_svg_file_is_sandboxed(make_client, tmp_path):
    """SVG is active content too — scripts inside it must be blocked."""
    file_path = tmp_path / "img.svg"
    file_path.write_text("<svg xmlns='http://www.w3.org/2000/svg'/>")
    chat_file = MockChatFile("img.svg", "image/svg+xml")
    client = await make_client(chat_file, str(file_path))

    response = await client.get(f"{settings.API_V1_STR}/files/{chat_file.id}")

    assert response.status_code == 200
    assert "sandbox" in response.headers["content-security-policy"]


async def test_download_plain_text_not_sandboxed_but_framable_same_origin(make_client, tmp_path):
    """Inert types keep the preview-friendly policy without sandbox."""
    file_path = tmp_path / "notes.txt"
    file_path.write_text("hello world")
    chat_file = MockChatFile("notes.txt", "text/plain")
    client = await make_client(chat_file, str(file_path))

    response = await client.get(f"{settings.API_V1_STR}/files/{chat_file.id}")

    assert response.status_code == 200
    assert response.headers["content-security-policy"] == "frame-ancestors 'self'"
    assert response.headers["x-frame-options"] == "SAMEORIGIN"
    assert response.headers["x-content-type-options"] == "nosniff"


async def test_download_disposition_attachment_forces_download(make_client, tmp_path):
    """?disposition=attachment still forces the save dialog."""
    file_path = tmp_path / "page.html"
    file_path.write_text("<p>hi</p>")
    chat_file = MockChatFile("page.html", "text/html")
    client = await make_client(chat_file, str(file_path))

    response = await client.get(
        f"{settings.API_V1_STR}/files/{chat_file.id}", params={"disposition": "attachment"}
    )

    assert response.status_code == 200
    assert response.headers["content-disposition"].startswith("attachment")
