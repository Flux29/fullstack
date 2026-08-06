"""Tests for core modules."""
# ruff: noqa: I001 - Imports structured for Jinja2 template conditionals

from pathlib import Path

from app.core.config import Settings, settings
from app.core.exceptions import (
    AlreadyExistsError,
    AppException,
    AuthenticationError,
    AuthorizationError,
    NotFoundError,
    ValidationError,
)
from app.core.middleware import RequestIDMiddleware
from app.core.cache import setup_cache
from unittest.mock import patch

from fastapi import FastAPI

from app.core.logfire_setup import instrument_app, setup_logfire


class TestSettings:
    """Tests for settings configuration."""

    def test_project_name_is_set(self):
        """Test project name is configured."""
        assert settings.PROJECT_NAME == "fullstack"

    def test_api_v1_str_is_set(self):
        """Test API version string is set."""
        assert settings.API_V1_STR == "/api/v1"

    def test_debug_mode_default(self):
        """Test debug mode has default value."""
        assert isinstance(settings.DEBUG, bool)

    def test_cors_origins_is_list(self):
        """Test CORS origins is a list."""
        assert isinstance(settings.CORS_ORIGINS, list)


REPO_ROOT = Path(__file__).resolve().parents[2]

# Retired with the Google Workspace MCP architecture. Re-adding one of these
# resurrects a credential fallback chain that no code path reads.
REMOVED_GOOGLE_MCP_ALIASES = [
    "GOOGLE_WORKSPACE_MCP_CLIENT_ID",
    "GOOGLE_WORKSPACE_MCP_CLIENT_SECRET",
    "GOOGLE_DRIVE_CLIENT_ID",
    "GOOGLE_DRIVE_CLIENT_SECRET",
]


class TestRetiredGoogleMcpConfiguration:
    """The deprecated OAuth aliases are gone; the service-account var is not.

    ``GOOGLE_DRIVE_CREDENTIALS_FILE`` is a different, live mechanism (the RAG
    Google Drive sync connector) that a keyword sweep for "GOOGLE_DRIVE" would
    happily delete along with the aliases — so it is asserted here too.
    """

    def test_aliases_are_gone_from_settings(self):
        for name in REMOVED_GOOGLE_MCP_ALIASES:
            assert name not in Settings.model_fields

    def test_service_account_credentials_file_survives(self):
        assert "GOOGLE_DRIVE_CREDENTIALS_FILE" in Settings.model_fields
        assert settings.GOOGLE_DRIVE_CREDENTIALS_FILE

    def test_aliases_are_gone_from_generated_manifests(self):
        """ENV_VARS.md and the configuration manifest are generated from
        Settings, so a stale one here means `make governance-sync` was skipped.
        """
        generated = [
            REPO_ROOT / "ENV_VARS.md",
            REPO_ROOT / "governance" / "manifests" / "generated" / "configuration.json",
        ]
        for path in generated:
            text = path.read_text(encoding="utf-8")
            for name in REMOVED_GOOGLE_MCP_ALIASES:
                assert name not in text, f"{name} still recorded in {path.name}"
            assert "GOOGLE_DRIVE_CREDENTIALS_FILE" in text


class TestExceptions:
    """Tests for custom exceptions."""

    def test_app_exception(self):
        """Test AppException initialization."""
        error = AppException(message="Test error", code="TEST_ERROR")
        assert error.message == "Test error"
        assert error.code == "TEST_ERROR"
        assert str(error) == "Test error"

    def test_not_found_error(self):
        """Test NotFoundError."""
        error = NotFoundError(message="Item not found")
        assert error.status_code == 404
        assert error.code == "NOT_FOUND"

    def test_already_exists_error(self):
        """Test AlreadyExistsError."""
        error = AlreadyExistsError(message="Item already exists")
        assert error.status_code == 409
        assert error.code == "ALREADY_EXISTS"

    def test_authentication_error(self):
        """Test AuthenticationError."""
        error = AuthenticationError(message="Invalid credentials")
        assert error.status_code == 401
        assert error.code == "AUTHENTICATION_ERROR"

    def test_authorization_error(self):
        """Test AuthorizationError."""
        error = AuthorizationError(message="Not authorized")
        assert error.status_code == 403
        assert error.code == "AUTHORIZATION_ERROR"

    def test_validation_error(self):
        """Test ValidationError."""
        error = ValidationError(message="Invalid input")
        assert error.status_code == 422
        assert error.code == "VALIDATION_ERROR"


class TestCacheSetup:
    """Tests for cache setup."""

    def test_setup_cache_function_exists(self):
        """Test setup_cache function exists."""
        assert setup_cache is not None
        assert callable(setup_cache)


class TestMiddleware:
    """Tests for middleware."""

    def test_request_id_middleware_exists(self):
        """Test request ID middleware is configured."""
        assert RequestIDMiddleware is not None


class TestLogfireSetup:
    """Tests for Logfire setup."""

    @patch("app.core.logfire_setup.logfire")
    def test_setup_logfire_configures(self, mock_logfire):
        """Test setup_logfire calls configure."""
        setup_logfire()
        mock_logfire.configure.assert_called_once()

    @patch("app.core.logfire_setup.logfire")
    def test_instrument_app_instruments_fastapi(self, mock_logfire):
        """Test instrument_app instruments FastAPI."""
        app = FastAPI()
        instrument_app(app)
        mock_logfire.instrument_fastapi.assert_called()
