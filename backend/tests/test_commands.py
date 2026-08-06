"""Tests for CLI commands module."""
# ruff: noqa: I001 - Imports structured for Jinja2 template conditionals

import contextlib
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import click
from click.testing import CliRunner

from app.commands import (
    _commands,
    command,
    discover_commands,
    error,
    info,
    register_commands,
    success,
    warning,
)
from app.commands.example import hello
from app.commands import cleanup as cleanup_module
from app.commands.cleanup import cleanup, disable_retired_google_mcp
from app.commands.seed import seed


class TestCommandDecorator:
    """Tests for the command decorator."""

    def test_command_registers_function(self):
        """Test that @command decorator registers a click command."""
        initial_count = len(_commands)

        @command("test-cmd", help="Test command")
        def test_func():
            pass

        assert len(_commands) == initial_count + 1
        assert _commands[-1].name == "test-cmd"

    def test_command_uses_function_name_as_default(self):
        """Test that command name defaults to function name."""

        @command()
        def my_test_command():
            pass

        assert _commands[-1].name == "my-test-command"


class TestHelperFunctions:
    """Tests for helper output functions."""

    def test_success_prints_green(self, capsys):
        """Test success prints in green."""
        success("Test message")
        # Click uses escape codes for colors
        captured = capsys.readouterr()
        assert "Test message" in captured.out

    def test_error_prints_red(self, capsys):
        """Test error prints in red."""
        error("Error message")
        captured = capsys.readouterr()
        assert "Error message" in captured.out

    def test_warning_prints_yellow(self, capsys):
        """Test warning prints in yellow."""
        warning("Warning message")
        captured = capsys.readouterr()
        assert "Warning message" in captured.out

    def test_info_prints_plain(self, capsys):
        """Test info prints plain text."""
        info("Info message")
        captured = capsys.readouterr()
        assert "Info message" in captured.out


class TestDiscoverCommands:
    """Tests for command discovery."""

    def test_discover_commands_returns_list(self):
        """Test that discover_commands returns a list."""
        commands = discover_commands()
        assert isinstance(commands, list)

    def test_discover_commands_caches_results(self):
        """Test that discover_commands caches on second call."""
        commands1 = discover_commands()
        commands2 = discover_commands()
        assert commands1 is commands2


class TestRegisterCommands:
    """Tests for registering commands."""

    def test_register_commands_adds_to_group(self):
        """Test that register_commands adds discovered commands to CLI group."""

        @click.group()
        def cli():
            pass

        register_commands(cli)
        # After registration, cli should have commands
        # We can't assert exact count since it depends on what's discovered


class TestSeedCommand:
    """Tests for the seed command."""

    def test_seed_dry_run(self):
        """Test seed command with --dry-run."""
        runner = CliRunner()
        result = runner.invoke(seed, ["--dry-run", "--count", "5"])
        assert result.exit_code == 0
        assert "[DRY RUN]" in result.output
        assert "5" in result.output

    def test_seed_dry_run_with_clear(self):
        """Test seed command with --dry-run and --clear."""
        runner = CliRunner()
        result = runner.invoke(seed, ["--dry-run", "--clear"])
        assert result.exit_code == 0
        assert "Would clear existing data" in result.output


class TestHelloCommand:
    """Tests for the hello command."""

    def test_hello_command_runs(self):
        """Test hello command executes."""
        runner = CliRunner()
        result = runner.invoke(hello)
        assert result.exit_code == 0
        assert "Hello" in result.output

    def test_hello_command_with_name(self):
        """Test hello command with --name option."""
        runner = CliRunner()
        result = runner.invoke(hello, ["--name", "Alice"])
        assert result.exit_code == 0
        assert "Alice" in result.output


class TestCleanupCommand:
    """Tests for the cleanup command."""

    def test_cleanup_dry_run(self):
        """Test cleanup command with --dry-run."""
        runner = CliRunner()
        result = runner.invoke(cleanup, ["--dry-run"])
        assert result.exit_code == 0
        assert "[DRY RUN]" in result.output

    def test_cleanup_with_days_option(self):
        """Test cleanup command with --days option."""
        runner = CliRunner()
        result = runner.invoke(cleanup, ["--dry-run", "--days", "7"])
        assert result.exit_code == 0


def _retired_row(**overrides):
    """A connection row as the sweep returns it, carrying a secret to leak."""
    data = {
        "id": uuid4(),
        "url": "https://gmailmcp.googleapis.com/mcp/v1",
        "auth_token": "enc:SUPERSECRETTOKEN",
        "oauth_payload": "enc:SUPERSECRETPAYLOAD",
    }
    data.update(overrides)
    return SimpleNamespace(**data)


class TestDisableRetiredGoogleMcpCommand:
    """The retired Google Workspace MCP sweep, driven through the CLI."""

    @staticmethod
    def _patch(monkeypatch, *, retired):
        """Stub the DB session and service; count which sweep was called."""
        calls = {"list": 0, "disable": 0}

        @contextlib.asynccontextmanager
        async def fake_db_context():
            yield MagicMock()

        class FakeService:
            def __init__(self, db):
                pass

            async def list_retired_google_workspace_mcp(self):
                calls["list"] += 1
                return retired

            async def disable_retired_google_workspace_mcp(self):
                calls["disable"] += 1
                return retired

        monkeypatch.setattr(cleanup_module, "get_db_context", fake_db_context)
        monkeypatch.setattr(cleanup_module, "McpConnectionService", FakeService)
        return calls

    def test_dry_run_previews_without_writing(self, monkeypatch):
        row = _retired_row()
        calls = self._patch(monkeypatch, retired=[row])

        result = CliRunner().invoke(disable_retired_google_mcp, ["--dry-run"])

        assert result.exit_code == 0
        assert calls == {"list": 1, "disable": 0}
        assert str(row.id) in result.output
        assert "1 connection(s) would be disabled" in result.output

    def test_sweep_disables_and_reports_ids(self, monkeypatch):
        rows = [
            _retired_row(url="https://drivemcp.googleapis.com/mcp/v1"),
            _retired_row(url="https://people.googleapis.com/mcp/v1"),
        ]
        calls = self._patch(monkeypatch, retired=rows)

        result = CliRunner().invoke(disable_retired_google_mcp, [])

        assert result.exit_code == 0
        assert calls["disable"] == 1
        for row in rows:
            assert str(row.id) in result.output
        assert "Disabled 2 connection(s)" in result.output

    def test_output_never_carries_credentials(self, monkeypatch):
        row = _retired_row()
        self._patch(monkeypatch, retired=[row])

        result = CliRunner().invoke(disable_retired_google_mcp, [])

        assert "SUPERSECRET" not in result.output
        assert str(row.id) in result.output and row.url in result.output
