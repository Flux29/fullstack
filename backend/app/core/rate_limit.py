"""Fixed-window rate limiting for the credential endpoints.

The Next.js proxy calls the backend server-to-server, so an edge-level limit
(Traefik) sees only the proxy's address. These counters key on the forwarded
client IP and on the targeted account, which survive the proxy hop.

Counters live in Redis logical database 0 (the general application cache)
under the ``ratelimit:`` prefix; they are ephemeral, window-scoped values with
cache semantics. Redis being unreachable fails open: authentication must not
become unavailable because the cache is down.
"""

import logging

from starlette.requests import Request

from app.clients.redis import RedisClient
from app.core.exceptions import RateLimitError

logger = logging.getLogger(__name__)

# Per-scope (max attempts, window seconds). Applied to the client IP; account
# identifiers get the same window with half the attempts (rounded up), so a
# distributed guesser is still pinned per account.
AUTH_LIMITS: dict[str, tuple[int, int]] = {
    "login": (10, 300),
    "register": (5, 3600),
    "password-reset": (3, 900),
    "magic-link": (3, 900),
    "oauth-exchange": (10, 300),
}


def client_ip(request: Request) -> str:
    """Client address for rate-limit keys.

    The first X-Forwarded-For hop is the real client when the request came
    through the Next proxy or the edge; the direct peer address is the
    fallback. The backend is not reachable from the open internet in
    production, so the header is set by our own infrastructure.
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


async def enforce_auth_rate_limit(
    redis: RedisClient,
    *,
    scope: str,
    request: Request,
    account: str | None = None,
) -> None:
    """Raise RateLimitError when the scope's window is exhausted.

    ``account`` is the targeted identifier (email) when the endpoint has one;
    it is lowercased so case variants share a counter.
    """
    max_attempts, window = AUTH_LIMITS[scope]
    checks: list[tuple[str, int]] = [(f"ratelimit:{scope}:ip:{client_ip(request)}", max_attempts)]
    if account:
        account_attempts = max(1, (max_attempts + 1) // 2)
        checks.append((f"ratelimit:{scope}:acct:{account.strip().lower()}", account_attempts))

    for key, allowed in checks:
        try:
            count = await redis.incr_with_ttl(key, window)
        except Exception:
            logger.warning("rate_limit_backend_unavailable", extra={"scope": scope})
            return
        if count > allowed:
            raise RateLimitError(
                message="Too many attempts. Try again later.",
                details={"scope": scope, "retry_after_seconds": window},
            )
