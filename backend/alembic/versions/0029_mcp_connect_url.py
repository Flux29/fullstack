"""add encrypted connect_url to mcp_connections

Revision ID: 0029_mcp_connect_url
Revises: 0028_user_magic_link_epoch

Some catalog MCP servers place their API token in the connection URL itself
(``?apikey=...``), which made ``mcp_connections.url`` a plaintext credential
store while ``auth_token`` was Fernet-encrypted. The URL is now stored twice:

- ``url`` — credential-stripped form (no query string, fragment, userinfo);
  what the API returns and the UI shows.
- ``connect_url`` — the full URL, Fernet-encrypted like the bearer token;
  the only form used to open a connection.

The backfill runs in Python because Fernet needs ``SECRET_KEY`` — encrypting
in SQL is not possible. Downgrade drops ``connect_url`` after restoring each
row's full URL into ``url``, so no credential is lost on a round-trip (rows
whose ciphertext no longer decrypts keep the stripped URL).
"""

import sqlalchemy as sa

from alembic import op

revision = "0029_mcp_connect_url"
down_revision = "0028_user_magic_link_epoch"
branch_labels = None
depends_on = None


def upgrade() -> None:
    from app.core.config import settings
    from app.core.crypto import encrypt_value
    from app.core.sanitize import strip_url_credentials

    op.add_column("mcp_connections", sa.Column("connect_url", sa.Text(), nullable=True))

    bind = op.get_bind()
    rows = bind.execute(sa.text("SELECT id, url FROM mcp_connections")).fetchall()
    for row in rows:
        bind.execute(
            sa.text(
                "UPDATE mcp_connections SET connect_url = :connect_url, url = :url WHERE id = :id"
            ),
            {
                "connect_url": encrypt_value(row.url, settings.SECRET_KEY),
                "url": strip_url_credentials(row.url),
                "id": row.id,
            },
        )


def downgrade() -> None:
    from app.core.config import settings
    from app.core.crypto import decrypt_value

    bind = op.get_bind()
    rows = bind.execute(
        sa.text("SELECT id, connect_url FROM mcp_connections WHERE connect_url IS NOT NULL")
    ).fetchall()
    for row in rows:
        try:
            full_url = decrypt_value(row.connect_url, settings.SECRET_KEY)
        except Exception:
            continue  # unreadable ciphertext — keep the stripped URL
        bind.execute(
            sa.text("UPDATE mcp_connections SET url = :url WHERE id = :id"),
            {"url": full_url, "id": row.id},
        )

    op.drop_column("mcp_connections", "connect_url")
