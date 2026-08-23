"""create workspaces table

Revision ID: 0031_create_workspaces
Revises: 0030_mcp_url_origin_only

User-owned coding workspaces (ADR-006). A row holds a workspace's identity
and policy only: ``root`` is the opaque sandbox session id the backend
reattaches to (never a host path), ``repo_url`` is stored with userinfo,
query and fragment stripped, ``ruleset`` selects the tool surface, and
``auto_approve`` resolves deferred approvals for this workspace without a
browser round-trip.
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

from alembic import op

revision = "0031_create_workspaces"
down_revision = "0030_mcp_url_origin_only"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "workspaces",
        sa.Column(
            "id",
            PG_UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "user_id",
            PG_UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("backend_kind", sa.String(16), nullable=False, server_default="remote"),
        sa.Column("root", sa.String(128), nullable=False),
        sa.Column("repo_url", sa.String(2048), nullable=True),
        sa.Column("ruleset", sa.String(16), nullable=False, server_default="default"),
        sa.Column("auto_approve", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("user_id", "name", name="uq_workspaces_user_name"),
    )
    op.create_index("ix_workspaces_user_id", "workspaces", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_workspaces_user_id", table_name="workspaces")
    op.drop_table("workspaces")
