"""OAuth 2.1 authorization-code flow for MCP servers (web redirect variant).

Most business MCP servers (Notion, Linear, Atlassian, …) are OAuth-gated:
an unauthenticated request returns ``401`` with a ``WWW-Authenticate`` header
pointing at RFC 9728 protected-resource metadata. This module drives the full
flow for a *web* app, where the authorization redirect and its callback are
two separate HTTP requests rather than a blocking CLI prompt:

    1. :func:`discover`      — probe the server, resolve the authorization
       server, fetch its RFC 8414 metadata (endpoints, PKCE support).
    2. :func:`register_client` — RFC 7591 dynamic client registration.
    3. :func:`authorization_url` — build the consent URL (PKCE + state +
       RFC 8707 resource indicator); the browser is redirected there.
    4. :func:`exchange_code` — swap the returned code for tokens (callback).
    5. :func:`refresh_tokens` — refresh an expired access token.

We lean on the MCP SDK's ``mcp.client.auth.oauth2`` helpers for discovery
URL ordering, request building and response parsing so our behaviour matches
the SDK's own client; the token grant/refresh POSTs are issued here so the
flow can be split across requests and persisted between them.

Every URL reached from here — discovery candidates, the token endpoint, and
each redirect hop — is SSRF-checked (see :func:`_send`), because discovery
means the remote server, not the user, picks most of the addresses we call.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlsplit

import httpx
from mcp.client.auth import PKCEParameters
from mcp.client.auth.exceptions import OAuthFlowError
from mcp.client.auth.oauth2 import (
    build_oauth_authorization_server_metadata_discovery_urls,
    build_protected_resource_metadata_discovery_urls,
    create_client_registration_request,
    create_oauth_metadata_request,
    extract_resource_metadata_from_www_auth,
    handle_auth_metadata_response,
    handle_protected_resource_response,
    handle_registration_response,
    resource_url_from_server_url,
)
from mcp.shared.auth import OAuthClientMetadata, OAuthMetadata, OAuthToken
from pydantic import AnyUrl, BaseModel

from app.agents.google_workspace_api import google_api_scopes
from app.agents.mcp import validate_mcp_url
from app.core.config import settings

logger = logging.getLogger(__name__)

# How long an access token must still be valid to be reused without refreshing.
TOKEN_EXPIRY_SKEW_SECS = 60.0

# How long a consent redirect stays redeemable. The ``state`` token is the only
# thing authenticating the callback, and it travels through the provider and the
# browser's history — so a flow the user never finished must stop working rather
# than sit in the database indefinitely.
FLOW_TTL_SECS = 600.0

# Redirects are followed by hand (see _send) so every hop is SSRF-checked.
_MAX_REDIRECTS = 5

_HTTP_TIMEOUT = httpx.Timeout(
    settings.MCP_CONNECT_TIMEOUT_SECS, connect=settings.MCP_CONNECT_TIMEOUT_SECS
)


class OAuthError(Exception):
    """A recoverable failure in the OAuth flow (surfaced to the user)."""


async def _send(client: httpx.AsyncClient, request: httpx.Request) -> httpx.Response:
    """Send *request*, SSRF-checking every hop, redirects included.

    Discovery and token endpoints are chosen by the remote server, so the URL
    the user typed is not the URL we end up talking to. The client has
    redirects turned off and we follow them here, one validated hop at a time —
    otherwise a server could answer with ``302 http://169.254.169.254/`` and we
    would fetch it from inside the network.
    """
    for _ in range(_MAX_REDIRECTS + 1):
        try:
            await validate_mcp_url(str(request.url))
        except ValueError as exc:
            logger.warning("Blocked MCP OAuth request to %s: %s", request.url, exc)
            raise OAuthError("This server pointed the OAuth flow at a blocked address.") from exc
        response = await client.send(request)
        if response.next_request is None:
            return response
        request = response.next_request
    raise OAuthError("Too many redirects while talking to this server.")


class McpOAuthPayload(BaseModel):
    """Everything an OAuth connection needs, stored Fernet-encrypted as JSON.

    ``code_verifier`` is present only while a consent redirect is in flight;
    once tokens arrive it is dropped and ``access_token``/``refresh_token`` are
    filled in. ``expires_at`` is epoch seconds (or None if the token doesn't
    expire).
    """

    # The MCP server this flow authorizes against. Applied to the connection
    # only once tokens arrive, so a re-authorization that points at a new URL
    # cannot move the connection while the old tokens are still stored.
    server_url: str
    # "google_workspace" is retired: oauth_provider can no longer produce it.
    # It stays in this Literal for one release because connections authorized
    # before the removal carry it inside the Fernet-encrypted payload JSON, and
    # dropping the member makes those rows fail to deserialize. Delete it, the
    # compatibility block below, and the same member on exchange_code and
    # refresh_tokens (which are handed this stored value) in one change.
    provider: Literal["generic", "google_workspace", "google_api"] = "generic"
    # Epoch seconds when the consent redirect was issued — see FLOW_TTL_SECS.
    started_at: float
    authorization_endpoint: str
    token_endpoint: str
    registration_endpoint: str | None = None
    client_id: str
    client_secret: str | None = None
    scope: str | None = None
    resource: str
    redirect_uri: str
    code_verifier: str | None = None
    access_token: str | None = None
    refresh_token: str | None = None
    expires_at: float | None = None


@dataclass(frozen=True)
class DiscoveredServer:
    """The endpoints and metadata needed to run the flow for one server."""

    authorization_endpoint: str
    token_endpoint: str
    registration_endpoint: str | None
    resource: str
    scope: str | None
    metadata: OAuthMetadata


# ---------------------------------------------------------------------------
# COMPATIBILITY — retired Google Workspace MCP endpoints. Delete whole.
#
# Google Workspace runs entirely on the direct REST toolsets in
# ``app/agents/google_apis`` (see :mod:`app.agents.google_workspace_api`); the
# preview MCP servers were withdrawn after persistent connection failures.
# Everything in this block exists only to make that transition legible, and
# comes out next release together with the "google_workspace" member of
# ``McpOAuthPayload.provider`` — one removal, at the milestone already recorded
# on the GOOGLE_WORKSPACE_MCP_* alias family in the configuration manifest:
# "Remove once no deployment sets it and the direct Google REST executor is the
# only path" (governance/manifests/curated/architectural-intent.json).
#
# Until then these URLs are refused by name. Left to fall through they would
# classify as "generic" and be driven through ordinary discovery and dynamic
# registration against a host that no longer answers — a slow, opaque failure
# where the user needs to be told to pick the built-in integration instead.
# ---------------------------------------------------------------------------

_RETIRED_GOOGLE_WORKSPACE_MCP_HOSTS = frozenset(
    {
        "gmailmcp.googleapis.com",
        "drivemcp.googleapis.com",
        "docsmcp.googleapis.com",
        "sheetsmcp.googleapis.com",
        "slidesmcp.googleapis.com",
        "calendarmcp.googleapis.com",
        "chatmcp.googleapis.com",
    }
)

# Deliberately not in the set above. This host also serves the live Contacts
# integration (https://people.googleapis.com/v1, the canonical catalog entry),
# so it is retired by path rather than by hostname: everything on it that the
# direct REST integrations do not claim. A blanket hostname rejection here
# would disconnect Contacts.
_RETIRED_PEOPLE_MCP_HOST = "people.googleapis.com"

RETIRED_GOOGLE_WORKSPACE_MCP_MESSAGE = (
    "Google Workspace MCP endpoints are no longer supported; use the built-in "
    "Google integrations from the catalog."
)


def is_retired_google_workspace_mcp_url(server_url: str) -> bool:
    """True for a URL :func:`oauth_provider` refuses as a retired MCP endpoint.

    Applies the same two steps in the same order as ``oauth_provider``: a URL
    the direct REST integrations claim is never retired, which is what keeps
    the shared ``people.googleapis.com`` host usable for Contacts. The
    maintenance command that disables stored connections shares this predicate
    rather than re-deriving the rule, so the two cannot drift apart.
    """
    if google_api_scopes(server_url) is not None:
        return False
    host = urlsplit(server_url).hostname
    return host in _RETIRED_GOOGLE_WORKSPACE_MCP_HOSTS or host == _RETIRED_PEOPLE_MCP_HOST


# --------------------------- end compatibility -----------------------------


def oauth_provider(server_url: str) -> Literal["generic", "google_api"]:
    """Classify *server_url* for the OAuth flow.

    The order is load-bearing. The generally available Google REST APIs are
    claimed first: they are the only URLs that receive the deployment's static
    Google client credentials, so the match is exact (see
    :func:`~app.agents.google_workspace_api.google_api_kind`) and no alternate
    path, port or subdomain can impersonate one. Retired Workspace MCP
    endpoints are refused second, because one of their hostnames still serves a
    live integration. Everything else is a user-supplied server and runs the
    ordinary discovery + dynamic-registration flow.
    """
    if google_api_scopes(server_url) is not None:
        return "google_api"
    if is_retired_google_workspace_mcp_url(server_url):
        raise OAuthError(RETIRED_GOOGLE_WORKSPACE_MCP_MESSAGE)
    return "generic"


def google_client_credentials() -> tuple[str, str]:
    """Return the deployment's static Google Web OAuth client."""
    client_id = settings.GOOGLE_API_CLIENT_ID
    secret = settings.GOOGLE_API_CLIENT_SECRET.get_secret_value()
    if not client_id or not secret:
        raise OAuthError(
            "Google integrations require GOOGLE_API_CLIENT_ID and "
            "GOOGLE_API_CLIENT_SECRET (the Full Stack Web OAuth client)."
        )
    return client_id, secret


def google_api_server(server_url: str) -> DiscoveredServer:
    """Static OAuth metadata for the generally available Google REST APIs."""
    scopes = google_api_scopes(server_url)
    if scopes is None:
        raise OAuthError("Unsupported Google API integration URL.")
    return DiscoveredServer(
        authorization_endpoint="https://accounts.google.com/o/oauth2/v2/auth",
        token_endpoint="https://oauth2.googleapis.com/token",
        registration_endpoint=None,
        resource=server_url,
        scope=" ".join(scopes),
        metadata=OAuthMetadata(
            issuer="https://accounts.google.com",
            authorization_endpoint=AnyUrl("https://accounts.google.com/o/oauth2/v2/auth"),
            token_endpoint=AnyUrl("https://oauth2.googleapis.com/token"),
        ),
    )


def client_metadata(redirect_uri: str, scope: str | None) -> OAuthClientMetadata:
    """Our app's client metadata for dynamic registration / the auth request."""
    return OAuthClientMetadata(
        client_name=f"{settings.PROJECT_NAME} assistant",
        redirect_uris=[AnyUrl(redirect_uri)],
        grant_types=["authorization_code", "refresh_token"],
        response_types=["code"],
        token_endpoint_auth_method="client_secret_post",
        scope=scope,
    )


async def discover(server_url: str) -> DiscoveredServer:
    """Resolve the authorization server and its metadata for *server_url*.

    Follows the SEP-985 / RFC 9728 discovery chain: read the protected-resource
    metadata (from the ``WWW-Authenticate`` header if present, else well-known
    URIs), then the authorization-server metadata (RFC 8414, OIDC fallbacks).
    """
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT, follow_redirects=False) as client:
        # 1. Probe the server unauthenticated to surface the WWW-Authenticate hint.
        www_auth_url: str | None = None
        try:
            probe = await _send(
                client,
                client.build_request(
                    "POST",
                    server_url,
                    headers={"Accept": "application/json, text/event-stream"},
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "initialize",
                        "params": {
                            "protocolVersion": "2025-06-18",
                            "capabilities": {},
                            "clientInfo": {"name": settings.PROJECT_NAME, "version": "1.0"},
                        },
                    },
                ),
            )
            www_auth_url = extract_resource_metadata_from_www_auth(probe)
        except (httpx.HTTPError, OAuthError):
            # Probe failed or was blocked — fall back to well-known discovery below.
            pass

        # 2. Protected-resource metadata → the authorization server URL.
        auth_server_url: str | None = None
        prm_scopes: list[str] | None = None
        for url in build_protected_resource_metadata_discovery_urls(www_auth_url, server_url):
            try:
                prm = await handle_protected_resource_response(
                    await _send(client, create_oauth_metadata_request(url))
                )
            except (httpx.HTTPError, OAuthError):
                continue
            if prm and prm.authorization_servers:
                auth_server_url = str(prm.authorization_servers[0])
                prm_scopes = prm.scopes_supported
                break

        # 3. Authorization-server metadata (endpoints, PKCE support). Both the
        #    candidate URLs and auth_server_url come from the server's own
        #    metadata, so _send validates each one before it is requested.
        asm: OAuthMetadata | None = None
        for url in build_oauth_authorization_server_metadata_discovery_urls(
            auth_server_url, server_url
        ):
            try:
                keep_going, candidate = await handle_auth_metadata_response(
                    await _send(client, create_oauth_metadata_request(url))
                )
            except (httpx.HTTPError, OAuthError):
                continue
            if candidate is not None:
                asm = candidate
                break
            if not keep_going:
                break

        if asm is None:
            raise OAuthError(
                "This server did not advertise OAuth metadata. It may not support "
                "OAuth, or may require a static token instead."
            )

        # The consent URL is handed to the user's browser rather than fetched
        # here, so it never passes through _send — check it now.
        try:
            await validate_mcp_url(str(asm.authorization_endpoint))
        except ValueError as exc:
            logger.warning(
                "Blocked MCP OAuth authorization endpoint %s: %s", asm.authorization_endpoint, exc
            )
            raise OAuthError("This server pointed the OAuth flow at a blocked address.") from exc

        scope = asm.scopes_supported or prm_scopes
        return DiscoveredServer(
            authorization_endpoint=str(asm.authorization_endpoint),
            token_endpoint=str(asm.token_endpoint),
            registration_endpoint=(
                str(asm.registration_endpoint) if asm.registration_endpoint else None
            ),
            resource=resource_url_from_server_url(server_url),
            scope=" ".join(scope) if scope else None,
            metadata=asm,
        )


async def register_client(server: DiscoveredServer, redirect_uri: str) -> tuple[str, str | None]:
    """Dynamically register this app; return ``(client_id, client_secret)``.

    Public clients (no registration or no secret returned) get ``None`` for the
    secret and use PKCE alone.
    """
    metadata = client_metadata(redirect_uri, server.scope)
    request = create_client_registration_request(
        server.metadata, metadata, server.authorization_endpoint
    )
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT, follow_redirects=False) as client:
        try:
            info = await handle_registration_response(await _send(client, request))
        except httpx.HTTPError as exc:
            raise OAuthError(f"Dynamic client registration failed: {exc}") from exc
        except OAuthFlowError as exc:
            # The SDK puts the server's response body in the message — keep it
            # in the log, hand the user a fixed string.
            logger.warning("MCP dynamic client registration failed at %s: %s", request.url, exc)
            raise OAuthError("This server rejected the client registration request.") from exc
    if not info.client_id:
        raise OAuthError("The server did not return a client_id during registration.")
    return info.client_id, info.client_secret


def authorization_url(
    server: DiscoveredServer,
    *,
    client_id: str,
    redirect_uri: str,
    state: str,
    code_challenge: str,
    provider: Literal["generic", "google_api"] = "generic",
) -> str:
    """Build the consent URL the browser is redirected to."""
    params: dict[str, str] = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    if provider == "google_api":
        # Required to receive a refresh token for background agent turns.
        params.update(
            access_type="offline",
            prompt="consent",
            include_granted_scopes="true",
        )
    else:
        params["resource"] = server.resource
    if server.scope:
        params["scope"] = server.scope
    query = httpx.QueryParams(params)
    sep = "&" if "?" in server.authorization_endpoint else "?"
    return f"{server.authorization_endpoint}{sep}{query}"


def new_pkce() -> PKCEParameters:
    """Fresh PKCE verifier/challenge pair."""
    return PKCEParameters.generate()


async def exchange_code(
    *,
    token_endpoint: str,
    client_id: str,
    client_secret: str | None,
    code: str,
    code_verifier: str,
    redirect_uri: str,
    resource: str,
    provider: Literal["generic", "google_workspace", "google_api"] = "generic",
) -> OAuthToken:
    """Exchange an authorization code for tokens (called from the callback)."""
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": client_id,
        "code_verifier": code_verifier,
    }
    if provider not in {"google_workspace", "google_api"}:
        data["resource"] = resource
    if client_secret:
        data["client_secret"] = client_secret
    return await _token_request(token_endpoint, data)


async def refresh_tokens(
    *,
    token_endpoint: str,
    client_id: str,
    client_secret: str | None,
    refresh_token: str,
    resource: str,
    scope: str | None,
    provider: Literal["generic", "google_workspace", "google_api"] = "generic",
) -> OAuthToken:
    """Refresh an expired access token using the stored refresh token."""
    data = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id,
    }
    if provider not in {"google_workspace", "google_api"}:
        data["resource"] = resource
    if client_secret:
        data["client_secret"] = client_secret
    if scope:
        data["scope"] = scope
    return await _token_request(token_endpoint, data)


async def _token_request(token_endpoint: str, data: dict[str, str]) -> OAuthToken:
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT, follow_redirects=False) as client:
        try:
            response = await _send(
                client,
                client.build_request(
                    "POST",
                    token_endpoint,
                    data=data,
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                ),
            )
        except httpx.HTTPError as exc:
            raise OAuthError(f"Token request failed: {exc}") from exc
    if response.status_code != 200:
        # The body is whatever the endpoint chose to return, and this message
        # ends up in the user's browser — log the detail, show the status.
        logger.warning(
            "MCP token endpoint %s returned %s: %s",
            token_endpoint,
            response.status_code,
            response.text[:200],
        )
        raise OAuthError(f"Token endpoint returned {response.status_code}.")
    try:
        return OAuthToken.model_validate_json(response.content)
    except ValueError as exc:
        raise OAuthError(f"Invalid token response: {exc}") from exc
