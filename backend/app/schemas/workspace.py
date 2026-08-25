"""Schemas for user-owned coding workspaces (ADR-006)."""

from __future__ import annotations

from typing import Literal
from urllib.parse import urlparse
from uuid import UUID

from pydantic import Field, field_validator

from app.core.sanitize import WEBHOOK_ALLOWED_SCHEMES, strip_url_credentials
from app.schemas.base import BaseSchema, TimestampSchema

# Slug-style names, same rule as MCP connections: the name is shown in the
# chat controls and used as the sandbox's human label.
NAME_PATTERN = r"^[a-z0-9][a-z0-9-]{0,31}$"

BackendKind = Literal["remote", "docker"]
Ruleset = Literal["readonly", "default", "strict"]

STRICT_AUTO_APPROVE_MESSAGE = "auto_approve cannot be combined with the strict ruleset"

# The webhook allowlist plus git://, which by construction carries no
# credential material for :func:`strip_url_credentials` to miss — the protocol
# has no userinfo or auth exchange at all. It exists for the dev-stack local
# daemon (ADR-006 rule 7 / open question 1 note); ssh:// stays out because it
# does carry identity.
REPO_URL_ALLOWED_SCHEMES = WEBHOOK_ALLOWED_SCHEMES | {"git"}


def normalize_repo_url(url: str) -> str:
    """Reduce a repository URL to scheme + host + path.

    Userinfo (``https://user:token@host/...``), the query string, and the
    fragment are dropped: a clone token must never reach the row, and ADR-006
    leaves private-repository credentials as an open question. The scheme
    allowlist is the webhook one plus ``git://``; widening it further
    (``ssh://`` is one of the mechanisms ADR-006 open question 1 may choose)
    is an edit to ``REPO_URL_ALLOWED_SCHEMES``, not to this reasoning.

    SSRF validation is deliberately *not* done here — it resolves DNS, so it
    belongs at the moment the sandbox is told to clone, not at write time.
    """
    url = url.strip()
    parsed = urlparse(url)
    if parsed.scheme not in REPO_URL_ALLOWED_SCHEMES:
        raise ValueError("repo_url must start with http://, https://, or git://")
    if not parsed.hostname:
        raise ValueError("repo_url must include a host")
    return strip_url_credentials(url, keep_path=True)


class WorkspaceCreate(BaseSchema):
    name: str = Field(..., min_length=1, max_length=32, pattern=NAME_PATTERN)
    backend_kind: BackendKind = "remote"
    repo_url: str | None = Field(default=None, min_length=1, max_length=2048)
    ruleset: Ruleset = "default"
    auto_approve: bool = False

    @field_validator("repo_url")
    @classmethod
    def _normalize_repo_url(cls, value: str | None) -> str | None:
        return normalize_repo_url(value) if value else None


class WorkspaceUpdate(BaseSchema):
    name: str | None = Field(default=None, min_length=1, max_length=32, pattern=NAME_PATTERN)
    backend_kind: BackendKind | None = None
    # Explicit ``null`` detaches the repository; an absent key leaves it alone
    # (the service dumps with ``exclude_unset``, which tells the two apart).
    repo_url: str | None = Field(default=None, min_length=1, max_length=2048)
    ruleset: Ruleset | None = None
    auto_approve: bool | None = None

    @field_validator("repo_url")
    @classmethod
    def _normalize_repo_url(cls, value: str | None) -> str | None:
        return normalize_repo_url(value) if value else None


class WorkspaceRead(TimestampSchema, BaseSchema):
    id: UUID
    name: str
    backend_kind: BackendKind
    # Sandbox session id — opaque, read-only.
    root: str
    repo_url: str | None
    ruleset: Ruleset
    auto_approve: bool


class WorkspaceList(BaseSchema):
    items: list[WorkspaceRead]
    total: int
