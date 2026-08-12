"""MCP server toolsets for the assistant agent.

Two kinds of servers end up here as uniform :class:`McpServerSpec` entries:
  - deployment-managed servers from ``settings.MCP_SERVERS``, and
  - per-user connections configured in Settings → Integrations
    (built by :mod:`app.services.mcp_connection`).

Each spec is probed with a short ``tools/list`` round-trip before the turn;
unreachable servers are skipped (with a warning) instead of failing the chat,
because pydantic-ai enters every toolset when the run starts and a dead
server would otherwise abort the whole turn. Probe verdicts are TTL-cached
per (url, auth-fingerprint) so adjacent turns don't re-pay the handshake —
and a dead server doesn't stall every turn for the connect timeout.

The transport (streamable HTTP or SSE) is inferred from each server's URL, so
SSE-only servers such as Atlassian/Jira work alongside streamable-HTTP ones.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from time import monotonic
from typing import Any

from app.core.config import GITHUB_MCP_READ_ONLY_TOOLS, settings
from app.core.sanitize import validate_webhook_url

logger = logging.getLogger(__name__)


class McpProbeError(Exception):
    """A failed liveness probe, with its root cause already unwrapped.

    The MCP client runs on anyio task groups, so a failure surfaces as a
    (possibly ``BaseException``-carrying) group that ``except Exception``
    would miss. :func:`probe_mcp_server` collapses those into this type so
    every caller can handle a dead server with a plain ``except Exception``.
    """


async def validate_mcp_url(url: str) -> str:
    """SSRF-check a URL before we talk to it (same policy as webhooks).

    Applies to every URL we request, not just the one the user typed: OAuth
    discovery hands us endpoints the remote server chose, and those deserve
    the same check. Runs in a thread because validation resolves DNS.
    """
    return await asyncio.to_thread(validate_webhook_url, url)


@dataclass(frozen=True)
class McpToolInfo:
    """One tool advertised by an MCP server (for the /test endpoint and UI)."""

    name: str
    description: str


@dataclass(frozen=True)
class McpServerSpec:
    """Transport-level description of one MCP server to attach to a run."""

    name: str
    url: str
    headers: dict[str, str] = field(default_factory=dict)
    # None = expose every tool the server offers.
    allowed_tools: list[str] | None = None
    assert_read_only: bool = False


def static_server_specs() -> list[McpServerSpec]:
    """Specs for the deployment-managed servers from ``MCP_SERVERS``."""
    specs: list[McpServerSpec] = []
    for cfg in settings.MCP_SERVERS:
        headers = dict(cfg.headers)
        if cfg.auth == "github":
            token = settings.GITHUB_MCP_TOKEN.get_secret_value()
            headers["Authorization"] = f"Bearer {token}"
        specs.append(
            McpServerSpec(
                name=cfg.name,
                url=cfg.url,
                headers=headers,
                allowed_tools=cfg.allowed_tools,
                assert_read_only=cfg.auth == "github",
            )
        )
    return specs


@asynccontextmanager
async def _mcp_transport(
    url: str, headers: dict[str, str] | None
) -> AsyncIterator[tuple[Any, Any]]:
    """Open the right client transport for *url*, yielding ``(read, write)``.

    The transport is inferred from the URL exactly as the toolset layer does it
    (FastMCP): a path segment ``/sse`` selects the SSE client (used by servers
    like Atlassian/Jira), everything else uses streamable HTTP. The streamable
    client yields a third session-id callable we don't need here.
    """
    from pydantic_ai.mcp import infer_transport_type_from_url

    if infer_transport_type_from_url(url) == "sse":
        from mcp.client.sse import sse_client

        async with sse_client(url, headers=headers or None) as (read, write):
            yield read, write
    else:
        from mcp.client.streamable_http import streamablehttp_client

        async with streamablehttp_client(url, headers=headers or None) as (read, write, _):
            yield read, write


async def probe_mcp_server(
    url: str,
    headers: dict[str, str] | None = None,
    timeout: float | None = None,
) -> list[McpToolInfo]:
    """Connect to an MCP server and list its tools.

    Any failure is raised as :class:`McpProbeError` with the root cause already
    unwrapped, so callers can skip a dead server with ``except Exception``.
    Cancellation always propagates.

    Used both as the pre-flight liveness check before a chat turn and as the
    backing call for the connection "test" endpoint. The transport (streamable
    HTTP or SSE) is inferred from the URL, matching the toolset layer.
    """
    from mcp import ClientSession

    try:
        async with asyncio.timeout(timeout or settings.MCP_CONNECT_TIMEOUT_SECS):
            async with (
                _mcp_transport(url, headers) as (read, write),
                ClientSession(read, write) as session,
            ):
                await session.initialize()
                result = await session.list_tools()
    except (Exception, BaseExceptionGroup) as exc:
        if _carries_base_exception(exc):
            raise
        raise McpProbeError(probe_error_message(exc)) from exc
    return [McpToolInfo(name=t.name, description=t.description or "") for t in result.tools]


def _carries_base_exception(exc: BaseException) -> bool:
    """True when *exc* is (or wraps) something that isn't an ``Exception``.

    A group carrying a ``CancelledError`` means the turn was cancelled, not
    that the server is down — swallowing it would keep the run alive.
    """
    if isinstance(exc, BaseExceptionGroup):
        return any(_carries_base_exception(inner) for inner in exc.exceptions)
    return not isinstance(exc, Exception)


def probe_error_message(exc: BaseException) -> str:
    """Human-readable reason for a failed probe.

    The MCP client runs on anyio task groups, so failures surface as nested
    ExceptionGroups ("unhandled errors in a TaskGroup") — unwrap to the root
    cause before showing anything to a user.
    """
    while isinstance(exc, BaseExceptionGroup) and exc.exceptions:
        exc = exc.exceptions[0]
    if isinstance(exc, TimeoutError):
        return f"Connection timed out after {settings.MCP_CONNECT_TIMEOUT_SECS:g}s"
    return str(exc) or exc.__class__.__name__


def _tool_prefix(name: str) -> str:
    """Connection name → tool prefix, e.g. "github-work" → "github_work"."""
    return re.sub(r"[^a-z0-9_]", "_", name.lower()).strip("_") or "mcp"


def _make_toolset(spec: McpServerSpec) -> Any:
    """Build a pydantic-ai toolset for one MCP server.

    Tools are prefixed with the connection name so two servers exposing the
    same tool name can't collide (pydantic-ai raises on duplicates). The
    allowlist filter runs before prefixing, so it compares against the
    unprefixed names the user picked in the UI.

    Every MCP tool is marked ``defer_loading`` — its schema stays out of the
    model request until the auto-injected tool-search capability reveals it.
    A handful of connected servers otherwise puts >100 schemas (~40k input
    tokens) on every request whether or not the turn touches them; deferred,
    the model sees one ``search_tools`` entry and pulls in only the tools a
    turn actually needs. Capability is unchanged: discovery costs one search
    round-trip the first time a server is used in a run.
    """
    from pydantic_ai.mcp import MCPToolset
    from pydantic_ai.toolsets import DeferredLoadingToolset

    server: Any = MCPToolset(
        spec.url,
        headers=spec.headers or None,
        id=f"mcp:{spec.name}",
        init_timeout=settings.MCP_CONNECT_TIMEOUT_SECS,
    )
    if spec.allowed_tools is not None:
        allowed = set(spec.allowed_tools)
        server = server.filtered(lambda _ctx, tool: tool.name in allowed)
    return DeferredLoadingToolset(server.prefixed(_tool_prefix(spec.name)))


def _dedupe_by_prefix(specs: list[McpServerSpec]) -> list[McpServerSpec]:
    """Drop specs whose tool prefix an earlier spec already claimed.

    Two servers sharing a prefix emit identical tool names and pydantic-ai
    raises on duplicates, which aborts the whole turn. Deployment-managed
    servers come first, so they win over a user connection that happens to
    pick the same name (e.g. both called "github").
    """
    unique: list[McpServerSpec] = []
    taken: set[str] = set()
    for spec in specs:
        prefix = _tool_prefix(spec.name)
        if prefix in taken:
            logger.warning(
                "Skipping MCP server %r: tool prefix %r is already used by another server",
                spec.name,
                prefix,
            )
            continue
        taken.add(prefix)
        unique.append(spec)
    return unique


@dataclass(frozen=True)
class _ProbeVerdict:
    """One remembered probe outcome; ``tools`` is None when the probe failed."""

    expires_at: float
    tools: list[McpToolInfo] | None


#: (url, auth-fingerprint) -> last probe outcome. Keyed by a hash of the auth
#: headers, never the headers themselves, so no token is retained here — and
#: so one user's expired credentials can't mark a server dead for everyone
#: else who reaches the same URL with their own auth.
_probe_cache: dict[tuple[str, str], _ProbeVerdict] = {}


def clear_probe_cache() -> None:
    """Forget every remembered probe outcome (tests; after credential changes)."""
    _probe_cache.clear()


def _probe_cache_key(url: str, headers: dict[str, str] | None) -> tuple[str, str]:
    fingerprint = hashlib.sha256(
        "\x00".join(f"{k}\x1f{v}" for k, v in sorted((headers or {}).items())).encode()
    ).hexdigest()
    return (url, fingerprint)


def _remember_probe(
    key: tuple[str, str], now: float, *, tools: list[McpToolInfo] | None, ttl: float
) -> None:
    for stale in [k for k, verdict in _probe_cache.items() if verdict.expires_at <= now]:
        del _probe_cache[stale]
    _probe_cache[key] = _ProbeVerdict(expires_at=now + ttl, tools=tools)


async def _probe_with_cache(spec: McpServerSpec) -> list[McpToolInfo] | None:
    """Probe *spec*'s server, reusing a verdict younger than its TTL.

    Liveness barely changes between adjacent chat turns, yet the handshake was
    paid on every one — and a dead server cost the full connect timeout every
    turn. Success and failure are remembered for different windows: failures
    for less, so a restarted server comes back quickly. The connection "test"
    endpoint calls :func:`probe_mcp_server` directly and stays uncached — an
    explicit re-check must actually check.
    """
    key = _probe_cache_key(spec.url, spec.headers)
    now = monotonic()
    cached = _probe_cache.get(key)
    if cached is not None and now < cached.expires_at:
        if cached.tools is None:
            logger.debug("Skipping MCP server %r: cached probe failure", spec.name)
        return cached.tools
    try:
        tools = await probe_mcp_server(spec.url, spec.headers)
    except Exception as exc:
        _remember_probe(key, now, tools=None, ttl=settings.MCP_PROBE_FAILURE_TTL_SECS)
        logger.warning(
            "Skipping MCP server %r for this turn: %s", spec.name, probe_error_message(exc)
        )
        return None
    _remember_probe(key, now, tools=tools, ttl=settings.MCP_PROBE_TTL_SECS)
    return tools


async def build_mcp_toolsets(specs: list[McpServerSpec]) -> list[Any]:
    """Toolsets for every reachable server in *specs*.

    Fresh probes run concurrently; a server whose last probe is younger than
    its TTL is not re-probed. The GitHub read-only assertion still runs every
    turn, against the advertised tool list the probe (cached or fresh) returned.
    """
    specs = _dedupe_by_prefix(specs)
    if not specs:
        return []

    async def _try(spec: McpServerSpec) -> Any | None:
        tools = await _probe_with_cache(spec)
        if tools is None:
            return None
        if spec.assert_read_only:
            advertised = {tool.name for tool in tools}
            exposed = advertised.intersection(spec.allowed_tools or [])
            unsafe = sorted(exposed - GITHUB_MCP_READ_ONLY_TOOLS)
            if unsafe:
                logger.warning(
                    "Skipping MCP server %r for this turn: "
                    "GitHub MCP exposed forbidden mutation tools: %s",
                    spec.name,
                    ", ".join(unsafe),
                )
                return None
        return _make_toolset(spec)

    results = await asyncio.gather(*(_try(spec) for spec in specs))
    return [toolset for toolset in results if toolset is not None]
