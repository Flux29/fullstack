"""add durable embedding cache and RAG collection metadata

Revision ID: 0027_embedding_cache
Revises: 0026_create_mcp_connections
Create Date: 2026-08-01
"""

from alembic import op

revision = "0027_embedding_cache"
down_revision = "0026_create_mcp_connections"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute(
        """
        CREATE TABLE embedding_cache (
            cache_key char(64) PRIMARY KEY,
            model_id varchar(255) NOT NULL,
            model_version varchar(255) NOT NULL,
            model_revision varchar(255) NOT NULL DEFAULT '',
            dimensions integer NOT NULL CHECK (dimensions = 1024),
            input_kind varchar(16) NOT NULL CHECK (input_kind IN ('query', 'document')),
            instruction_hash char(64) NOT NULL,
            normalized_input_hash char(64) NOT NULL,
            embedding vector(1024) NOT NULL,
            hit_count bigint NOT NULL DEFAULT 0,
            created_at timestamptz NOT NULL DEFAULT now(),
            last_accessed_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE TABLE rag_collection_metadata (
            collection_name varchar(63) PRIMARY KEY,
            embedding_fingerprint char(64) NOT NULL,
            model_id varchar(255) NOT NULL,
            model_version varchar(255) NOT NULL,
            model_revision varchar(255) NOT NULL DEFAULT '',
            dimensions integer NOT NULL CHECK (dimensions = 1024),
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS rag_collection_metadata")
    op.execute("DROP TABLE IF EXISTS embedding_cache")
