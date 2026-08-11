"""add users.magic_link_epoch

Revision ID: 0028_user_magic_link_epoch
Revises: 0027_embedding_cache
Create Date: 2026-07-31T17:23:40.171144+00:00

Magic-link sign-in tokens are JWTs, so they cannot be revoked. Each issued
link carries the epoch in force at issue time; redeeming one bumps the epoch,
which invalidates that link and every other link outstanding for the user.
Without this a leaked sign-in link stays usable for its whole TTL, and stays
usable after the legitimate user has already signed in with it.
"""

import sqlalchemy as sa

from alembic import op

revision = "0028_user_magic_link_epoch"
down_revision = "0027_embedding_cache"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("magic_link_epoch", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("users", "magic_link_epoch")
