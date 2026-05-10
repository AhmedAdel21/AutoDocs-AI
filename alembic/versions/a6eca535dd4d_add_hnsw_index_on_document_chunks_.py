"""add HNSW index on document_chunks.embedding

Revision ID: a6eca535dd4d
Revises: 6b84944bcbbc
Create Date: 2026-05-11 01:58:27.141509

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "a6eca535dd4d"
down_revision: Union[str, Sequence[str], None] = "6b84944bcbbc"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # HNSW with cosine distance operator class.
    # m=16 (default): max connections per layer
    # ef_construction=64 (default): index quality tunable; higher = slower build, better recall
    op.execute(
        """
        CREATE INDEX ix_chunks_embedding_hnsw
        ON document_chunks
        USING hnsw (embedding vector_cosine_ops)
        WITH (m = 16, ef_construction = 64);
    """
    )


def downgrade() -> None:
     op.execute("DROP INDEX IF EXISTS ix_chunks_embedding_hnsw;")
