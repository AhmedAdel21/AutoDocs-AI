import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import String, DateTime, ForeignKey, Integer, Text, func, Index
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from pgvector.sqlalchemy import Vector

from app.db import Base


class DocumentStatus(StrEnum):
    PENDING = "pending"  # uploaded, not yet ingested
    PROCESSING = "processing"  # ingestion running
    READY = "ready"  # all chunks embedded and stored
    FAILED = "failed"  # ingestion failed; details in error_message


# Embedding dimensions for the all-MiniLM-L6-v2 sentence-transformers model.
# We pin this constant so changes are explicit and forced through migration.
EMBEDDING_DIM = 384


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    # Display name — human-readable, can be edited
    title: Mapped[str] = mapped_column(String(512), nullable=False)

    # Idempotency key for ingestion: SHA-256 of the raw content
    # If a client re-uploads the same content, we detect it via content_hash and skip re-ingestion
    content_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        unique=True,  # one document per unique content
        index=True,
    )

    # The raw content. For Day 5 we accept text. PDF/docx parsing comes Day 6+.
    raw_content: Mapped[str] = mapped_column(Text, nullable=False)

    status: Mapped[DocumentStatus] = mapped_column(
        String(32),
        nullable=False,
        default=DocumentStatus.PENDING,
        index=True,  # so we can list pending/failed
    )

    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Who uploaded it
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    chunks_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    chunks: Mapped[list["DocumentChunk"]] = relationship(
        "DocumentChunk",
        back_populates="document",
        cascade="all, delete-orphan",
    )


class DocumentChunk(Base):
    __tablename__ = "document_chunks"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Position within the document (0-indexed)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)

    content: Mapped[str] = mapped_column(Text, nullable=False)

    # Character offsets in the original document — useful for citation rendering
    start_char: Mapped[int] = mapped_column(Integer, nullable=False)
    end_char: Mapped[int] = mapped_column(Integer, nullable=False)

    # The embedding vector. 384 dims because that's the all-MiniLM-L6-v2 output size.
    embedding: Mapped[list[float]] = mapped_column(
        Vector(EMBEDDING_DIM),
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    document: Mapped["Document"] = relationship(
        "Document",
        back_populates="chunks",
    )

    __table_args__ = (
        # We'll add the HNSW vector index in Hour 4 after seeding some data
        Index("ix_chunks_document_chunk_idx", "document_id", "chunk_index"),
    )
