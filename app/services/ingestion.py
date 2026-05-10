"""Document ingestion service — chunk + embed + store, all in one transaction.

Idempotency model:
- The 'idempotency key' is the SHA-256 hash of the raw content (the document_hash).
- If a document with the same content_hash already exists, we return that document
  unchanged. No re-ingestion.
- This is content-defined idempotency, not session-defined. Re-uploading the same
  spec from a different client, days apart, returns the same document.
"""

import hashlib
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import Document, DocumentChunk, DocumentStatus
from app.services.chunking import chunk_text
from app.services.embedding import embed_texts
from app.observability import tracer
from app.logging_config import log


def hash_content(text: str) -> str:
    """SHA-256 hex digest of the content, used as the idempotency key."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


async def ingest_document(
    db: AsyncSession,
    *,
    title: str,
    raw_content: str,
    uploaded_by: uuid.UUID | None,
) -> tuple[Document, bool]:
    """Ingest a document: chunk, embed, store.

    Returns: (document, was_newly_created)
    - If the content_hash already exists, returns the existing document and False.
    - Otherwise, creates the document, chunks, and embeddings; returns the new doc and True.

    Atomic: the document, all chunks, and all embeddings are inserted in one transaction.
    If embedding fails, nothing is committed.
    """
    with tracer.start_as_current_span("ingestion.ingest_document") as span:
        content_hash = hash_content(raw_content)
        span.set_attribute("document.content_hash", content_hash)

        # Idempotency check — does this content already exist?
        existing_stmt = select(Document).where(Document.content_hash == content_hash)
        existing = (await db.execute(existing_stmt)).scalar_one_or_none()
        if existing is not None:
            log.info(
                "ingestion.duplicate",
                document_id=str(existing.id),
                content_hash=content_hash,
            )
            span.set_attribute("ingestion.outcome", "duplicate")
            return existing, False

        # Create document record in PROCESSING state
        document = Document(
            title=title,
            content_hash=content_hash,
            raw_content=raw_content,
            status=DocumentStatus.PROCESSING,
            uploaded_by=uploaded_by,
            chunks_count=0,
        )
        db.add(document)
        await db.flush()  # assigns document.id

        # Chunk the content
        chunks = chunk_text(raw_content)
        span.set_attribute("ingestion.chunks_count", len(chunks))

        if not chunks:
            document.status = DocumentStatus.FAILED
            document.error_message = "no chunks produced from content"
            await db.flush()
            return document, True

        # Embed all chunks in batches (handled inside embed_texts)
        chunk_texts = [c.content for c in chunks]
        embeddings = await embed_texts(chunk_texts)

        if len(embeddings) != len(chunks):
            raise RuntimeError(
                f"embedding count mismatch: {len(embeddings)} vs {len(chunks)} chunks"
            )

        # Build chunk records
        for i, (chunk, embedding) in enumerate(zip(chunks, embeddings)):
            chunk_record = DocumentChunk(
                document_id=document.id,
                chunk_index=i,
                content=chunk.content,
                start_char=chunk.start_char,
                end_char=chunk.end_char,
                embedding=embedding,
            )
            db.add(chunk_record)

        document.chunks_count = len(chunks)
        document.status = DocumentStatus.READY

        await db.flush()
        await db.refresh(document)

        log.info(
            "ingestion.complete",
            document_id=str(document.id),
            chunks_count=len(chunks),
        )
        span.set_attribute("ingestion.outcome", "created")
        return document, True
