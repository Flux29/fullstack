"""re-strip mcp display URLs to origin only and clear stale last_error

Revision ID: 0030_mcp_url_origin_only
Revises: 0029_mcp_connect_url

Closes the two gaps 0029 left in the credential split:

- ``strip_url_credentials`` kept the URL path, but catalog servers can
  carry the whole secret there (Zapier personal links,
  ``/api/mcp/s/<secret>/mcp``), so rows backfilled by 0029 may still hold
  a live credential in the plaintext ``url`` column. The stripper now
  reduces to the origin (scheme + host + port); this re-strips every
  stored display URL from the decrypted ``connect_url`` (or from ``url``
  itself on pre-0029 legacy rows).
- ``last_error`` was written unscrubbed before 0029's error scrubber
  landed, and httpx embeds the full request URL in HTTP errors, so a
  probe that failed before that deploy can have persisted a token-bearing
  URL. The stale diagnostics are cleared; the next probe rewrites them
  through the scrubbing ``probe_error_message``.

Downgrade restores nothing: the previous display forms are derivable from
``connect_url`` and the cleared errors were stale diagnostics.
"""

import contextlib

import sqlalchemy as sa

from alembic import op

revision = "0030_mcp_url_origin_only"
down_revision = "0029_mcp_connect_url"
branch_labels = None
depends_on = None


def upgrade() -> None:
    from app.core.config import settings
    from app.core.crypto import decrypt_value
    from app.core.sanitize import strip_url_credentials

    bind = op.get_bind()
    rows = bind.execute(sa.text("SELECT id, url, connect_url FROM mcp_connections")).fetchall()
    for row in rows:
        full_url = row.url
        if row.connect_url is not None:
            # On unreadable ciphertext, strip the display form we have.
            with contextlib.suppress(Exception):
                full_url = decrypt_value(row.connect_url, settings.SECRET_KEY)
        bind.execute(
            sa.text("UPDATE mcp_connections SET url = :url WHERE id = :id"),
            {"url": strip_url_credentials(full_url), "id": row.id},
        )

    bind.execute(
        sa.text("UPDATE mcp_connections SET last_error = NULL WHERE last_error IS NOT NULL")
    )


def downgrade() -> None:
    # Nothing to restore: display URLs remain valid (just origin-only), and
    # the cleared last_error values were stale pre-scrubber diagnostics.
    pass
