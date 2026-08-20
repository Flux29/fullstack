"""OAuth2 authentication routes.

The Google callback never puts JWTs in the redirect URL. It stores a short-lived,
single-use opaque code in Redis and redirects with only that code; the frontend
proxy exchanges it server-to-server at ``/oauth/exchange`` for the token pair and
sets the HttpOnly cookies. URLs (history, access logs, extensions, referrers)
therefore never carry a credential.
"""

import logging
import secrets
from typing import Any
from urllib.parse import urlencode
from uuid import UUID

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

from app.api.deps import Redis, SessionSvc, UserSvc
from app.core.config import settings
from app.core.exceptions import AppException, AuthenticationError
from app.core.oauth import oauth
from app.core.rate_limit import enforce_auth_rate_limit
from app.core.security import create_access_token, create_refresh_token
from app.schemas.token import OAuthCodeExchangeRequest, Token

logger = logging.getLogger(__name__)

router = APIRouter()

LOGIN_CODE_PREFIX = "oauth:login-code:"
LOGIN_CODE_TTL_SECONDS = 60


@router.get("/google/login", response_model=None)
async def google_login(request: Request):
    """Redirect to Google OAuth2 login page."""
    return await oauth.google.authorize_redirect(request, settings.GOOGLE_REDIRECT_URI)


@router.get("/google/callback", response_model=None)
async def google_callback(request: Request, user_service: UserSvc, redis: Redis):
    """Handle Google OAuth2 callback.

    Issues a single-use sign-in code instead of redirecting with tokens.
    """
    frontend = settings.FRONTEND_URL.rstrip("/")
    try:
        token = await oauth.google.authorize_access_token(request)
        user_info = token.get("userinfo")

        if not user_info:
            params = urlencode({"error": "Failed to get user info from Google"})
            return RedirectResponse(url=f"{frontend}/login?{params}")

        user = await user_service.get_or_create_oauth_user(
            provider="google",
            provider_id=user_info.get("sub"),
            email=user_info.get("email"),
            full_name=user_info.get("name"),
        )

        code = secrets.token_urlsafe(32)
        await redis.set(f"{LOGIN_CODE_PREFIX}{code}", str(user.id), ttl=LOGIN_CODE_TTL_SECONDS)

        params = urlencode({"code": code})
        return RedirectResponse(url=f"{frontend}/auth/callback?{params}")

    except Exception:
        logger.exception("google_oauth_callback_failed")
        params = urlencode({"error": "Sign-in failed. Please try again."})
        return RedirectResponse(url=f"{frontend}/login?{params}")


@router.post("/exchange", response_model=Token)
async def exchange_login_code(
    request: Request,
    body: OAuthCodeExchangeRequest,
    user_service: UserSvc,
    session_service: SessionSvc,
    redis: Redis,
) -> Any:
    """Exchange a single-use OAuth sign-in code for an access + refresh token pair.

    Called server-to-server by the frontend proxy, mirroring magic-link verify.
    The Redis GETDEL makes the code single-use even under concurrent attempts.
    """
    await enforce_auth_rate_limit(redis, scope="oauth-exchange", request=request)

    user_id = await redis.getdel(f"{LOGIN_CODE_PREFIX}{body.code}")
    if not user_id:
        raise AuthenticationError(message="Invalid or expired sign-in code")

    try:
        user = await user_service.get_by_id(UUID(user_id))
    except (AppException, ValueError) as exc:
        raise AuthenticationError(message="Invalid or expired sign-in code") from exc
    if not user.is_active:
        raise AuthenticationError(message="User account is disabled")

    access_token = create_access_token(subject=str(user.id))
    refresh_token = create_refresh_token(subject=str(user.id))
    await session_service.create_session(
        user_id=user.id,
        refresh_token=refresh_token,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("User-Agent"),
    )
    return Token(access_token=access_token, refresh_token=refresh_token)
