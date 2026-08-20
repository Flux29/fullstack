"""Token schemas."""

from typing import Literal

from pydantic import Field

from app.schemas.base import BaseSchema


class Token(BaseSchema):
    """OAuth2 token response with refresh token."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class TokenPayload(BaseSchema):
    """JWT token payload."""

    sub: str | None = None
    exp: int | None = None
    type: Literal["access", "refresh"] | None = None


class RefreshTokenRequest(BaseSchema):
    """Request body for token refresh."""

    refresh_token: str


class OAuthCodeExchangeRequest(BaseSchema):
    """Request body for exchanging a single-use OAuth sign-in code for tokens."""

    code: str = Field(min_length=16, max_length=128)
