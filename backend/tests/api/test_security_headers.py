# ruff: noqa: I001 - Imports structured for Jinja2 template conditionals
"""Tests for SecurityHeadersMiddleware installation and behavior."""

import pytest
from httpx import ASGITransport, AsyncClient
from fastapi import FastAPI

from app.core.middleware import SecurityHeadersMiddleware

pytestmark = pytest.mark.anyio


async def test_app_responses_carry_security_headers(client: AsyncClient):
    """The middleware is installed: every response carries the header set."""
    response = await client.get("/api/v1/health")

    assert "default-src 'self'" in response.headers["content-security-policy"]
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "strict-origin-when-cross-origin"
    assert "permissions-policy" in response.headers


async def test_no_hsts_outside_production(client: AsyncClient):
    """HSTS is only meaningful behind TLS; absent in the test environment."""
    response = await client.get("/api/v1/health")
    assert "strict-transport-security" not in response.headers


def _tiny_app(**middleware_kwargs) -> FastAPI:
    app = FastAPI()
    app.add_middleware(SecurityHeadersMiddleware, **middleware_kwargs)

    @app.get("/ping")
    def ping() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/admin/thing")
    def admin_thing() -> dict[str, str]:
        return {"status": "ok"}

    return app


async def test_hsts_set_when_enabled():
    """hsts=True (production wiring) adds Strict-Transport-Security."""
    transport = ASGITransport(app=_tiny_app(hsts=True))
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.get("/ping")

    assert response.headers["strict-transport-security"] == "max-age=31536000; includeSubDomains"


async def test_admin_paths_excluded():
    """/admin is excluded so the admin UI's own asset policy is untouched."""
    transport = ASGITransport(app=_tiny_app())
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.get("/admin/thing")

    assert "content-security-policy" not in response.headers
