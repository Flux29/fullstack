"""User-owned coding workspaces (ADR-006).

A workspace is the sandboxed filesystem a chat turn may name. The row holds the
workspace's *identity* and *policy* — never its contents, and never a host
path: ``root`` is the sandbox session id the backend reattaches to
(``RemoteSandbox(session_id=...)`` / ``DockerSandbox(container_name=...)``),
and files live wherever the sandbox service keeps them.

``repo_url`` is stored with userinfo, query string, and fragment removed (see
:mod:`app.schemas.workspace`); credentials for private repositories are an open
question in ADR-006 and are deliberately not a column here.

``ruleset`` selects the tool surface (``readonly`` registers no write or
execute tool; ``default`` and ``strict`` gate every mutation behind deferred
approval). ``auto_approve`` resolves those approvals without a round-trip for
this workspace only; it is refused together with ``strict``.
"""

from __future__ import annotations

import uuid

from sqlalchemy import Boolean, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class Workspace(Base, TimestampMixin):
    """One user-scoped coding workspace."""

    __tablename__ = "workspaces"
    __table_args__ = (UniqueConstraint("user_id", "name", name="uq_workspaces_user_name"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    # "remote" (sandboxd over HTTP — every Compose stack) or "docker" (the
    # backend drives Docker itself — only when it runs as a host process).
    backend_kind: Mapped[str] = mapped_column(String(16), nullable=False, default="remote")
    # Sandbox session id, generated at creation. Never a host path.
    root: Mapped[str] = mapped_column(String(128), nullable=False)
    repo_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    # "readonly" | "default" | "strict".
    ruleset: Mapped[str] = mapped_column(String(16), nullable=False, default="default")
    auto_approve: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # No ``user`` relationship: ownership is checked with ``user_id`` and the
    # read schema never exposes the user, so an eager join would ship the
    # whole user row on every list, get, patch, and delete. McpConnection's
    # equivalent had to be undone with lazyload() to take a row lock.

    def __repr__(self) -> str:
        return f"<Workspace(name={self.name} kind={self.backend_kind} ruleset={self.ruleset})>"
