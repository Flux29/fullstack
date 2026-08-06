"""Runtime evidence collection.

The per-user MCP connections are the privileged tool surface that exists only as database
rows. Static extraction cannot see them, and pretending otherwise would produce a permission
map that is confidently incomplete.

Everything here follows the runtime-evidence rules, and each one exists for a specific
reason rather than as general caution:

* **Aggregate only.** The query counts and groups; it never returns a row. A dump of this
  table would contain live credentials.
* **URLs reduced to scheme and host at capture time.** The catalog supports placing a token
  in the URL, and the `url` column is stored unencrypted while `auth_token` is not. A URL is
  credential-bearing until proven otherwise, and stripping it after storage is too late.
* **Never committed.** Output lands under the gitignored cache. Committed artifacts may
  reference it by ID and summary only.
* **Read-only, explicitly invoked, and never from `.env`.** The DSN is passed on the command
  line. Reading the application's environment would be the same in-process credential load
  the extraction rules forbid.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from repo_governance.config import Context
from repo_governance.io_atomic import write_json_atomic

#: Aggregates only. Every column here is a count or a low-cardinality label; nothing
#: identifies a user or carries a secret.
QUERY = """
SELECT
    auth_type,
    is_enabled,
    (allowed_tools IS NULL)  AS exposes_every_advertised_tool,
    (auth_token IS NOT NULL) AS has_encrypted_token,
    last_status,
    COUNT(*)                 AS connections,
    COUNT(DISTINCT user_id)  AS users
FROM mcp_connections
GROUP BY 1, 2, 3, 4, 5
ORDER BY 1, 2, 3, 4, 5
"""

HOST_QUERY = """
SELECT split_part(split_part(url, '://', 2), '/', 1) AS host, COUNT(*) AS connections
FROM mcp_connections
GROUP BY 1
ORDER BY 2 DESC, 1
"""


#: Wraps the aggregate queries so a single psql invocation returns parseable output. Used
#: by the Docker path, where the driver is not available on this side of the container.
JSON_WRAPPER = "SELECT coalesce(json_agg(t), '[]'::json) FROM ({query}) t"


@dataclass
class SnapshotResult:
    evidence_id: str
    path: str
    summary: str


class EvidenceError(RuntimeError):
    """Evidence collection could not proceed."""


def sanitize_host(value: str | None) -> str:
    """Reduce a host to something that cannot carry a credential."""
    if not value:
        return "unknown"
    cleaned = value.split("@")[-1].split("?")[0].strip()
    return cleaned or "unknown"


def sanitize_dsn(dsn: str) -> str:
    """A DSN for display: scheme, host, and database only, never the password."""
    parts = urlsplit(dsn)
    host = parts.hostname or "unknown"
    return f"{parts.scheme}://{host}{parts.path}"


def _collect_via_docker(container: str, user: str, database: str) -> tuple[list[dict], list[tuple]]:
    """Read the aggregates through `docker exec psql`.

    The base stack publishes no host port at all — that is the exposure posture governance
    asserts, not an accident — so requiring a host-reachable DSN would make this unusable in
    exactly the configuration the policy recommends. Both statements are read-only
    aggregates; neither returns a row.
    """
    import json
    import shutil
    import subprocess

    if shutil.which("docker") is None:
        raise EvidenceError("docker is not on PATH")

    def run(query: str) -> Any:
        completed = subprocess.run(
            [
                "docker",
                "exec",
                container,
                "psql",
                "-U",
                user,
                "-d",
                database,
                "-tAc",
                JSON_WRAPPER.format(query=query.strip().rstrip(";")),
            ],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        if completed.returncode != 0:
            raise EvidenceError(f"psql failed in {container}: {completed.stderr.strip()[:200]}")
        return json.loads(completed.stdout.strip() or "[]")

    grouped = run(QUERY)
    hosts = [(row.get("host"), row.get("connections")) for row in run(HOST_QUERY)]
    return grouped, hosts


def snapshot_mcp_connections(
    ctx: Context,
    dsn: str | None,
    *,
    date: str,
    container: str | None = None,
    user: str = "postgres",
    database: str = "fullstack",
) -> SnapshotResult:
    if container:
        rows, hosts = _collect_via_docker(container, user, database)
        environment = f"docker://{container}/{database}"
        return _write_snapshot(ctx, rows, hosts, environment=environment, date=date)

    if not dsn:
        raise EvidenceError("give either --dsn or --via-docker")
    return _snapshot_via_driver(ctx, dsn, date=date)


def _snapshot_via_driver(ctx: Context, dsn: str, *, date: str) -> SnapshotResult:
    try:
        import psycopg
    except ImportError as exc:  # pragma: no cover - depends on the optional extra
        raise EvidenceError(
            "psycopg is not installed. Evidence collection is an optional extra so that the "
            "default install cannot reach a database: "
            "uv sync --project tools/repo_governance --extra evidence"
        ) from exc

    try:
        with psycopg.connect(dsn, connect_timeout=10) as connection:
            connection.read_only = True
            with connection.cursor() as cursor:
                cursor.execute(QUERY)
                grouped = cursor.fetchall()
                columns = [description.name for description in cursor.description]
                cursor.execute(HOST_QUERY)
                hosts = cursor.fetchall()
    except Exception as exc:  # noqa: BLE001 - any driver error is the same outcome here
        raise EvidenceError(f"could not read the database: {type(exc).__name__}: {exc}") from exc

    rows = [dict(zip(columns, row, strict=True)) for row in grouped]
    return _write_snapshot(ctx, rows, hosts, environment=sanitize_dsn(dsn), date=date)


def _write_snapshot(
    ctx: Context,
    rows: list[dict],
    hosts: list[tuple],
    *,
    environment: str,
    date: str,
) -> SnapshotResult:
    total = sum(row["connections"] for row in rows)
    ungated = sum(row["connections"] for row in rows if row["exposes_every_advertised_tool"])

    evidence_id = f"mcp-connections-{date}"
    document: dict[str, Any] = {
        "evidence_id": evidence_id,
        "collected_for": "the product-security decision on MCP approval gating (ADR-003)",
        "environment": environment,
        "committed": False,
        "totals": {
            "connections": total,
            "connections_exposing_every_advertised_tool": ungated,
        },
        "by_configuration": rows,
        "by_host": [
            {"host": sanitize_host(host), "connections": count} for host, count in hosts
        ],
        "notes": [
            "Aggregates only; no row was read. URLs were reduced to host at capture time.",
            "A connection with allowed_tools unset exposes every tool its server advertises, "
            "and MCP-sourced tools bypass the deferred-tool approval gate entirely.",
        ],
    }

    target = ctx.paths.evidence / f"{evidence_id}.json"
    write_json_atomic(target, document)

    summary = (
        f"{total} per-user MCP connections, of which {ungated} expose every tool their server "
        f"advertises and none are approval-gated."
    )
    return SnapshotResult(
        evidence_id=evidence_id,
        path=str(target),
        summary=summary,
    )
