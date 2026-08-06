"""
Cleanup old or stale data from the database.

These commands are useful for maintenance tasks.
"""

import asyncio
from datetime import UTC, datetime, timedelta

import click

from app.commands import command, info, success, warning
from app.db.session import get_db_context
from app.services.mcp_connection import McpConnectionService


@command("cleanup", help="Clean up old data from the database")
@click.option("--days", "-d", default=90, type=int, help="Delete records older than N days")
@click.option("--dry-run", is_flag=True, help="Show what would be deleted without making changes")
@click.option("--force", "-f", is_flag=True, help="Skip confirmation prompt")
def cleanup(days: int, dry_run: bool, force: bool) -> None:
    """
    Remove old records from the database.

    Example:
        project cmd cleanup --days 90
        project cmd cleanup --days 30 --dry-run
        project cmd cleanup --days 7 --force
    """
    cutoff_date = datetime.now(UTC) - timedelta(days=days)

    if dry_run:
        info(f"[DRY RUN] Would delete records older than {cutoff_date}")
        return

    if not force and not click.confirm(
        f"Delete all records older than {days} days ({cutoff_date})?"
    ):
        warning("Aborted.")
        return

    async def _cleanup() -> None:
        info(f"Cleaning up records older than {cutoff_date}...")
        total_deleted = 0
        success(f"Done. Total deleted: {total_deleted} rows.")

    asyncio.run(_cleanup())


@command(
    "disable-retired-google-mcp",
    help="Disable MCP connections pointing at retired Google Workspace MCP endpoints",
)
@click.option("--dry-run", is_flag=True, help="List affected connections without changing them")
def disable_retired_google_mcp(dry_run: bool) -> None:
    """
    Disable connections whose URL is a withdrawn Google Workspace MCP endpoint.

    Safe to run twice. Contacts (https://people.googleapis.com/v1) shares a
    hostname with a retired endpoint and is deliberately left alone.

    Re-enabling is a user decision, not a rollback step: the stored credentials
    may be revoked and the endpoint may be gone.

    Example:
        fullstack cmd disable-retired-google-mcp --dry-run
        fullstack cmd disable-retired-google-mcp
    """

    async def _disable() -> None:
        async with get_db_context() as db:
            service = McpConnectionService(db)
            if dry_run:
                affected = await service.list_retired_google_workspace_mcp()
            else:
                affected = await service.disable_retired_google_workspace_mcp()
            prefix = "Would disable" if dry_run else "Disabled"
            # Identifiers and the retired URL only — never tokens or payloads.
            for connection in affected:
                info(f"{prefix} {connection.id} -> {connection.url}")
            if dry_run:
                warning(f"[DRY RUN] {len(affected)} connection(s) would be disabled.")
            else:
                success(f"Done. Disabled {len(affected)} connection(s).")

    asyncio.run(_disable())
